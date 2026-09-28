"""PydanticAI orchestration for the P0 market tools."""

import asyncio
import re
from collections.abc import AsyncIterator
from contextlib import suppress
from dataclasses import replace
from typing import Literal

from starlette.concurrency import run_in_threadpool
from anyio import CancelScope

from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, TextPart, ToolCallPart, UserPromptPart
from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.usage import UsageLimits

from app.agent import tools
from app.agent.trace import record_tool
from app.agent.tools import AgentDependencies
from app.core.config import Settings
from app.providers.exceptions import DataSourceError, ProviderTimeoutError
from app.services.chat import ChatService, ChatTurn
from app.services.trace import TraceService


SYSTEM_INSTRUCTIONS = """你是 StockPilot 的 A 股行情助手。回答使用中文，简洁、准确。
涉及当前或历史行情、自选股、指数时，必须先调用对应 Tool，不能依靠记忆补数值。
Tool 返回的字段才是数据事实；明确区分数据事实与分析。旧缓存必须说明非最新数据。
Tool 优先读取最近成功缓存；cached_at 是 UTC 保存时间，cache_age_seconds 是缓存年龄。
回答行情时说明数据截至时间（转换为北京时间）；stale=true 时明确为旧缓存，不能称为当前实时行情。
Tool 失败、股票不存在或没有数据时明确说明，绝不猜测价格、涨跌幅、原因或走势。
目前没有资金流和新闻数据 Tool。用户问价格波动原因时不要凭价格推断原因，
说明需要更多数据验证。不要给出交易指令、收益承诺或涨跌预测。
回答应注明行情信息仅供参考，不构成投资建议。"""
MARKET_FACT_TERMS = (
    "今天", "现在", "最新", "最近", "行情", "股票", "股价", "涨", "跌",
    "走势", "K线", "K 线", "指数", "自选股", "成交", "比较",
)
NO_MARKET_DATA = "本次未能从行情 Tool 获取数据，暂无法回答行情事实。请重试或提供六位股票代码。"


def _wants_market_facts(message: str) -> bool:
    return any(term in message for term in MARKET_FACT_TERMS) or bool(
        re.search(r"(?<!\d)[03468]\d{5}(?!\d)", message)
    )


class ModelNotConfiguredError(Exception):
    pass


class AgentExecutionError(Exception):
    def __init__(self, session_id: str, status_code: int = 502) -> None:
        self.session_id = session_id
        self.status_code = status_code
        super().__init__("Agent execution failed")


def _visible_history(turns: list[ChatTurn]) -> list[ModelMessage]:
    history: list[ModelMessage] = []
    for turn in turns[-20:]:
        if turn.role == "user":
            history.append(ModelRequest(parts=[UserPromptPart(content=turn.content)]))
        elif turn.role == "assistant":
            history.append(ModelResponse(parts=[TextPart(content=turn.content)]))
    return history


def build_agent(model: Model) -> Agent[AgentDependencies, str]:
    agent = Agent(model, deps_type=AgentDependencies, instructions=SYSTEM_INSTRUCTIONS)

    @agent.tool
    async def get_stock_quote(ctx: RunContext[AgentDependencies], symbol: str) -> dict:
        """Read latest cached quote by A-share symbol, fetching only on cache miss."""
        return await record_tool(
            ctx.deps.trace_steps, "get_stock_quote", {"symbol": symbol},
            lambda: tools.get_stock_quote(ctx.deps, symbol),
        )

    @agent.tool
    async def get_stock_kline(
        ctx: RunContext[AgentDependencies],
        symbol: str,
        period: Literal["daily", "weekly"] = "daily",
        limit: int = 5,
    ) -> dict:
        """Read cached daily/weekly OHLC candles, fetching only on cache miss."""
        return await record_tool(
            ctx.deps.trace_steps, "get_stock_kline",
            {"symbol": symbol, "period": period, "limit": limit},
            lambda: tools.get_stock_kline(ctx.deps, symbol, period, limit),
        )

    @agent.tool
    async def get_market_indices(ctx: RunContext[AgentDependencies]) -> dict:
        """Read the latest cached major A-share indices, fetching only on cache miss."""
        return await record_tool(
            ctx.deps.trace_steps, "get_market_indices", {},
            lambda: tools.get_market_indices(ctx.deps),
        )

    @agent.tool
    async def get_watchlist(ctx: RunContext[AgentDependencies]) -> dict:
        """Read watchlist and latest cached quotes, fetching a batch only on cache miss."""
        return await record_tool(
            ctx.deps.trace_steps, "get_watchlist", {},
            lambda: tools.get_watchlist(ctx.deps),
        )

    return agent


class StockAgentService:
    def __init__(
        self,
        settings: Settings,
        dependencies: AgentDependencies,
        chats: ChatService,
        traces: TraceService,
        *,
        model: Model | None = None,
    ) -> None:
        self._dependencies = dependencies
        self._chats = chats
        self._traces = traces
        if model is not None:
            self._agent = build_agent(model)
        elif settings.model_name and settings.model_api_key:
            configured_model = OpenAIChatModel(
                settings.model_name,
                provider=OpenAIProvider(
                    base_url=settings.model_base_url or None,
                    api_key=settings.model_api_key,
                ),
            )
            self._agent = build_agent(configured_model)
        else:
            self._agent = None

    @property
    def configured(self) -> bool:
        return self._agent is not None

    async def chat(self, message: str, session_id: str | None = None) -> tuple[str, str]:
        if self._agent is None:
            raise ModelNotConfiguredError
        turns = await run_in_threadpool(self._chats.messages, session_id) if session_id else []
        active_session_id = await run_in_threadpool(self._chats.ensure_session, session_id, message)
        run_dependencies = replace(self._dependencies, trace_steps=[])
        try:
            result = await self._agent.run(
                message,
                deps=run_dependencies,
                message_history=_visible_history(turns),
                usage_limits=UsageLimits(request_limit=8, tool_calls_limit=12),
            )
        except ProviderTimeoutError as error:
            raise AgentExecutionError(active_session_id, 504) from error
        except DataSourceError as error:
            raise AgentExecutionError(active_session_id, 503) from error
        except Exception as error:
            raise AgentExecutionError(active_session_id) from error
        finally:
            await run_in_threadpool(
                self._traces.save_steps, active_session_id, run_dependencies.trace_steps
            )
        answer = result.output.strip()
        if not answer:
            raise AgentExecutionError(active_session_id)
        wants_market_facts = _wants_market_facts(message)
        used_tool = any(
            isinstance(part, ToolCallPart)
            for model_message in result.new_messages()
            if isinstance(model_message, ModelResponse)
            for part in model_message.parts
        )
        if wants_market_facts and not used_tool:
            answer = NO_MARKET_DATA
        await run_in_threadpool(self._chats.save_exchange, active_session_id, message, answer)
        return active_session_id, answer

    async def prepare_stream(self, message: str, session_id: str | None) -> tuple[str, list[ChatTurn]]:
        if self._agent is None:
            raise ModelNotConfiguredError
        turns = await run_in_threadpool(self._chats.messages, session_id) if session_id else []
        active_id = await run_in_threadpool(self._chats.ensure_session, session_id, message)
        return active_id, turns

    async def stream_chat(
        self, message: str, session_id: str, turns: list[ChatTurn]
    ) -> AsyncIterator[tuple[str, dict]]:
        # Keep the model context and its cancellation scopes inside one producer task.
        # The HTTP consumer can then close without suspending those scopes at a yield.
        queue: asyncio.Queue[tuple[str, dict] | AgentExecutionError] = asyncio.Queue(maxsize=32)
        worker = asyncio.create_task(self._produce_stream(message, session_id, turns, queue))
        try:
            while True:
                event = await queue.get()
                if isinstance(event, AgentExecutionError):
                    raise event
                yield event
                if event[0] == "done":
                    break
        finally:
            with CancelScope(shield=True):
                if not worker.done():
                    worker.cancel()
                with suppress(asyncio.CancelledError):
                    await worker

    async def _produce_stream(
        self, message: str, session_id: str, turns: list[ChatTurn],
        queue: asyncio.Queue[tuple[str, dict] | AgentExecutionError],
    ) -> None:
        try:
            await self._run_stream(message, session_id, turns, queue)
        except AgentExecutionError as error:
            await queue.put(error)
        except Exception:
            await queue.put(AgentExecutionError(session_id))

    async def _run_stream(
        self, message: str, session_id: str, turns: list[ChatTurn],
        queue: asyncio.Queue[tuple[str, dict] | AgentExecutionError],
    ) -> None:
        assert self._agent is not None
        dependencies = replace(self._dependencies, trace_steps=[])
        try:
            async with self._agent.run_stream(
                message, deps=dependencies, message_history=_visible_history(turns),
                usage_limits=UsageLimits(request_limit=8, tool_calls_limit=12),
            ) as result:
                # run_stream executes preceding Tools before exposing final text.
                # Quarantine an unsupported market answer before sending any tokens.
                allowed = not _wants_market_facts(message) or any(
                    step.status == "success" for step in dependencies.trace_steps
                )
                async for delta in result.stream_text(delta=True):
                    if allowed and delta:
                        await queue.put(("delta", {"text": delta}))
                answer = (await result.get_output()).strip() if allowed else NO_MARKET_DATA
                if not allowed:
                    await queue.put(("delta", {"text": answer}))
                if not answer:
                    raise AgentExecutionError(session_id)
            await run_in_threadpool(self._chats.save_exchange, session_id, message, answer)
        except ProviderTimeoutError as error:
            raise AgentExecutionError(session_id, 504) from error
        except DataSourceError as error:
            raise AgentExecutionError(session_id, 503) from error
        except AgentExecutionError:
            raise
        except Exception as error:
            raise AgentExecutionError(session_id) from error
        finally:
            with CancelScope(shield=True):
                await run_in_threadpool(self._traces.save_steps, session_id, dependencies.trace_steps)
        await queue.put(("done", {"session_id": session_id, "answer": answer}))

    async def messages(self, session_id: str) -> list[ChatTurn]:
        return await run_in_threadpool(self._chats.messages, session_id)

"""Collect only visible Tool execution facts for the Trace service."""

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, TypeVar

from app.providers.exceptions import DataSourceError, ProviderTimeoutError

T = TypeVar("T")


@dataclass
class ToolStep:
    step_index: int
    tool_name: str
    tool_input: dict[str, Any]
    tool_output_summary: str = ""
    status: str = "error"
    latency_ms: int = 0


def _summary(tool_name: str, result: dict) -> str:
    stale = "；旧缓存" if result.get("stale") else ""
    state = result.get("collection_state")
    stale += {"warming": "；后台预热中", "unavailable": "；暂无可用缓存", "partial": "；部分数据缺失"}.get(state, "")
    if tool_name == "get_stock_quote":
        return ("返回 1 条报价" if result.get("found") else "暂无缓存报价" if state else "未找到报价") + stale
    if tool_name == "get_stock_kline":
        return f"返回 {len(result['klines'])} 条 K 线" + stale
    if tool_name == "get_stock_money_flow":
        items = result.get("items", [])
        through = f"；统计截至 {items[-1]['date']}" if items else ""
        return f"返回 {len(items)} 个交易日资金流（元、%）" + through + stale
    if tool_name == "get_market_indices":
        return f"返回 {len(result['indices'])} 条指数" + stale
    if tool_name == "get_market_breadth":
        return "返回沪深京市场涨跌统计" + ("；部分数据缺失/旧值" if result.get("partial") else "") + stale
    if tool_name == "search_stock_documents":
        items = result.get("evidence", [])
        return f"检索返回 {len(items)} 个证据片段；{result.get('reason')}" + ("；" + ", ".join(i["evidence_id"] for i in items))
    if tool_name == "get_watchlist":
        return f"返回 {len(result['entries'])} 只自选股、{len(result['quotes'])} 条报价" + stale
    if tool_name in ("get_stock_news", "get_market_news"):
        return f"返回 {len(result['items'])} 条新闻标题/来源片段（非全文）" + stale
    if tool_name == "get_stock_announcements":
        document = result.get("document")
        detail = f"；正文 {document['text_status']}" if document else ""
        if document and document.get("tool_text_truncated"):
            detail += "（Tool片段截断）"
        return f"返回 {len(result['items'])} 条公告" + detail + stale
    return "Tool 已执行"


def _error_summary(error: BaseException) -> str:
    if isinstance(error, ProviderTimeoutError):
        return "行情数据源超时"
    if isinstance(error, DataSourceError):
        return "行情数据源暂不可用"
    if isinstance(error, ValueError):
        return "Tool 参数无效"
    return "Tool 执行失败"


async def record_tool(
    steps: list[ToolStep],
    name: str,
    inputs: dict[str, Any],
    call: Callable[[], Awaitable[T]],
) -> T:
    safe_inputs = {
        key: value[:64] if isinstance(value, str) else value
        for key, value in inputs.items()
    }
    step = ToolStep(step_index=len(steps) + 1, tool_name=name, tool_input=safe_inputs)
    steps.append(step)
    started = time.perf_counter()
    try:
        result = await call()
        step.status = "success"
        step.tool_output_summary = _summary(name, result) if isinstance(result, dict) else "Tool 已执行"
        return result
    except BaseException as error:
        step.status = "error"
        step.tool_output_summary = _error_summary(error)
        raise
    finally:
        step.latency_ms = max(0, round((time.perf_counter() - started) * 1000))

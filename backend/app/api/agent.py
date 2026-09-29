"""Agent chat and Tool execution trace endpoints."""

import json
from contextlib import aclosing
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, StringConstraints

from app.agent.service import AgentExecutionError, ModelNotConfiguredError, StockAgentService
from app.api.dependencies import get_agent_service, get_trace_service
from app.api.schemas import DataResponse
from app.services.chat import ChatNotFoundError
from app.services.trace import TraceEntry, TraceService

router = APIRouter(prefix="/api/agent", tags=["agent"])
AgentService = Annotated[StockAgentService, Depends(get_agent_service)]
Traces = Annotated[TraceService, Depends(get_trace_service)]
Message = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


class ChatRequest(BaseModel):
    message: Message
    session_id: UUID | None = None


class ChatResponse(BaseModel):
    session_id: UUID
    answer: str


class ChatMessageResponse(BaseModel):
    role: str
    content: str


class ChatSessionResponse(BaseModel):
    session_id: UUID
    messages: list[ChatMessageResponse]


@router.get("/status")
def agent_status(agent: AgentService) -> dict[str, bool]:
    return {"configured": agent.configured}


@router.post("/chat", response_model=ChatResponse)
async def agent_chat(body: ChatRequest, agent: AgentService) -> ChatResponse:
    try:
        session_id, answer = await agent.chat(body.message, str(body.session_id) if body.session_id else None)
    except ModelNotConfiguredError:
        raise HTTPException(status_code=503, detail="Agent model is not configured") from None
    except ChatNotFoundError:
        raise HTTPException(status_code=404, detail="Chat session not found") from None
    except AgentExecutionError as error:
        detail = {
            429: "调用预算已用尽，请缩小股票数量或问题范围后重试。",
            503: "Market data temporarily unavailable",
            504: "Market data provider timed out",
        }.get(error.status_code, "Agent model request failed")
        raise HTTPException(
            status_code=error.status_code,
            detail=detail,
            headers={"X-Agent-Session-ID": error.session_id},
        ) from None
    return ChatResponse(session_id=UUID(session_id), answer=answer)


@router.get("/sessions/{session_id}", response_model=ChatSessionResponse)
async def get_chat_session(session_id: UUID, agent: AgentService) -> ChatSessionResponse:
    try:
        messages = await agent.messages(str(session_id))
    except ChatNotFoundError:
        raise HTTPException(status_code=404, detail="Chat session not found") from None
    return ChatSessionResponse(
        session_id=session_id,
        messages=[ChatMessageResponse(role=item.role, content=item.content) for item in messages],
    )


@router.post("/chat/stream")
async def stream_agent_chat(body: ChatRequest, agent: AgentService) -> StreamingResponse:
    try:
        session_id, turns = await agent.prepare_stream(
            body.message, str(body.session_id) if body.session_id else None
        )
    except ModelNotConfiguredError:
        raise HTTPException(status_code=503, detail="Agent model is not configured") from None
    except ChatNotFoundError:
        raise HTTPException(status_code=404, detail="Chat session not found") from None

    def event(name: str, data: dict) -> str:
        return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

    async def generate():
        yield event("session", {"session_id": session_id})
        try:
            async with aclosing(agent.stream_chat(body.message, session_id, turns)) as output:
                async for name, data in output:
                    yield event(name, data)
        except AgentExecutionError as error:
            detail = {
                429: "调用预算已用尽，请缩小股票数量或问题范围后重试。",
                503: "Market data temporarily unavailable", 504: "Market data provider timed out",
            }.get(error.status_code, "Agent model request failed")
            yield event("error", {"status": error.status_code, "detail": detail, "session_id": session_id})

    return StreamingResponse(
        generate(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/sessions/{session_id}/evidence")
async def session_evidence(session_id: UUID, agent: AgentService):
    try:
        return {"data": await agent.evidence(str(session_id))}
    except ChatNotFoundError:
        raise HTTPException(status_code=404,detail="Chat session not found") from None


@router.get("/traces/recent", response_model=DataResponse[list[TraceEntry]])
async def recent_traces(
    traces: Traces,
    limit: Annotated[int, Query(ge=1, le=20)] = 5,
) -> DataResponse[list[TraceEntry]]:
    return DataResponse(data=await run_in_threadpool(traces.recent, limit))


@router.get("/traces/{session_id}", response_model=DataResponse[list[TraceEntry]])
async def session_traces(session_id: UUID, traces: Traces) -> DataResponse[list[TraceEntry]]:
    try:
        return DataResponse(data=await run_in_threadpool(traces.for_session, str(session_id)))
    except ChatNotFoundError:
        raise HTTPException(status_code=404, detail="Chat session not found") from None

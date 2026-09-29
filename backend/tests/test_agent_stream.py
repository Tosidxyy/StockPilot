"""SSE output preserves the market-data guard and completed exchanges."""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from pydantic_ai.messages import ToolReturnPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from app.main import create_app
from app.providers.exceptions import DataSourceError, ProviderTimeoutError
from test_api import FakeProvider


async def stream_indices(messages, _info):
    if any(isinstance(part, ToolReturnPart) for part in messages[-1].parts):
        for chunk in ["**指数**", "\n\n- 已读取行情", "，仅供参考。"]:
            yield chunk
            await asyncio.sleep(0.12)
    else:
        yield {0: DeltaToolCall(name="get_market_indices", json_args="{}")}


def events(response):
    return [(lines[0][7:], json.loads(lines[1][6:]))
            for frame in response.text.strip().split("\n\n")
            if (lines := frame.splitlines())]


def test_stream_deltas_complete_exchange_and_trace(tmp_path):
    with TestClient(create_app(
        provider=FakeProvider(), database_url=f"sqlite:///{(tmp_path / 'stream.db').as_posix()}",
        agent_model=FunctionModel(stream_function=stream_indices),
    )) as client:
        response = client.post("/api/agent/chat/stream", json={"message": "今天指数怎么样？"})
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        emitted = events(response)
        assert emitted[0][0] == "session" and emitted[-1][0] == "done"
        progress = [data["text"] for name, data in emitted if name == "progress"]
        assert "正在读取市场指数…" in progress and "市场指数已读取" in progress
        assert next(i for i, (name, _) in enumerate(emitted) if name == "progress") < next(i for i, (name, _) in enumerate(emitted) if name == "delta")
        chunks = [data["text"] for name, data in emitted if name == "delta"]
        assert len(chunks) >= 2
        answer = "".join(chunks)
        assert answer == emitted[-1][1]["answer"]
        session_id = emitted[0][1]["session_id"]
        stored = client.get(f"/api/agent/sessions/{session_id}").json()["messages"]
        assert stored == [{"role": "user", "content": "今天指数怎么样？"}, {"role": "assistant", "content": answer}]
        assert client.get(f"/api/agent/traces/{session_id}").json()["data"][0]["status"] == "success"
        continued = client.post("/api/agent/chat/stream", json={"message": "继续", "session_id": session_id})
        assert events(continued)[-1][0] == "done"
        assert len(client.get(f"/api/agent/sessions/{session_id}").json()["messages"]) == 4
        assert client.post("/api/agent/chat/stream", json={"message": " "}).status_code == 422
        assert client.post("/api/agent/chat/stream", json={"message": "继续", "session_id": "00000000-0000-0000-0000-000000000000"}).status_code == 404


def test_stream_quarantines_market_answer_without_tool(tmp_path):
    async def no_tool(_messages, _info):
        yield "当前股价为 999.99，涨了 20%。"

    with TestClient(create_app(
        provider=FakeProvider(), database_url=f"sqlite:///{(tmp_path / 'guard.db').as_posix()}",
        agent_model=FunctionModel(stream_function=no_tool),
    )) as client:
        response = client.post("/api/agent/chat/stream", json={"message": "300750 现在行情？"})
        assert "999.99" not in response.text
        assert "20%" not in response.text
        assert "未能从行情 Tool" in response.text
        assert events(response)[-1][0] == "done"


@pytest.mark.parametrize("failure,status", [(DataSourceError("private"), 503), (ProviderTimeoutError("private"), 504)])
def test_stream_failure_records_trace_and_never_emits_answer(tmp_path, failure, status):
    provider = FakeProvider()
    provider.error = failure
    with TestClient(create_app(
        provider=provider, database_url=f"sqlite:///{(tmp_path / 'failure.db').as_posix()}",
        agent_model=FunctionModel(stream_function=stream_indices),
    )) as client:
        response = client.post("/api/agent/chat/stream", json={"message": "今天指数怎么样？"})
        emitted = events(response)
        assert [name for name, _ in emitted if name != "progress"] == ["session", "error"]
        assert emitted[-1][1]["status"] == status
        assert "private" not in response.text
        session_id = emitted[0][1]["session_id"]
        assert client.get(f"/api/agent/sessions/{session_id}").json()["messages"] == []
        assert client.get(f"/api/agent/traces/{session_id}").json()["data"][0]["status"] == "error"


def test_interrupted_stream_does_not_save_partial_exchange(tmp_path):
    with TestClient(create_app(
        provider=FakeProvider(), database_url=f"sqlite:///{(tmp_path / 'cancel.db').as_posix()}",
        agent_model=FunctionModel(stream_function=stream_indices),
    )) as client:
        async def stop():
            agent = client.app.state.agent_service
            session_id, turns = await agent.prepare_stream("今天指数怎么样？", None)
            generator = agent.stream_chat("今天指数怎么样？", session_id, turns)
            name, _ = await anext(generator)
            while name == "progress":
                name, _ = await anext(generator)
            assert name == "delta"
            await generator.aclose()
            return session_id

        session_id = client.portal.call(stop)
        assert client.get(f"/api/agent/sessions/{session_id}").json()["messages"] == []
        assert client.get(f"/api/agent/traces/{session_id}").json()["data"][0]["status"] == "success"

"""Trace persists Tool facts on success and failure without model reasoning."""

from fastapi.testclient import TestClient
from pydantic_ai import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.messages import ToolReturnPart
from pydantic_ai.models.function import FunctionModel

from app.main import create_app
from app.providers.exceptions import DataSourceError
from test_api import FakeProvider


def _indices_then_answer(messages, _info):
    if any(isinstance(part, ToolReturnPart) for part in messages[-1].parts):
        return ModelResponse(parts=[TextPart("已读取指数")])
    return ModelResponse(parts=[ToolCallPart("get_market_indices", {})])


def test_successful_traces_are_queryable_and_monotonic(tmp_path) -> None:
    url = f"sqlite:///{(tmp_path / 'trace.db').as_posix()}"
    with TestClient(
        create_app(provider=FakeProvider(), database_url=url, agent_model=FunctionModel(_indices_then_answer))
    ) as client:
        first = client.post("/api/agent/chat", json={"message": "上证指数怎么样？"})
        assert first.status_code == 200
        session_id = first.json()["session_id"]
        second = client.post(
            "/api/agent/chat", json={"message": "继续看指数", "session_id": session_id}
        )
        assert second.status_code == 200
        traces = client.get(f"/api/agent/traces/{session_id}")
        assert traces.status_code == 200
        steps = traces.json()["data"]
        assert [step["step_index"] for step in steps] == [1, 2]
        assert all(step["tool_name"] == "get_market_indices" for step in steps)
        assert all(step["tool_input"] == {} for step in steps)
        assert all(step["status"] == "success" for step in steps)
        assert all(step["tool_output_summary"].startswith("返回 1 条指数") for step in steps)
        assert all(isinstance(step["latency_ms"], int) and step["latency_ms"] >= 0 for step in steps)
        assert all(step["created_at"] for step in steps)
        recent = client.get("/api/agent/traces/recent", params={"limit": 1})
        assert recent.status_code == 200
        assert len(recent.json()["data"]) == 1
        assert recent.json()["data"][0]["step_index"] == 2
        assert client.get("/api/agent/traces/recent", params={"limit": 0}).status_code == 422
        assert client.get("/api/agent/traces/00000000-0000-0000-0000-000000000000").status_code == 404


def test_failed_tool_trace_is_saved_without_upstream_detail(tmp_path) -> None:
    provider = FakeProvider()
    provider.error = DataSourceError("private upstream response")
    url = f"sqlite:///{(tmp_path / 'failed_trace.db').as_posix()}"
    with TestClient(
        create_app(provider=provider, database_url=url, agent_model=FunctionModel(_indices_then_answer))
    ) as client:
        response = client.post(
            "/api/agent/chat",
            json={"message": "上证指数怎么样？"},
            headers={"Origin": "http://localhost:3000"},
        )
        assert response.status_code == 503
        session_id = response.headers["x-agent-session-id"]
        traces = client.get(f"/api/agent/traces/{session_id}")
        assert traces.status_code == 200
        step = traces.json()["data"][0]
        assert step["status"] == "error"
        assert step["tool_output_summary"] == "行情数据源暂不可用"
        assert "private upstream response" not in traces.text
        assert client.get(f"/api/agent/sessions/{session_id}").json()["messages"] == []
        assert "x-agent-session-id" in response.headers["access-control-expose-headers"].lower()

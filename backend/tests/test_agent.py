"""Agent tools, configuration, and the HTTP conversation boundary."""

import asyncio

import pytest
from fastapi.testclient import TestClient
from pydantic_ai import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.messages import ToolReturnPart
from pydantic_ai.models.function import FunctionModel

from app.agent.tools import AgentDependencies, get_market_indices, get_stock_kline, get_stock_quote, get_watchlist
from app.main import create_app
from app.database.session import create_database_engine, create_session_factory, init_db
from app.services.market import MarketService
from app.services.stock import StockService
from app.services.watchlist import WatchlistService
from app.providers.exceptions import DataSourceError
from test_api import FakeProvider


def test_tools_use_services_and_batch_watchlist(tmp_path) -> None:
    provider = FakeProvider()
    engine = create_database_engine(f"sqlite:///{(tmp_path / 'tools.db').as_posix()}")
    init_db(engine)
    watchlist = WatchlistService(create_session_factory(engine))
    watchlist.add("600519")
    watchlist.add("000001")
    deps = AgentDependencies(StockService(provider), MarketService(provider), watchlist)

    quote = asyncio.run(get_stock_quote(deps, "600519"))
    assert quote["quote"]["price"] == 12.3
    assert quote["stale"] is False
    kline = asyncio.run(get_stock_kline(deps, "600519", "weekly", 5))
    assert kline["klines"][0]["date"] == "2026-09-25"
    assert provider.periods == ["weekly"]
    indices = asyncio.run(get_market_indices(deps))
    assert indices["indices"][0]["symbol"] == "000001"
    watch = asyncio.run(get_watchlist(deps))
    assert [item["symbol"] for item in watch["quotes"]] == ["600519", "000001"]
    assert provider.quote_batches[-1] == ("600519", "000001")
    with pytest.raises(ValueError):
        asyncio.run(get_stock_quote(deps, "bad"))
    with pytest.raises(ValueError):
        asyncio.run(get_stock_kline(deps, "600519", "daily", 0))
    engine.dispose()


def test_agent_chat_saves_and_restores_session(tmp_path) -> None:
    def model_function(messages, _info):
        if any(isinstance(part, ToolReturnPart) for part in messages[-1].parts):
            return ModelResponse(parts=[TextPart("测试回答")])
        return ModelResponse(parts=[ToolCallPart("get_market_indices", {})])

    url = f"sqlite:///{(tmp_path / 'chat.db').as_posix()}"
    with TestClient(
        create_app(provider=FakeProvider(), database_url=url, agent_model=FunctionModel(model_function))
    ) as client:
        assert client.get("/api/agent/status").json() == {"configured": True}
        first = client.post("/api/agent/chat", json={"message": "上证指数怎么样？"})
        assert first.status_code == 200, first.text
        session_id = first.json()["session_id"]
        assert first.json()["answer"] == "测试回答"
        second = client.post(
            "/api/agent/chat", json={"session_id": session_id, "message": "继续"}
        )
        assert second.status_code == 200, second.text
        assert second.json()["session_id"] == session_id
        history = client.get(f"/api/agent/sessions/{session_id}")
        assert history.status_code == 200
        assert [item["role"] for item in history.json()["messages"]] == [
            "user", "assistant", "user", "assistant"
        ]
        assert client.post("/api/agent/chat", json={"message": "  "}).status_code == 422
        assert client.post(
            "/api/agent/chat",
            json={"session_id": "00000000-0000-0000-0000-000000000000", "message": "继续"},
        ).status_code == 404


def test_agent_without_model_reports_configuration_error(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MODEL_NAME", "")
    monkeypatch.setenv("MODEL_API_KEY", "")
    from app.core.config import get_settings
    get_settings.cache_clear()
    url = f"sqlite:///{(tmp_path / 'unconfigured.db').as_posix()}"
    try:
        with TestClient(create_app(provider=FakeProvider(), database_url=url)) as client:
            assert client.get("/api/agent/status").json() == {"configured": False}
            response = client.post("/api/agent/chat", json={"message": "今天行情？"})
            assert response.status_code == 503
            assert response.json()["detail"] == "Agent model is not configured"
            assert client.post("/api/agent/chat/stream", json={"message": "今天行情？"}).status_code == 503
    finally:
        get_settings.cache_clear()


def test_failed_market_tool_does_not_save_fabricated_answer(tmp_path) -> None:
    def request_indices(_messages, _info):
        return ModelResponse(parts=[ToolCallPart("get_market_indices", {})])

    provider = FakeProvider()
    provider.error = DataSourceError("upstream detail")
    url = f"sqlite:///{(tmp_path / 'failed.db').as_posix()}"
    with TestClient(
        create_app(provider=provider, database_url=url, agent_model=FunctionModel(request_indices))
    ) as client:
        response = client.post("/api/agent/chat", json={"message": "上证现在多少？"})
        assert response.status_code == 503
        assert "upstream detail" not in response.text


def test_market_answer_requires_a_tool_call(tmp_path) -> None:
    def unsupported_answer(_messages, _info):
        return ModelResponse(parts=[TextPart("上证现在涨了 2%。")])

    url = f"sqlite:///{(tmp_path / 'guard.db').as_posix()}"
    with TestClient(
        create_app(provider=FakeProvider(), database_url=url, agent_model=FunctionModel(unsupported_answer))
    ) as client:
        response = client.post("/api/agent/chat", json={"message": "上证现在涨了多少？"})
        assert response.status_code == 200
        assert "2%" not in response.json()["answer"]
        assert "未能从行情 Tool 获取数据" in response.json()["answer"]

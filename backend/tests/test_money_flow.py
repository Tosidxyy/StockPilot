import asyncio
from datetime import date, datetime
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel

from app.agent.tools import get_stock_money_flow
from app.database.session import create_database_engine, create_session_factory, init_db
from app.main import create_app
from app.models.money_flow import MoneyFlowSeries
from app.providers.eastmoney import EastMoneyProvider
from app.providers.exceptions import DataSourceError, InvalidSymbolError, ProviderTimeoutError
from app.providers.money_flow import parse_flows
from app.services.collector import BEIJING
from app.services.money_flow import MoneyFlowService
from app.services.money_flow_collector import MoneyFlowCollector
from app.services.watchlist import WatchlistService
from test_api import FakeProvider
from test_services import Clock

ROW = "2026-09-28,270407264,-129333952,-141073296,225769248,44638016,2.75,-1.32,-1.44,2.30,0.45,291.99,-0.51,0,0"


def payload(symbol="300750", rows=None):
    return {"rc": 0, "data": {"code": symbol, "klines": [ROW] if rows is None else rows}}


class FlowProvider(FakeProvider):
    def __init__(self):
        super().__init__()
        self.calls = []
        self.flow_error = None
        self.empty = False

    async def get_stock_money_flow(self, symbol):
        self.calls.append(symbol)
        await asyncio.sleep(0)
        if self.flow_error:
            raise self.flow_error
        return parse_flows(payload(symbol, [] if self.empty else [ROW.replace("2026-09-28", "2026-09-25"), ROW]), symbol)


def test_field_mapping_units_missing_dedup_and_order():
    series = parse_flows(payload(rows=[ROW, ROW.replace("2026-09-28", "2026-09-25"), ROW]), "300750")
    assert len(series.items) == 2 and series.items[-1].date == date(2026, 9, 28)
    last = series.items[-1]
    assert last.main_net == 270407264 and last.super_large_net == 44638016
    assert last.large_net == 225769248 and last.medium_net == -141073296 and last.small_net == -129333952
    assert last.main_ratio == 2.75 and last.super_large_ratio == 0.45 and last.large_ratio == 2.30
    assert last.medium_ratio == -1.44 and last.small_ratio == -1.32
    assert series.amount_unit == "CNY" and series.ratio_unit == "percent"
    missing = parse_flows(payload(rows=[ROW.replace("270407264", "-").replace("2.75", "")]), "300750").items[0]
    assert missing.main_net is None and missing.main_ratio is None
    assert parse_flows(payload(rows=[]), "300750").items == []
    dates = [ROW.replace("2026-09-28", f"2026-08-{i:02}") for i in range(1, 32)]
    assert len(parse_flows(payload(rows=dates), "300750").items) == 30


@pytest.mark.parametrize("raw", [
    None, {"rc": 1}, {"rc": 0, "data": None}, payload("600519"),
    {"rc": 0, "data": {"code": "300750", "klines": None}}, payload(rows=["bad"]),
    payload(rows=[ROW.replace("2026-09-28", "2026-02-30")]),
    payload(rows=[ROW.replace("2026-09-28", "2026-W40-1")]),
    payload(rows=[ROW.replace("270407264", "NaN")]), payload(rows=[ROW.replace("270407264", "Infinity")]),
    payload(rows=[ROW.replace("2.75", "101")]), payload(rows=[ROW.replace("2.75", "word")]),
])
def test_invalid_source_not_silently_zeroed(raw):
    with pytest.raises((ValueError, TypeError)):
        parse_flows(raw, "300750")


def test_adapter_fallback_timeouts_symbol_validation_and_resilient_delegation():
    async def run():
        requests = []
        def response(request):
            requests.append(request)
            if request.url.host == "push2his.eastmoney.com":
                return httpx.Response(200, json={"rc": 0, "data": None})
            return httpx.Response(200, json=payload())
        async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
            provider = EastMoneyProvider(client)
            from app.providers.resilient import ResilientMarketProvider
            wrapper = ResilientMarketProvider(provider)
            try:
                result = await wrapper.get_stock_money_flow("300750")
                assert result.items[0].main_net == 270407264 and len(requests) == 2
                assert requests[-1].url.params["secid"] == "0.300750"
                assert requests[-1].url.params["lmt"] == "30" and requests[-1].url.params["klt"] == "101"
                with pytest.raises(InvalidSymbolError):
                    await wrapper.get_stock_money_flow("bad")
                assert len(requests) == 2
            finally:
                await wrapper.aclose()
        def timeout(request):
            raise httpx.ReadTimeout("private URL", request=request)
        async with httpx.AsyncClient(transport=httpx.MockTransport(timeout)) as client:
            with pytest.raises(ProviderTimeoutError):
                await EastMoneyProvider(client).get_stock_money_flow("600519")
    asyncio.run(run())


def setup(tmp_path, background=False):
    engine = create_database_engine(f"sqlite:///{(tmp_path/'flows.db').as_posix()}")
    init_db(engine)
    sessions = create_session_factory(engine)
    watch = WatchlistService(sessions)
    provider, timer = FlowProvider(), Clock()
    service = MoneyFlowService(provider, sessions, watch, background=background, timer=timer)
    return engine, sessions, watch, provider, timer, service


def test_shared_fetch_ttl_stale_backoff_restart_and_empty_snapshot(tmp_path):
    async def run():
        engine, sessions, watch, provider, timer, service = setup(tmp_path)
        try:
            a, b = await asyncio.gather(service.get("300750"), service.get("300750", 1))
            assert len(a.data.items) == 2 and len(b.data.items) == 1 and provider.calls == ["300750"]
            saved = a.cached_at
            timer.now = 61
            tool = await get_stock_money_flow(SimpleNamespace(money_flow=service), "300750", 1)
            assert tool["stale"] and tool["items"][0]["date"] == "2026-09-28"
            assert provider.calls == ["300750"]
            provider.flow_error = DataSourceError("private failure")
            stale = await service.get("300750")
            assert stale.stale and stale.cached_at == saved
            await service.get("300750")
            assert len(provider.calls) == 2
            with pytest.raises(DataSourceError):
                await service.get("600519")
            await service.aclose()
            restarted = MoneyFlowService(provider, sessions, watch, timer=timer)
            old = await restarted.get("300750")
            assert old.stale and old.cached_at == saved and old.data.items == a.data.items
            await restarted.aclose()
            provider.flow_error = None
            provider.empty = True
            timer.now = 100
            empty = await service.get("600519")
            assert empty.data.items == [] and empty.state == "ready"
            restart_empty = MoneyFlowService(provider, sessions, watch, background=True)
            watch.add("600519")
            read = await restart_empty.get("600519")
            assert read.state == "ready" and read.data.items == [] and read.cached_at == empty.cached_at
            await restart_empty.aclose()
        finally:
            await service.aclose()
            engine.dispose()
    asyncio.run(run())


def test_background_unopened_membership_cadence_readonly_retry_and_stop(tmp_path):
    async def run():
        engine, _, watch, provider, timer, service = setup(tmp_path, True)
        wall = [datetime(2026, 9, 28, 10, tzinfo=BEIJING)]
        collector = MoneyFlowCollector(service, timer=timer, clock=lambda: wall[0])
        service.collector = collector
        try:
            watch.add("300750"); watch.add("600519"); watch.add("600578")
            assert (await service.get("300750")).state == "warming" and provider.calls == []
            await collector.poll()
            assert len(collector._pending) == 2
            await asyncio.gather(*collector._pending.values())
            await collector.poll(); await asyncio.gather(*collector._pending.values())
            assert set(provider.calls) == {"300750", "600519", "600578"}
            saved = await service.get("300750")
            for _ in range(3):
                await service.get("300750", 1)
                await get_stock_money_flow(SimpleNamespace(money_flow=service), "300750")
            assert len(provider.calls) == 3
            timer.now = 59
            await collector.poll(); assert len(provider.calls) == 3
            timer.now = 61
            provider.flow_error = DataSourceError("offline")
            await collector.poll(); await asyncio.gather(*collector._pending.values())
            old = await service.get("300750")
            assert old.stale and old.cached_at == saved.cached_at
            watch.remove("600519"); watch.add("002414")
            await collector.poll(); await asyncio.gather(*collector._pending.values())
            assert (await service.get("002414")).state == "unavailable"
            calls = len(provider.calls)
            collector.request_refresh({"002414"})
            await collector.poll(); await asyncio.gather(*collector._pending.values())
            assert len(provider.calls) == calls  # Service backoff survives refresh requests.
            timer.now = 65
            provider.flow_error = None
            collector.request_refresh({"002414"})
            await collector.poll(); await asyncio.gather(*collector._pending.values())
            assert (await service.get("002414")).state == "ready"
            wall[0] = datetime(2026, 9, 28, 12, tzinfo=BEIJING)
            assert collector.interval() == 300
            timer.now = 122
            calls = len(provider.calls)
            await collector.poll(); await asyncio.gather(*collector._pending.values())
            assert len(provider.calls) == calls
            assert provider.calls.count("600519") == 2
            service.background = False
            await service.get("601777")
            assert provider.calls[-1] == "601777"
        finally:
            await collector.stop(); await service.aclose(); engine.dispose()
            assert not collector._pending and collector._runner is None
    asyncio.run(run())


def test_api_tool_trace_limits_empty_and_errors(tmp_path):
    provider = FlowProvider()
    def model(messages, _):
        if any(isinstance(part, ToolReturnPart) for part in messages[-1].parts):
            return ModelResponse(parts=[TextPart("资金流测试：统计截至2026-09-28，仅供参考。")])
        return ModelResponse(parts=[ToolCallPart("get_stock_money_flow", {"symbol": "300750", "limit": 1})])
    with TestClient(create_app(provider=provider, database_url=f"sqlite:///{(tmp_path/'api.db').as_posix()}",
                              agent_model=FunctionModel(model))) as client:
        assert client.get("/health").status_code == 200
        result = client.get("/api/stocks/300750/money-flow").json()
        assert result["collection_state"] == "ready" and len(result["data"]["items"]) == 2
        assert len(client.get("/api/stocks/300750/money-flow?limit=1").json()["data"]["items"]) == 1
        response = client.post("/api/agent/chat", json={"message": "宁德时代主力净流入"})
        assert response.status_code == 200, response.text
        trace = str(client.get("/api/agent/traces/recent").json())
        assert "get_stock_money_flow" in trace and "统计截至" in trace and "success" in trace
        assert provider.calls == ["300750"]
        assert client.post("/api/stocks/300750/money-flow/refresh").status_code == 202
        assert provider.calls == ["300750"]
        for path in ("/api/stocks/bad/money-flow", "/api/stocks/300750/money-flow?limit=0", "/api/stocks/300750/money-flow?limit=31"):
            assert client.get(path).status_code == 422
        provider.flow_error = ProviderTimeoutError("private timeout")
        assert client.get("/api/stocks/600519/money-flow").status_code == 504
        provider.flow_error = DataSourceError("private failure")
        bad = client.get("/api/stocks/600578/money-flow")
        assert bad.status_code == 503 and "private" not in bad.text
        provider.flow_error = None; provider.empty = True
        assert client.get("/api/stocks/002414/money-flow").json()["data"]["items"] == []
        deps = SimpleNamespace(money_flow=client.app.state.money_flow_service)
        for code, limit in (("bad", 5), ("300750", 0), ("300750", 31)):
            with pytest.raises(ValueError):
                client.portal.call(get_stock_money_flow, deps, code, limit)

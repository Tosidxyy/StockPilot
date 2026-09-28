import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.providers.eastmoney import EastMoneyProvider
from app.providers.exceptions import DataSourceError, InvalidSymbolError, ProviderTimeoutError
from app.providers.history import TencentHistory
from app.providers.resilient import ResilientMarketProvider


ROWS = [
    ["2026-09-28", "5.200", "5.190", "5.210", "5.120", "318105.000"],
    ["2026-09-25", "5.250", "5.220", "5.280", "5.180", "420000.000"],
]


@pytest.mark.parametrize("symbol,code,period,interval", [
    ("600578", "sh600578", "daily", "day"),
    ("300750", "sz300750", "weekly", "week"),
    ("832566", "bj832566", "daily", "day"),
])
def test_history_mapping_and_missing_amount(symbol, code, period, interval):
    def handler(request):
        assert request.url.params["param"] == f"{code},{interval},,,2"
        assert request.url.path == "/appstock/app/kline/kline"
        return httpx.Response(200, json={"code": 0, "data": {code: {interval: ROWS}}})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            rows = await TencentHistory(client).get_kline(symbol, period, 2)
        assert [item.date.isoformat() for item in rows] == ["2026-09-25", "2026-09-28"]
        assert rows[-1].close == 5.19 and rows[-1].volume == 318105
        assert all(item.turnover is None and item.source == "tencent" for item in rows)
    asyncio.run(run())


@pytest.mark.parametrize("payload", [
    [], {"code": 1}, {"code": 0, "data": None},
    {"code": 0, "data": {"sh600578": {"day": []}}},
    {"code": 0, "data": {"sh600578": {"qfqday": ROWS}}},
    {"code": 0, "data": {"sh600578": {"day": [["2026-09-28", "NaN", "5", "6", "4", "100"]]}}},
    {"code": 0, "data": {"sh600578": {"day": [["bad-date", "5", "5", "6", "4", "100"]]}}},
    {"code": 0, "data": {"sh600578": {"day": [["2026-09-28", "5", "5", "4", "6", "100"]]}}},
])
def test_history_rejects_empty_malformed_and_adjusted_data(payload):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))) as client:
            with pytest.raises(DataSourceError):
                await TencentHistory(client).get_kline("600578")
    asyncio.run(run())


def test_history_fallback_api_and_persisted_snapshot(tmp_path):
    calls = []
    failing = False

    def handler(request):
        calls.append(request.url.host)
        if "eastmoney" in request.url.host or failing:
            raise httpx.RemoteProtocolError("disconnected", request=request)
        return httpx.Response(200, json={"code": 0, "data": {"sh600578": {"day": ROWS}}})

    def provider():
        # Owned clients are closed by the application's lifespan.
        primary = EastMoneyProvider(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        history = TencentHistory(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        primary._owns_client = history._owns_client = True
        return ResilientMarketProvider(primary, history)

    database = f"sqlite:///{(tmp_path / 'fallback.db').as_posix()}"
    first = provider()
    app = create_app(provider=first, database_url=database)
    with TestClient(app) as client:
        response = client.get("/api/stocks/600578/kline?period=daily&limit=2")
        assert response.status_code == 200
        saved = response.json()
        assert saved["stale"] is False
        assert saved["data"][-1]["source"] == "tencent"
        assert saved["data"][-1]["turnover"] is None
        assert "push2his.eastmoney.com" in calls and "1.push2his.eastmoney.com" in calls
        assert calls[-1] == "web.ifzq.gtimg.cn"
        calls.clear()
        assert client.get("/api/stocks/600578/kline?period=daily&limit=2").json() == saved
        assert calls == []
        asyncio.run(first.aclose())
    failing = True
    second = provider()
    with TestClient(create_app(provider=second, database_url=database)) as client:
        result = client.get("/api/stocks/600578/kline?period=daily&limit=2").json()
        assert result["stale"] is True
        assert result["data"] == saved["data"] and result["cached_at"] == saved["cached_at"]
        assert client.get("/api/stocks/600578/kline?period=weekly&limit=2").status_code == 503
        assert client.get("/api/stocks/600519/kline?period=daily&limit=2").status_code == 503
        asyncio.run(second.aclose())


def test_primary_success_and_invalid_symbol_never_use_history():
    def handler(request):
        assert "eastmoney" in request.url.host
        return httpx.Response(200, json={"rc": 0, "data": {"klines": ["2026-09-28,5.2,5.19,5.21,5.12,318105,165000000"]}})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = ResilientMarketProvider(EastMoneyProvider(client), TencentHistory(client))
            assert (await provider.get_kline("600578"))[0].source == "eastmoney"
            with pytest.raises(InvalidSymbolError):
                await provider.get_kline("900901")
            with pytest.raises(ValueError):
                await provider.get_kline("600578", "monthly")
    asyncio.run(run())


def test_history_timeout_remains_distinct():
    def handler(request):
        raise httpx.ReadTimeout("timeout", request=request)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ProviderTimeoutError):
                await TencentHistory(client).get_kline("600578")
    asyncio.run(run())

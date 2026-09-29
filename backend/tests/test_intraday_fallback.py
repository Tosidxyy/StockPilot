import asyncio
from datetime import datetime
from decimal import Decimal

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.models.market import IntradayPoint
from app.providers.eastmoney import EastMoneyProvider
from app.providers.exceptions import DataSourceError, InvalidSymbolError, ProviderTimeoutError
from app.providers.history import TencentHistory
from app.providers.intraday import TencentIntraday, parse_minutes
from app.providers.resilient import ResilientMarketProvider
from app.services.reads import StockReadService
from test_api import FakeProvider
from test_collector import setup, settle
from test_services import Clock


ROWS = ["0930 5.20 100 52000.10", "0931 5.19 150 77950.20", "1130 5.18 200 103850.20",
        "1300 5.18 210 109030.20", "1500 5.19 300 155740.20", "1530 5.19 350 181690.20"]


def payload(code="sh600578", rows=None, date="20260928"):
    return {"code": 0, "data": {code: {"data": {"date": date, "data": rows if rows is not None else ROWS}}}}


@pytest.mark.parametrize("kind,symbol,code", [("stock", "600578", "sh600578"), ("stock", "300750", "sz300750"),
    ("index", "000001", "sh000001"), ("index", "399001", "sz399001"), ("index", "399006", "sz399006")])
def test_mapping_dates_units_and_regular_session(kind, symbol, code):
    def handler(request):
        assert request.url.params["code"] == code
        assert request.url.path == "/appstock/app/minute/query"
        return httpx.Response(200, json=payload(code))
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            source = TencentIntraday(client)
            points = await (source.get_stock_intraday(symbol) if kind == "stock" else source.get_index_intraday(symbol))
            assert len(points) == 5 and points[-1].time == datetime(2026, 9, 28, 15)
            assert points[0].volume == 100 and points[1].volume == 50
            assert points[1].turnover == float(Decimal("77950.20") - Decimal("52000.10"))
            assert points[2].volume is None  # Missing minutes cannot be spread/estimated.
            assert points[3].volume == 10  # Normal noon break is not a missing-minute gap.
            assert all(point.source == "tencent" for point in points)
    asyncio.run(run())


def test_dedup_sort_and_missing_opening_minute():
    points = parse_minutes(payload(rows=["0932 5.19 175 90700", "0931 5.20 150 78000", "0932 5.20 180 93000"]), "sh600578")
    assert len(points) == 2 and points[0].volume is None and points[0].turnover is None
    assert points[1].price == 5.20 and points[1].volume == 30
    assert points[1].turnover == 15000


@pytest.mark.parametrize("invalid", [
    {"code": 1}, {"code": 0, "data": None}, payload(rows=[]), payload(date="bad"),
    payload(rows=["0930 NaN 1 100"]), payload(rows=["0930 5 Infinity 100"]),
    payload(rows=["0930 5 1 1e999999"]), payload(rows=["0930 5 -1 100"]),
    payload(rows=["0930 5 1.5 100"]), payload(rows=["2560 5 1 100"]),
    payload(rows=["1530 5 1 100"]), payload(rows=["0930 5 100 500", "0931 5 99 600"]),
    payload(rows=["0930 5 100 500", "0931 5 110 499"]),
])
def test_invalid_or_empty_series_cannot_replace_good_cache(invalid):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=invalid))) as client:
            with pytest.raises(DataSourceError):
                await TencentIntraday(client).get_stock_intraday("600578")
    asyncio.run(run())


def test_timeout_and_invalid_codes_do_not_request_wrong_market():
    calls = []
    def handler(request):
        calls.append(request.url)
        raise httpx.ReadTimeout("private timeout", request=request)
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            source = TencentIntraday(client)
            for call in (source.get_stock_intraday("bad"), source.get_index_intraday("123456")):
                with pytest.raises(InvalidSymbolError):
                    await call
            assert not calls
            with pytest.raises(ProviderTimeoutError):
                await source.get_stock_intraday("600578")
            assert len(calls) == 1
    asyncio.run(run())


def test_tencent_preference_cooldown_recovery_and_stock_index_isolation():
    class EastMoney(FakeProvider):
        def __init__(self): super().__init__(); self.calls=[]
        async def get_stock_intraday(self, symbol):
            self.calls.append(("stock",symbol))
            return [IntradayPoint(time=datetime(2026,9,28,9,30),price=5,volume=1,turnover=500)]
        async def get_index_intraday(self,code):
            self.calls.append(("index",code))
            return [IntradayPoint(time=datetime(2026,9,28,9,30),price=3000,volume=1,turnover=500)]
        async def aclose(self): pass
    class Tencent:
        def __init__(self): self.calls=[]; self.failed=set()
        async def get_stock_intraday(self, symbol):
            return await self.read("stock",symbol)
        async def get_index_intraday(self, code):
            return await self.read("index",code)
        async def read(self,kind,code):
            self.calls.append((kind,code))
            if (kind,code) in self.failed:
                if kind=="index": return []
                raise ProviderTimeoutError("offline")
            return [IntradayPoint(time=datetime(2026,9,28,9,30),price=5,volume=1,turnover=500,source="tencent")]
        async def aclose(self): pass
    async def run():
        eastmoney,tencent,timer=EastMoney(),Tencent(),Clock()
        provider=ResilientMarketProvider(eastmoney,intraday=tencent,timer=timer)
        try:
            assert (await provider.get_stock_intraday("600578"))[0].source=="tencent" and not eastmoney.calls
            tencent.failed.add(("stock","600578"))
            assert (await provider.get_stock_intraday("600578"))[0].source=="eastmoney"
            count=len(tencent.calls)
            for seconds in (2,4,28):
                timer.now=seconds
                assert (await provider.get_stock_intraday("600578"))[0].source=="eastmoney"
            assert len(tencent.calls)==count
            assert (await provider.get_stock_intraday("300750"))[0].source=="tencent"
            assert (await provider.get_index_intraday("000001"))[0].source=="tencent"
            tencent.failed.add(("index","000001"))
            assert (await provider.get_index_intraday("000001"))[0].source=="eastmoney"
            assert (await provider.get_stock_intraday("000001"))[0].source=="tencent"
            tencent.failed.clear();timer.now=31
            assert (await provider.get_stock_intraday("600578"))[0].source=="tencent"
        finally: await provider.aclose()
    asyncio.run(run())


def test_both_empty_sources_are_failure_not_successful_empty():
    class Empty(FakeProvider):
        async def get_stock_intraday(self,symbol): return []
        async def get_index_intraday(self,code): return []
        async def aclose(self): pass
    async def run():
        provider=ResilientMarketProvider(Empty(),intraday=Empty())
        try:
            with pytest.raises(DataSourceError): await provider.get_stock_intraday("600578")
            with pytest.raises(DataSourceError): await provider.get_index_intraday("000001")
        finally: await provider.aclose()
    asyncio.run(run())


@pytest.mark.parametrize("status", [501, 503])
def test_tencent_http_failure_uses_eastmoney_fallback(status):
    class EastMoney(FakeProvider):
        async def aclose(self): pass
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda _: httpx.Response(status,text="unavailable"))) as client:
            provider=ResilientMarketProvider(EastMoney(),intraday=TencentIntraday(client))
            try:
                stock=await provider.get_stock_intraday("600578")
                index=await provider.get_index_intraday("000001")
                assert stock and index
                assert all(point.source=="eastmoney" for point in stock+index)
            finally: await provider.aclose()
    asyncio.run(run())


def test_actual_provider_api_snapshot_restart_and_both_sources_failed(tmp_path):
    calls, failing = [], [False]
    def handler(request):
        calls.append(request.url.host)
        if "eastmoney" in request.url.host or failing[0]:
            raise httpx.RemoteProtocolError("private disconnect", request=request)
        code = request.url.params["code"]
        return httpx.Response(200, json=payload(code, ["0930 5.20 100 52000", "0931 5.19 150 77950"]))
    def provider():
        clients = [httpx.AsyncClient(transport=httpx.MockTransport(handler)) for _ in range(3)]
        primary, history, minute = EastMoneyProvider(clients[0]), TencentHistory(clients[1]), TencentIntraday(clients[2])
        primary._owns_client = history._owns_client = minute._owns_client = True
        return ResilientMarketProvider(primary, history, minute)
    database = f"sqlite:///{(tmp_path/'minute.db').as_posix()}"
    first = provider()
    with TestClient(create_app(provider=first, database_url=database)) as client:
        stock = client.get("/api/stocks/600578/intraday")
        index = client.get("/api/market/indices/000001/intraday")
        assert stock.status_code == index.status_code == 200
        saved = stock.json()
        assert not saved["stale"] and saved["data"][0]["source"] == "tencent"
        assert calls == ["web.ifzq.gtimg.cn", "web.ifzq.gtimg.cn"]
        count = len(calls)
        assert client.get("/api/stocks/600578/intraday").json() == saved and len(calls) == count
        asyncio.run(first.aclose())
    failing[0] = True
    second = provider()
    with TestClient(create_app(provider=second, database_url=database)) as client:
        old = client.get("/api/stocks/600578/intraday")
        assert old.status_code == 200 and old.json()["stale"]
        assert old.json()["data"] == saved["data"] and old.json()["cached_at"] == saved["cached_at"]
        assert client.get("/api/stocks/300750/intraday").status_code == 503
        asyncio.run(second.aclose())


def test_background_watchlist_saves_tencent_without_opening_detail(tmp_path):
    async def run():
        class Primary(FakeProvider):
            async def aclose(self): pass
            async def get_stock_intraday(self, symbol):
                raise DataSourceError("primary offline")
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload(request.url.params["code"])))) as client:
            provider = ResilientMarketProvider(Primary(), intraday=TencentIntraday(client))
            engine, sessions, _, stocks, watchlist, collector = setup(tmp_path, provider)
            watchlist.add("600578")
            await settle(collector)
            cached = await stocks.cached_intraday("600578")
            assert cached and cached.data[0].source == "tencent"
            reader = StockReadService(stocks, watchlist, collector)
            assert (await reader.overview()).items[0].resources["intraday"].source == "tencent"
            await collector.stop(); await stocks.aclose(); await provider.aclose(); engine.dispose()
    asyncio.run(run())


def test_old_intraday_snapshot_defaults_to_eastmoney():
    point = IntradayPoint.model_validate({"time": "2026-09-28T09:30:00", "price": 5.2, "volume": 100, "turnover": 52000})
    assert point.source == "eastmoney"

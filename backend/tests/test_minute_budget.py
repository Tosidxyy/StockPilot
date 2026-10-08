import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.providers.exceptions import DataSourceError
from app.providers.resilient import ResilientMarketProvider
from app.providers.sina_intraday import SinaIntraday
from app.services.market import MarketService
from app.services.stock import StockService
from test_api import FakeProvider
from test_services import Clock
from test_sina_intraday import candle, wrapper


class MinuteSource(FakeProvider):
    async def aclose(self):
        pass


class BlockedMinutes(MinuteSource):
    def __init__(self):
        super().__init__()
        self.calls = 0
        self.cancelled = 0

    async def read(self):
        self.calls += 1
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled += 1
            raise

    async def get_stock_intraday(self, symbol):
        return await self.read()

    async def get_index_intraday(self, code="000001"):
        return await self.read()


@pytest.mark.parametrize("kind", ["stock", "index"])
def test_slow_backup_is_cancelled_cache_keeps_time_and_preferred_recovers(kind):
    async def run():
        clock, failed = Clock(), [False]
        source_calls = []
        target = "sh600519" if kind == "stock" else "sh000001"

        def respond(request):
            source_calls.append(request.url.params["symbol"])
            if failed[0] and request.url.params["symbol"] == target:
                raise httpx.RemoteProtocolError("controlled preferred outage", request=request)
            row = candle("2026-09-29 09:31:00")
            if clock.now >= 4:
                row["close"] = "5.3"
            return httpx.Response(200, text=wrapper([row]))

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            slow, untouched = BlockedMinutes(), BlockedMinutes()
            provider = ResilientMarketProvider(slow, intraday=slow,
                minute_sources=[SinaIntraday(client), slow, untouched], timer=clock, minute_timeout=.05)
            service = StockService(provider, timer=clock) if kind == "stock" else MarketService(provider, timer=clock)
            read = (lambda: service.get_intraday("600519")) if kind == "stock" else (
                lambda: service.get_index_intraday("000001"))
            try:
                first = await read()
                clock.now = 2
                failed[0] = True
                # Without a whole-chain budget these readers wait forever on the backup.
                results = await asyncio.wait_for(asyncio.gather(*(read() for _ in range(5))), 1)
                assert all(result.stale and result.data == first.data and result.cached_at == first.cached_at
                           for result in results)
                assert slow.calls == slow.cancelled == 1 and untouched.calls == 0
                assert len(source_calls) == 2  # The five readers share one refresh.
                assert (await provider.get_stock_intraday("300750"))[0].source == "sina"
                clock.now = 4
                failed[0] = False
                recovered = await read()
                assert not recovered.stale and recovered.data[0].price == 5.3
                assert recovered.cached_at > first.cached_at and slow.calls == 1
            finally:
                await service.aclose()
                await provider.aclose()
    asyncio.run(run())


@pytest.mark.parametrize("path", ["/api/stocks/600519/intraday", "/api/market/indices/000001/intraday"])
def test_whole_chain_deadline_is_shared_and_without_cache_api_returns_504(tmp_path, path):
    class SlowFailure(MinuteSource):
        async def fail(self):
            await asyncio.sleep(.03)
            raise DataSourceError("controlled first-source failure")

        async def get_stock_intraday(self, symbol):
            return await self.fail()

        async def get_index_intraday(self, code="000001"):
            return await self.fail()

    primary, backup, untouched = SlowFailure(), BlockedMinutes(), BlockedMinutes()
    provider = ResilientMarketProvider(primary, intraday=backup,
        minute_sources=[primary, backup, untouched], minute_timeout=.05)
    try:
        url = f"sqlite:///{(tmp_path / 'budget.db').as_posix()}"
        with TestClient(create_app(provider=provider, database_url=url, prefetch_enabled=False)) as client:
            assert client.get("/health").status_code == 200
            response = client.get(path)
            assert response.status_code == 504
            assert response.json() == {"detail": "Market data provider timed out"}
            assert backup.calls == backup.cancelled == 1 and untouched.calls == 0
    finally:
        asyncio.run(provider.aclose())


def test_fast_backup_still_succeeds_inside_shared_budget():
    class Failed(MinuteSource):
        async def get_stock_intraday(self, symbol):
            raise DataSourceError("controlled outage")

    async def run():
        primary, backup = Failed(), MinuteSource()
        provider = ResilientMarketProvider(primary, intraday=backup,
            minute_sources=[primary, backup], minute_timeout=.1)
        try:
            assert await provider.get_stock_intraday("600519") == await backup.get_stock_intraday("600519")
        finally:
            await provider.aclose()
    asyncio.run(run())

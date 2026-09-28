import asyncio
from datetime import date, datetime

import pytest

from app.models.market import IntradayPoint, KlineItem, MarketIndex, StockQuote, SymbolSearchResult
from app.providers.base import MarketDataProvider
from app.providers.exceptions import DataSourceError
from app.services.market import MarketService
from app.services.stock import StockService


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class FakeProvider(MarketDataProvider):
    def __init__(self) -> None:
        self.quote_calls: list[tuple[str, ...]] = []
        self.index_calls = 0
        self.intraday_calls = 0
        self.kline_calls = 0
        self.search_calls = 0
        self.fail = False

    async def get_quotes(self, symbols):
        self.quote_calls.append(tuple(symbols))
        if self.fail:
            raise DataSourceError("offline")
        return [StockQuote(
            symbol=symbol, name="测试股票", price=12.3, change_percent=1.0,
            change_amount=0.12, volume=100, turnover=1230.0, high=12.4,
            low=12.1, open=12.2, previous_close=12.18, turnover_rate=0.1,
            pe_ratio=10.0,
        ) for symbol in symbols]

    async def get_indices(self):
        self.index_calls += 1
        if self.fail:
            raise DataSourceError("offline")
        return [MarketIndex(symbol="000001", name="上证指数", value=3000.0,
                            change_percent=1.0, change_amount=30.0, volume=1, turnover=2.0)]

    async def get_index_intraday(self, index_code="000001"):
        self.intraday_calls += 1
        if self.fail:
            raise DataSourceError("offline")
        return [IntradayPoint(time=datetime(2026, 9, 25, 9, 30), price=3000.0,
                              volume=100, turnover=2000.0)]

    async def get_stock_intraday(self, symbol):
        return await self.get_index_intraday()

    async def get_kline(self, symbol, period="daily", limit=120):
        self.kline_calls += 1
        if self.fail:
            raise DataSourceError("offline")
        return [KlineItem(date=date(2026, 9, 25), open=12.0, close=12.3,
                          high=12.4, low=11.9, volume=100, turnover=1230.0)]

    async def search_stocks(self, query, limit=20):
        self.search_calls += 1
        if self.fail:
            raise DataSourceError("offline")
        return [SymbolSearchResult(symbol="600519", name="贵州茅台", market="SH")]


def test_stock_service_batches_caches_and_returns_stale_after_failure() -> None:
    async def run() -> None:
        clock = Clock()
        provider = FakeProvider()
        service = StockService(provider, quote_ttl=5, stale_ttl=400, timer=clock)
        first = await service.get_quotes(["600519", "000001", "600519"])
        assert [item.symbol for item in first.data] == ["600519", "000001"]
        assert provider.quote_calls == [("600519", "000001")]
        first.data[0].price = 999.0
        cached = await service.get_quotes(["600519", "000001"])
        assert cached.data[0].price == 12.3
        assert not cached.stale
        assert len(provider.quote_calls) == 1

        clock.now = 6.0
        provider.fail = True
        stale = await service.get_quotes(["600519", "000001"])
        assert stale.stale and stale.data[0].price == 12.3
        assert len(provider.quote_calls) == 2

        clock.now = 401.0
        with pytest.raises(DataSourceError):
            await service.get_quotes(["600519", "000001"])

    asyncio.run(run())


def test_market_service_uses_short_cache_and_stale_flag() -> None:
    async def run() -> None:
        clock = Clock()
        provider = FakeProvider()
        service = MarketService(provider, index_ttl=3, stale_ttl=30, timer=clock)
        assert (await service.get_indices()).data[0].value == 3000.0
        assert not (await service.get_indices()).stale
        assert provider.index_calls == 1
        clock.now = 4.0
        provider.fail = True
        assert (await service.get_indices()).stale
        assert provider.index_calls == 2
        provider.fail = False
        assert (await service.get_index_intraday()).data[0].price == 3000.0
        await service.get_index_intraday()
        assert provider.intraday_calls == 1
        clock.now = 6.0
        await service.get_index_intraday()
        assert provider.intraday_calls == 2
        provider.fail = True
        clock.now = 8.0
        assert (await service.get_index_intraday()).stale

    asyncio.run(run())


def test_stock_search_and_kline_cache_separately() -> None:
    async def run() -> None:
        provider = FakeProvider()
        service = StockService(provider)
        assert (await service.search(" 茅台 ")).data[0].symbol == "600519"
        await service.search("茅台")
        assert provider.search_calls == 1
        assert (await service.get_kline("600519", "weekly", 2)).data[0].close == 12.3
        await service.get_kline("600519", "weekly", 2)
        assert provider.kline_calls == 1
        quote = await service.get_quote("600519")
        assert quote.data is not None and quote.data.symbol == "600519"

    asyncio.run(run())


def test_stock_intraday_cache_is_per_symbol_and_refreshes_before_next_poll():
    async def run():
        clock = Clock()
        provider = FakeProvider()
        service = StockService(provider, timer=clock, stale_ttl=400)
        await service.get_intraday("300750")
        await service.get_intraday("300750")
        await service.get_intraday("600519")
        assert provider.intraday_calls == 2
        clock.now = 0.9
        await service.get_intraday("300750")
        assert provider.intraday_calls == 2
        clock.now = 2
        assert not (await service.get_intraday("300750")).stale
        assert provider.intraday_calls == 3
        clock.now = 4
        provider.fail = True
        result = await service.get_intraday("300750")
        assert result.stale and result.data[0].price == 3000
        clock.now = 403
        with pytest.raises(DataSourceError):
            await service.get_intraday("300750")

    asyncio.run(run())

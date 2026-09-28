import asyncio
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.agent.tools import AgentDependencies, get_market_indices, get_stock_kline, get_stock_quote, get_watchlist
from app.database.models import MarketSnapshot
from app.database.session import create_database_engine, create_session_factory, init_db
from app.models.market import KlineItem
from app.providers.exceptions import DataSourceError
from app.services.market import MarketService
from app.services.stock import StockService
from app.services.watchlist import WatchlistService
from test_api import FakeProvider
from test_services import Clock


class CountingProvider(FakeProvider):
    def __init__(self):
        super().__init__()
        self.index_calls = 0
        self.price = 12.3
        self.partial = False

    async def get_indices(self):
        self.index_calls += 1
        return await super().get_indices()

    async def get_quotes(self, symbols):
        data = await super().get_quotes(symbols)
        for quote in data:
            quote.price = self.price
        return data[:1] if self.partial else data

    async def get_kline(self, symbol, period="daily", limit=120):
        await super().get_kline(symbol, period, limit)
        return [KlineItem(date=date(2026, 9, day), open=10, close=day,
                          high=30, low=1, volume=100, turnover=1000)
                for day in range(1, 21)][-limit:]


def test_tools_reuse_ui_batch_and_kline_cache_without_refreshing_age(tmp_path):
    async def run():
        engine = create_database_engine(f"sqlite:///{(tmp_path / 'memory.db').as_posix()}")
        init_db(engine)
        watchlist = WatchlistService(create_session_factory(engine))
        for symbol in ("600519", "000001"):
            watchlist.add(symbol)
        clock = Clock()
        provider = CountingProvider()
        stocks = StockService(provider, timer=clock)
        market = MarketService(provider, timer=clock)
        deps = AgentDependencies(stocks, market, watchlist)
        try:
            original = await stocks.get_quotes(["600519", "000001"])
            await stocks.get_kline("600519", "daily", 120)
            await market.get_indices()
            fresh = await get_stock_quote(deps, "600519")
            assert not fresh["stale"]
            assert fresh["cached_at"] == original.cached_at.isoformat()
            clock.now = 61  # All fresh TTLs expired; old cached data is still usable.
            provider.error = DataSourceError("must not access upstream")
            quote = await get_stock_quote(deps, "600519")
            assert quote["stale"] and quote["quote"]["price"] == 12.3
            assert quote["cached_at"] == fresh["cached_at"]
            assert quote["cache_age_seconds"] >= 0
            quote["quote"]["price"] = 999
            assert (await get_stock_quote(deps, "600519"))["quote"]["price"] == 12.3
            kline = await get_stock_kline(deps, "600519", "daily", 5)
            assert [row["date"] for row in kline["klines"]] == [f"2026-09-{day}" for day in range(16, 21)]
            assert kline["stale"]
            assert (await get_market_indices(deps))["stale"]
            watch = await get_watchlist(deps)
            assert [row["symbol"] for row in watch["quotes"]] == ["600519", "000001"]
            assert watch["cached_at"] == fresh["cached_at"]
            assert provider.quote_batches == [("600519", "000001")]
            assert provider.periods == ["daily"] and provider.index_calls == 1
            # UI reads still refresh the provider; tools alone use cache-first mode.
            provider.error = None
            provider.price = 15
            assert (await stocks.get_quotes(["600519", "000001"])).data[0].price == 15
            assert (await get_stock_quote(deps, "600519"))["quote"]["price"] == 15
        finally:
            engine.dispose()

    asyncio.run(run())


def test_tools_select_newest_covering_cache_and_skip_partial_batches(tmp_path):
    async def run():
        engine = create_database_engine(f"sqlite:///{(tmp_path / 'latest.db').as_posix()}")
        init_db(engine)
        sessions = create_session_factory(engine)
        provider = CountingProvider()
        clock = Clock()
        stocks = StockService(provider, timer=clock, snapshot_sessions=sessions)
        deps = AgentDependencies(stocks, MarketService(provider), WatchlistService(sessions))
        try:
            await stocks.get_quotes(["600519", "000001"])
            provider.price = 20
            clock.now = 2
            await stocks.get_quote("600519")
            assert (await get_stock_quote(deps, "600519"))["quote"]["price"] == 20
            provider.price = 30
            await stocks.get_quotes(["600519", "000001", "000002"])
            assert (await get_stock_quote(deps, "600519"))["quote"]["price"] == 30
            provider.partial = True
            await stocks.get_quotes(["600519", "000001", "000002", "000003"])
            before = len(provider.quote_batches)
            provider.error = DataSourceError("offline")
            # Newest batch key covers this stock, but its returned payload does not.
            assert (await get_stock_quote(deps, "000001"))["quote"]["price"] == 30
            assert len(provider.quote_batches) == before
            with pytest.raises(DataSourceError):
                await get_stock_quote(deps, "000004")
            provider.error = None
            await stocks.get_kline("600519", "daily", 2)
            await get_stock_kline(deps, "600519", "daily", 5)
            await get_stock_kline(deps, "600519", "weekly", 5)
            assert provider.periods == ["daily", "daily", "weekly"]
        finally:
            engine.dispose()

    asyncio.run(run())


def test_tools_use_persistent_cache_after_restart_and_reject_expired_snapshots(tmp_path):
    async def run():
        url = f"sqlite:///{(tmp_path / 'restart.db').as_posix()}"
        engine = create_database_engine(url)
        init_db(engine)
        sessions = create_session_factory(engine)
        provider = CountingProvider()
        stocks = StockService(provider, snapshot_sessions=sessions)
        market = MarketService(provider, snapshot_sessions=sessions)
        await stocks.get_quotes(["600519", "000001"])
        await stocks.get_kline("600519", "daily", 120)
        await market.get_indices()
        saved_at = datetime.now(timezone.utc) - timedelta(seconds=10)
        with sessions.begin() as session:
            for row in session.scalars(select(MarketSnapshot)):
                row.saved_at = saved_at
        engine.dispose()

        engine = create_database_engine(url)
        sessions = create_session_factory(engine)
        offline = CountingProvider()
        offline.error = DataSourceError("offline")
        watchlist = WatchlistService(sessions)
        watchlist.add("600519")
        watchlist.add("000001")
        deps = AgentDependencies(StockService(offline, snapshot_sessions=sessions),
                                 MarketService(offline, snapshot_sessions=sessions), watchlist)
        try:
            for result in [await get_stock_quote(deps, "600519"), await get_market_indices(deps),
                           await get_watchlist(deps), await get_stock_kline(deps, "600519", "daily", 5)]:
                assert result["cached_at"] == saved_at.isoformat()
                assert result["cache_age_seconds"] >= 10
            assert offline.quote_batches == [] and offline.periods == [] and offline.index_calls == 0
            assert (await get_stock_quote(deps, "600519"))["stale"]
            with sessions.begin() as session:
                for row in session.scalars(select(MarketSnapshot)):
                    row.saved_at = datetime.now(timezone.utc) - timedelta(days=8)
            with pytest.raises(DataSourceError):
                await get_stock_quote(deps, "600519")
            assert offline.quote_batches == [("600519",)]
        finally:
            engine.dispose()

    asyncio.run(run())

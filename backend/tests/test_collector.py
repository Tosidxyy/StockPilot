import asyncio
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database.models import MarketSnapshot
from app.database.session import create_database_engine, create_session_factory, init_db
from app.main import create_app
from app.providers.exceptions import DataSourceError
from app.services.collector import BEIJING, WatchlistCollector, is_market_session
from app.services.stock import StockService
from app.services.watchlist import WatchlistService
from test_api import FakeProvider
from test_services import Clock


class RecordingProvider(FakeProvider):
    def __init__(self):
        super().__init__()
        self.intraday = []
        self.history = []
        self.failed_symbol = None

    async def get_stock_intraday(self, symbol):
        self.intraday.append(symbol)
        if symbol == self.failed_symbol:
            raise DataSourceError("unavailable")
        return await super().get_stock_intraday(symbol)

    async def get_kline(self, symbol, period="daily", limit=120):
        self.history.append((symbol, period, limit))
        return await super().get_kline(symbol, period, limit)


def setup(tmp_path, provider=None, clock=None, wall=None, workers=4):
    engine = create_database_engine(f"sqlite:///{(tmp_path / 'collector.db').as_posix()}")
    init_db(engine)
    sessions = create_session_factory(engine)
    provider = provider or RecordingProvider()
    stocks = StockService(provider, timer=clock, snapshot_sessions=sessions)
    watchlist = WatchlistService(sessions)
    collector = WatchlistCollector(
        stocks, watchlist, workers=workers, timer=clock or Clock(),
        clock=wall or (lambda: datetime(2026, 9, 28, 10, tzinfo=BEIJING)),
    )
    return engine, sessions, provider, stocks, watchlist, collector


async def settle(collector, rounds=4):
    for _ in range(rounds):
        await collector.poll()
        await asyncio.gather(*collector._pending.values())


@pytest.mark.parametrize("weekday,hour,minute,active", [
    (28, 9, 14, False), (28, 9, 15, True), (28, 11, 29, True),
    (28, 11, 30, False), (28, 12, 59, False), (28, 13, 0, True),
    (28, 15, 5, False), (26, 10, 0, False),
])
def test_session_heuristic(weekday, hour, minute, active):
    assert is_market_session(datetime(2026, 9, weekday, hour, minute, tzinfo=BEIJING)) is active


@pytest.mark.parametrize("month,day,active", [
    (1, 1, False), (2, 23, False), (4, 6, False), (5, 4, False),
    (6, 19, False), (9, 25, False), (9, 30, True),
    (10, 1, False), (10, 6, False), (10, 7, False), (10, 8, True),
])
def test_published_2026_exchange_closures(month, day, active):
    assert is_market_session(datetime(2026, month, day, 10, tzinfo=BEIJING)) is active


def test_holiday_collector_slows_and_reopening_restores_two_second_target(tmp_path):
    async def run():
        clock = Clock()
        wall = [datetime(2026, 10, 6, 10, tzinfo=BEIJING)]
        engine, _, provider, stocks, watch, collector = setup(tmp_path, clock=clock, wall=lambda: wall[0])
        watch.add("600519")
        try:
            assert collector.interval("quote") == 300
            await settle(collector)
            assert len(provider.quote_batches) == len(provider.intraday) == 1
            clock.now = 2
            await settle(collector)
            assert len(provider.quote_batches) == len(provider.intraday) == 1
            clock.now = 300
            await settle(collector)
            assert len(provider.quote_batches) == len(provider.intraday) == 2
            wall[0] = datetime(2026, 10, 8, 9, 15, tzinfo=BEIJING)
            assert collector.interval("quote") == 2
            clock.now = 302
            await settle(collector)
            assert len(provider.quote_batches) == len(provider.intraday) == 3
        finally:
            await collector.stop()
            await stocks.aclose()
            engine.dispose()
    asyncio.run(run())


def test_unopened_stocks_warm_all_snapshots_and_restart_offline(tmp_path):
    async def run():
        engine, sessions, provider, stocks, watch, collector = setup(tmp_path)
        for code in ("600519", "300750", "600578"):
            watch.add(code)
        try:
            # No quote, chart or HTTP page reads precede collection.
            await settle(collector)
            assert provider.quote_batches == [("600519", "300750", "600578")]
            assert set(provider.intraday) == {"600519", "300750", "600578"}
            assert len(provider.history) == 6
            with sessions() as session:
                rows = session.scalars(select(MarketSnapshot)).all()
                assert len(rows) == 10
                assert {row.namespace for row in rows} == {"stock_quotes", "stock_intraday", "stock_klines"}
            offline = RecordingProvider()
            offline.error = DataSourceError("offline")
            restarted = StockService(offline, snapshot_sessions=sessions)
            assert (await restarted.get_intraday("300750")).stale
            assert (await restarted.get_kline("300750", "weekly", 120)).stale
            assert (await restarted.get_quote("300750", prefer_cached=True)).data is not None
            assert offline.quote_batches == []
            await restarted.aclose()
        finally:
            await collector.stop()
            await stocks.aclose()
            engine.dispose()
    asyncio.run(run())


def test_cadence_new_members_removal_and_non_watchlist_is_on_demand(tmp_path):
    async def run():
        clock = Clock()
        engine, _, provider, stocks, watch, collector = setup(tmp_path, clock=clock)
        try:
            await settle(collector)
            assert not provider.quote_batches and not provider.intraday and not provider.history
            watch.add("600519")
            clock.now = 2
            await settle(collector)
            assert provider.intraday == ["600519"] and len(provider.history) == 2
            clock.now = 3.9
            await settle(collector)
            assert len(provider.intraday) == 1
            clock.now = 4
            await settle(collector)
            assert len(provider.intraday) == 2 and len(provider.history) == 2
            watch.remove("600519")
            watch.add("300750")
            clock.now = 6
            await settle(collector)
            assert provider.intraday[-1] == "300750" and len(provider.intraday) == 3
            assert len(provider.history) == 4
            assert "600578" not in provider.intraday
            await stocks.get_intraday("600578")
            assert provider.intraday[-1] == "600578"
            clock.now = 66
            await settle(collector)
            assert provider.intraday.count("600519") == 2
            assert len(provider.history) == 6
        finally:
            await collector.stop()
            await stocks.aclose()
            engine.dispose()
    asyncio.run(run())


def test_off_hours_preheat_and_opening_resumes_two_second_target(tmp_path):
    async def run():
        clock = Clock()
        wall = [datetime(2026, 9, 28, 12, tzinfo=BEIJING)]
        engine, _, provider, stocks, watch, collector = setup(tmp_path, clock=clock, wall=lambda: wall[0])
        watch.add("600519")
        try:
            await settle(collector)
            assert len(provider.intraday) == 1
            clock.now = 2
            await settle(collector)
            assert len(provider.intraday) == 1
            watch.add("300750")
            clock.now = 4
            await settle(collector)
            assert provider.intraday == ["600519", "300750"]
            clock.now = 300
            await settle(collector)
            assert provider.intraday.count("600519") == 2
            wall[0] = datetime(2026, 9, 28, 13, tzinfo=BEIJING)
            clock.now = 302
            await settle(collector)
            assert provider.intraday.count("600519") == 3
        finally:
            await collector.stop()
            await stocks.aclose()
            engine.dispose()
    asyncio.run(run())


def test_failed_stock_does_not_block_other_resources(tmp_path):
    async def run():
        engine, _, provider, stocks, watch, collector = setup(tmp_path)
        provider.failed_symbol = "600519"
        watch.add("600519")
        watch.add("300750")
        try:
            await settle(collector)
            assert len(provider.history) == 4 and len(provider.quote_batches) == 1
            assert (await stocks._intraday.peek("300750")) is not None
            assert (await stocks._intraday.peek("600519")) is None
        finally:
            await collector.stop()
            await stocks.aclose()
            engine.dispose()
    asyncio.run(run())


def test_slow_requests_are_bounded_fair_and_shutdown_cancels_loaders(tmp_path):
    async def run():
        started = asyncio.Event()
        release = asyncio.Event()

        class SlowProvider(RecordingProvider):
            active = 0
            peak = 0

            async def get_stock_intraday(self, symbol):
                self.intraday.append(symbol)
                self.active += 1
                self.peak = max(self.peak, self.active)
                if self.active == 2:
                    started.set()
                try:
                    await release.wait()
                    return await FakeProvider.get_stock_intraday(self, symbol)
                finally:
                    self.active -= 1

        clock = Clock()
        engine, _, provider, stocks, watch, collector = setup(tmp_path, SlowProvider(), clock, workers=2)
        for code in ("600519", "300750", "600578"):
            watch.add(code)
        try:
            await collector.poll()
            await asyncio.wait_for(started.wait(), 2)
            clock.now = 2
            await collector.poll()
            assert len(provider.intraday) == 2 and provider.peak == 2
            assert len(collector._pending) <= 5  # One batch + two live + two historical.
            release.set()
            await asyncio.gather(*collector._pending.values())
            await collector.poll()
            await asyncio.gather(*collector._pending.values())
            assert provider.intraday[2] == "600578"  # Unvisited stocks are not starved.
            release.clear()
            started.clear()
            clock.now = 4
            await collector.poll()
            await asyncio.wait_for(started.wait(), 2)
            await collector.stop()
            await stocks.aclose()
            assert provider.active == 0
            assert not collector._pending and not stocks._intraday._pending
        finally:
            release.set()
            await collector.stop()
            await stocks.aclose()
            engine.dispose()
    asyncio.run(run())


def test_lifespan_collects_without_stock_http_requests_and_can_be_disabled(tmp_path):
    database = f"sqlite:///{(tmp_path / 'lifespan.db').as_posix()}"
    with TestClient(create_app(provider=RecordingProvider(), database_url=database, prefetch_enabled=True)) as client:
        collector = client.app.state.watchlist_collector
        assert collector._runner is not None
        assert client.get("/health").status_code == 200
        assert client.post("/api/watchlist", json={"symbol": "600519"}).status_code == 201

        async def wait_for_snapshot():
            # Let the real two-second membership scanner notice the new entry.
            async def ready():
                while await client.app.state.stock_service._intraday.peek("600519") is None:
                    await asyncio.sleep(0.02)
            await asyncio.wait_for(ready(), 4)
        client.portal.call(wait_for_snapshot)
    assert collector._runner is None and not collector._pending
    provider = RecordingProvider()
    with TestClient(create_app(provider=provider, database_url=database, prefetch_enabled=False)) as client:
        assert client.app.state.watchlist_collector is None
        assert client.get("/health").status_code == 200
        assert not provider.intraday and not provider.quote_batches

import asyncio
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.database.models import MarketSnapshot
from app.database.session import create_database_engine, create_session_factory, init_db
from app.main import create_app
from app.models.market import IntradayPoint
from app.providers.exceptions import DataSourceError
from app.services.snapshots import SnapshotStore
from test_api import FakeProvider


def test_market_snapshots_survive_restart_and_are_replaced_by_live_data(tmp_path):
    url = f"sqlite:///{(tmp_path / 'restart.db').as_posix()}"
    paths = [
        "/api/market/indices",
        "/api/market/indices/000001/intraday",
        "/api/stocks/300750/quote",
        "/api/stocks/quotes?codes=600519,000001",
        "/api/stocks/300750/intraday",
        "/api/stocks/300750/kline?period=weekly&limit=5",
    ]
    with TestClient(create_app(provider=FakeProvider(), database_url=url)) as client:
        saved = {}
        for path in paths:
            response = client.get(path)
            assert response.status_code == 200
            payload = response.json()
            assert payload["stale"] is False and payload["cached_at"]
            saved[path] = payload

    offline = FakeProvider()
    offline.error = DataSourceError("offline")
    with TestClient(create_app(provider=offline, database_url=url)) as client:
        for path in paths:
            for _ in range(2):  # Also exercise fallback during failure cooldown.
                response = client.get(path)
                assert response.status_code == 200
                payload = response.json()
                assert payload["stale"] is True
                assert payload["data"] == saved[path]["data"]
                assert payload["cached_at"] == saved[path]["cached_at"]
        # Other symbols, periods and limits must never receive a mismatched snapshot.
        for path in ["/api/stocks/600519/intraday", "/api/stocks/300750/kline?period=daily&limit=5",
                     "/api/stocks/300750/kline?period=weekly&limit=120"]:
            assert client.get(path).status_code == 503

    with TestClient(create_app(provider=FakeProvider(), database_url=url)) as client:
        recovered = client.get(paths[0]).json()
        assert recovered["stale"] is False
        assert recovered["cached_at"] > saved[paths[0]]["cached_at"]


def test_snapshot_age_validation_capacity_and_empty_results(tmp_path):
    async def run():
        engine = create_database_engine(f"sqlite:///{(tmp_path / 'snapshots.db').as_posix()}")
        init_db(engine)
        sessions = create_session_factory(engine)
        now = datetime(2026, 9, 28, 3, 30, tzinfo=timezone.utc)
        store = SnapshotStore(sessions, "test", list[IntradayPoint], maxsize=2, clock=lambda: now)
        data = [IntradayPoint(time=now, price=12.3, volume=100, turnover=1230)]
        try:
            await store.save("a", data)
            now += timedelta(seconds=1)
            await store.save("b", data)
            now += timedelta(seconds=1)
            await store.save("c", data)
            assert await store.read("a") is None
            assert (await store.read("b"))[0][0].price == 12.3
            before = await store.read("c")
            await store.save("c", [])
            assert await store.read("c") == before
            with sessions.begin() as session:
                session.get(MarketSnapshot, ("test", '"b"')).payload = [{"invalid": True}]
            assert await store.read("b") is None
            now += timedelta(days=7, seconds=1)
            assert await store.read("c") is None
            await store.save("d", data)
            with sessions() as session:
                assert session.get(MarketSnapshot, ("test", '"c"')) is None
        finally:
            engine.dispose()

    asyncio.run(run())


def test_snapshot_storage_failure_does_not_hide_successful_provider_data(tmp_path):
    from app.services.cache import AsyncTTLStore

    async def run():
        # Deliberately omit schema initialization to simulate unavailable snapshot storage.
        engine = create_database_engine(f"sqlite:///{(tmp_path / 'missing.db').as_posix()}")
        store = SnapshotStore(create_session_factory(engine), "test", list[IntradayPoint])
        cache = AsyncTTLStore(ttl=1, snapshots=store)
        data = [IntradayPoint(time=datetime.now(timezone.utc), price=12.3, volume=100, turnover=1230)]

        async def load():
            return data

        try:
            assert (await cache.get("a", load)).data == data
            assert await store.read("a") is None
        finally:
            engine.dispose()

    asyncio.run(run())

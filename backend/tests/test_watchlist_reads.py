from contextlib import contextmanager
import asyncio
from datetime import datetime

from fastapi.testclient import TestClient

from app.agent.tools import AgentDependencies, get_stock_quote, get_watchlist
from app.main import create_app
from app.providers.exceptions import DataSourceError
from app.services.collector import BEIJING, WatchlistCollector
from app.services.stock import StockService
from app.services.reads import StockReadService
from test_collector import RecordingProvider
from test_services import Clock


@contextmanager
def application(tmp_path):
    provider = RecordingProvider()
    database = f"sqlite:///{(tmp_path / 'reads.db').as_posix()}"
    with TestClient(create_app(provider=provider, database_url=database, prefetch_enabled=False)) as client:
        clock = Clock()
        stocks = StockService(provider, timer=clock,
                              snapshot_sessions=client.app.state.watchlist_service._session_factory)
        collector = WatchlistCollector(stocks, client.app.state.watchlist_service, timer=clock,
                                       clock=lambda: datetime(2026, 9, 28, 10, tzinfo=BEIJING))
        client.app.state.stock_service = stocks
        client.app.state.watchlist_collector = collector
        try:
            yield client, provider, clock, collector
        finally:
            client.portal.call(collector.stop)


async def warm(collector):
    collector._synced_at = None
    for _ in range(5):
        await collector.poll()
        await asyncio.gather(*collector._pending.values())


def counts(provider):
    return len(provider.quote_batches), len(provider.intraday), len(provider.history)


def test_watchlist_reads_are_cache_only_and_non_watchlist_is_on_demand(tmp_path):
    with application(tmp_path) as (client, provider, clock, collector):
        client.post("/api/watchlist", json={"symbol": "600519"})
        for suffix in ("quote", "intraday", "kline?period=daily&limit=120"):
            payload = client.get(f"/api/stocks/600519/{suffix}").json()
            assert payload["collection_state"] == "warming" and not payload["data"]
        assert counts(provider) == (0, 0, 0)
        client.portal.call(warm, collector)
        before = counts(provider)
        saved = client.get("/api/stocks/600519/quote").json()
        clock.now = 2  # Beyond the original one-second quote TTL.
        for _ in range(3):
            quote = client.get("/api/stocks/600519/quote").json()
            assert quote["cached_at"] == saved["cached_at"] and quote["data"] == saved["data"]
            assert quote["collection_state"] == "ready"
            assert client.get("/api/stocks/600519/intraday").json()["data"]
            assert client.get("/api/stocks/600519/kline?period=daily&limit=5").json()["data"]
            assert client.get("/api/watchlist/status").json()["data"]["items"][0]["state"] == "ready"
        assert counts(provider) == before
        # Requests beyond prefetch coverage and non-members remain explicit on-demand reads.
        assert client.get("/api/stocks/600519/kline?limit=200").status_code == 200
        assert len(provider.history) == before[2] + 1
        assert client.get("/api/stocks/300750/intraday").status_code == 200
        assert len(provider.intraday) == before[1] + 1
        assert client.get("/api/stocks/search?q=test").status_code == 200
        assert len(provider.intraday) == before[1] + 1


def test_partial_failure_and_stale_metadata_without_exposing_errors(tmp_path):
    with application(tmp_path) as (client, provider, clock, collector):
        for symbol in ("600519", "300750"):
            client.post("/api/watchlist", json={"symbol": symbol})
        provider.failed_symbol = "600519"
        client.portal.call(warm, collector)
        overview = client.get("/api/watchlist/status").json()["data"]
        assert overview["enabled"]
        first, second = overview["items"]
        assert first["state"] == "partial" and first["resources"]["intraday"]["state"] == "unavailable"
        assert second["state"] == "ready"
        assert "unavailable" not in str(first["resources"]["quote"])
        saved = client.get("/api/stocks/300750/quote").json()
        clock.now = 2
        provider.error = DataSourceError("private upstream exception")
        client.portal.call(warm, collector)
        response = client.get("/api/stocks/300750/quote")
        assert response.json()["stale"] and response.json()["collection_state"] == "stale"
        assert response.json()["cached_at"] == saved["cached_at"]
        assert response.json()["data"] == saved["data"]
        assert "private" not in client.get("/api/watchlist/status").text
        assert client.get("/api/stocks/600519/intraday").json()["collection_state"] == "unavailable"
        # A new member does not erase the available cached quotes of existing members.
        client.post("/api/watchlist", json={"symbol": "600578"})
        before = counts(provider)
        partial = client.get("/api/watchlist/quotes").json()
        assert partial["collection_state"] == "partial"
        assert [row["symbol"] for row in partial["data"]] == ["600519", "300750"]
        assert counts(provider) == before


def test_combines_multiple_batches_latest_per_stock_and_tools_reuse_them(tmp_path):
    with application(tmp_path) as (client, provider, _, collector):
        for symbol in ("600519", "300750", "600578"):
            client.post("/api/watchlist", json={"symbol": symbol})
        stocks = client.app.state.stock_service

        async def seed():
            await stocks.get_quotes(["600519", "300750"])
            await stocks.get_quotes(["600578"])
        client.portal.call(seed)
        before = counts(provider)
        payload = client.get("/api/watchlist/quotes").json()
        assert [q["symbol"] for q in payload["data"]] == ["600519", "300750", "600578"]
        assert payload["collection_state"] == "ready"
        deps = AgentDependencies(stocks, client.app.state.market_service, client.app.state.watchlist_service)
        result = client.portal.call(get_watchlist, deps)
        assert len(result["quotes"]) == 3
        one = client.portal.call(get_stock_quote, deps, "600578")
        assert one["quote"]["symbol"] == "600578"
        assert counts(provider) == before


def test_refresh_is_scheduled_does_not_reset_backoff_and_removal_is_immediate(tmp_path):
    with application(tmp_path) as (client, provider, clock, collector):
        for symbol in ("600519", "300750"):
            client.post("/api/watchlist", json={"symbol": symbol})
        client.portal.call(warm, collector)
        clock.now = 2
        provider.error = DataSourceError("offline")
        client.portal.call(warm, collector)
        stocks = client.app.state.stock_service
        retry_at = stocks._intraday._failures["600519"].retry_at
        before = counts(provider)
        assert client.post("/api/watchlist/600519/refresh").status_code == 202
        assert ("daily", "300750") in collector._attempted
        assert ("daily", "600519") not in collector._attempted
        assert stocks._intraday._failures["600519"].retry_at == retry_at
        assert counts(provider) == before
        client.portal.call(warm, collector)
        assert len(provider.intraday) == before[1]  # Cooldown still blocks a new upstream call.
        assert client.delete("/api/watchlist/600519").status_code == 204
        assert client.post("/api/watchlist/600519/refresh").status_code == 404
        provider.error = None
        clock.now = 10
        assert client.get("/api/stocks/600519/intraday").json()["collection_state"] is None
        assert provider.intraday[-1] == "600519"
        client.app.state.watchlist_collector = None
        assert client.post("/api/watchlist/refresh").status_code == 409
        assert client.get("/api/watchlist/status").json()["data"]["enabled"] is False


def test_watchlist_larger_than_fifty_supports_cached_partial_response(tmp_path):
    with application(tmp_path) as (client, provider, _, _):
        codes = [f"600{index:03d}" for index in range(1, 52)]
        for code in codes:
            client.post("/api/watchlist", json={"symbol": code})
        stocks = client.app.state.stock_service

        async def seed():
            await stocks.get_quotes(codes[:50])
            await stocks.get_quotes(codes[50:])
        client.portal.call(seed)
        before = counts(provider)
        response = client.get("/api/watchlist/quotes")
        assert response.status_code == 200
        assert [q["symbol"] for q in response.json()["data"]] == codes
        assert counts(provider) == before


def test_restart_reads_persisted_cache_without_provider_requests(tmp_path):
    database = f"sqlite:///{(tmp_path / 'reads.db').as_posix()}"
    with application(tmp_path) as (client, _, _, collector):
        client.post("/api/watchlist", json={"symbol": "600519"})
        client.portal.call(warm, collector)
        original = client.get("/api/stocks/600519/intraday").json()
    provider = RecordingProvider()
    provider.error = DataSourceError("offline")
    with TestClient(create_app(provider=provider, database_url=database, prefetch_enabled=False)) as client:
        client.app.state.watchlist_collector = WatchlistCollector(client.app.state.stock_service, client.app.state.watchlist_service)
        before = counts(provider)
        response = client.get("/api/stocks/600519/intraday").json()
        assert response["data"] == original["data"] and response["cached_at"] == original["cached_at"]
        assert counts(provider) == before


def test_tools_share_background_state_and_timestamps_including_cold_start(tmp_path):
    with application(tmp_path) as (client, provider, clock, collector):
        client.post("/api/watchlist", json={"symbol": "600519"})
        stocks = client.app.state.stock_service
        deps = AgentDependencies(stocks, client.app.state.market_service, client.app.state.watchlist_service,
                                 reader=StockReadService(stocks, client.app.state.watchlist_service, collector))
        cold = client.portal.call(get_stock_quote, deps, "600519")
        assert not cold["found"] and cold["collection_state"] == "warming"
        assert counts(provider) == (0, 0, 0)
        client.portal.call(warm, collector)
        clock.now = 2
        before = counts(provider)
        quote = client.portal.call(get_stock_quote, deps, "600519")
        page = client.get("/api/stocks/600519/quote").json()
        assert quote["stale"] == page["stale"] and quote["collection_state"] == page["collection_state"]
        assert datetime.fromisoformat(quote["cached_at"]) == datetime.fromisoformat(page["cached_at"])
        assert quote["quote"] == page["data"]
        assert counts(provider) == before

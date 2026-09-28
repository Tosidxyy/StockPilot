from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.models.market import IntradayPoint, KlineItem, MarketIndex, StockQuote, SymbolSearchResult
from app.providers.base import MarketDataProvider
from app.providers.exceptions import DataSourceError, ProviderTimeoutError
from app.services.stock import StockService


class FakeProvider(MarketDataProvider):
    def __init__(self) -> None:
        self.quote_batches: list[tuple[str, ...]] = []
        self.periods: list[str] = []
        self.error: Exception | None = None

    def _check(self) -> None:
        if self.error is not None:
            raise self.error

    async def search_stocks(self, query: str, limit: int = 20) -> list[SymbolSearchResult]:
        self._check()
        return [SymbolSearchResult(symbol="600519", name="贵州茅台", market="SH")]

    async def get_quotes(self, symbols) -> list[StockQuote]:
        self.quote_batches.append(tuple(symbols))
        self._check()
        return [StockQuote(
            symbol=symbol, name="测试股票", price=12.3, change_percent=1.0,
            change_amount=0.12, volume=100, turnover=1230.0, high=12.4,
            low=12.1, open=12.2, previous_close=12.18, turnover_rate=0.1,
            pe_ratio=10.0,
        ) for symbol in symbols if symbol != "600000"]

    async def get_indices(self) -> list[MarketIndex]:
        self._check()
        return [MarketIndex(symbol="000001", name="上证指数", value=3000.0,
                            change_percent=1.0, change_amount=30.0, volume=1, turnover=2.0)]

    async def get_index_intraday(self, index_code="000001") -> list[IntradayPoint]:
        self._check()
        return [IntradayPoint(time=datetime(2026, 9, 25, 9, 30), price=3000.0,
                              volume=100, turnover=2000.0)]

    async def get_stock_intraday(self, symbol) -> list[IntradayPoint]:
        self._check()
        return [IntradayPoint(time=datetime(2026, 9, 28, 9, 30), price=12.3,
                              volume=100, turnover=1230.0)]

    async def get_kline(self, symbol: str, period="daily", limit=120) -> list[KlineItem]:
        self.periods.append(period)
        self._check()
        return [KlineItem(date=date(2026, 9, 25), open=12.0, close=12.3,
                          high=12.4, low=11.9, volume=100, turnover=1230.0)]


@pytest.fixture
def api(tmp_path):
    provider = FakeProvider()
    url = f"sqlite:///{(tmp_path / 'api.db').as_posix()}"
    with TestClient(create_app(provider=provider, database_url=url)) as client:
        yield client, provider, url


def test_market_indices_and_overview(api) -> None:
    client, _, _ = api
    assert client.get("/health").json() == {"status": "ok"}
    indices = client.get("/api/market/indices")
    assert indices.status_code == 200
    assert indices.json()["data"][0]["value"] == 3000.0
    assert indices.json()["stale"] is False
    intraday = client.get("/api/market/indices/000001/intraday")
    assert intraday.status_code == 200
    assert intraday.json()["data"][0]["time"] == "2026-09-25T09:30:00"
    assert client.get("/api/market/indices/123456/intraday").status_code == 422
    assert client.post("/api/watchlist", json={"symbol": "600519"}).status_code == 201
    overview = client.get("/api/market/overview")
    assert overview.status_code == 200
    assert overview.json()["watchlist_count"] == 1
    assert overview.json()["indices"][0]["symbol"] == "000001"


def test_local_frontend_cors_preflight(api) -> None:
    client, _, _ = api
    response = client.options(
        "/api/watchlist",
        headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "POST"},
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_stock_intraday_api_and_failure(api) -> None:
    client, provider, _ = api
    response = client.get("/api/stocks/300750/intraday")
    assert response.status_code == 200
    assert response.json()["data"][0]["price"] == 12.3
    assert response.json()["stale"] is False
    assert client.get("/api/stocks/bad/intraday").status_code == 422
    provider.error = DataSourceError("private upstream error")
    failed = client.get("/api/stocks/600519/intraday")
    assert failed.status_code == 503
    assert "private" not in failed.text


def test_stock_search_quote_batch_and_kline(api) -> None:
    client, provider, _ = api
    search = client.get("/api/stocks/search", params={"q": "茅台"})
    assert search.status_code == 200
    assert search.json()["data"][0] == {"symbol": "600519", "name": "贵州茅台", "market": "SH"}
    batch = client.get("/api/stocks/quotes", params={"codes": "600519,000001,600519"})
    assert batch.status_code == 200
    assert [item["symbol"] for item in batch.json()["data"]] == ["600519", "000001"]
    assert provider.quote_batches == [("600519", "000001")]
    quote = client.get("/api/stocks/600519/quote")
    assert quote.status_code == 200
    assert quote.json()["data"]["price"] == 12.3
    assert not any(key.startswith("f") and key[1:].isdigit() for key in quote.json()["data"])
    assert client.get("/api/stocks/600000/quote").status_code == 404
    kline = client.get("/api/stocks/600519/kline", params={"period": "weekly", "limit": 2})
    assert kline.status_code == 200
    assert kline.json()["data"][0]["date"] == "2026-09-25"
    assert provider.periods == ["weekly"]


def test_validation_rejects_bad_symbols_and_parameters(api) -> None:
    client, _, _ = api
    assert client.get("/api/stocks/search").status_code == 422
    assert client.get("/api/stocks/quotes", params={"codes": "600519,bad"}).status_code == 422
    assert client.get("/api/stocks/bad/quote").status_code == 422
    assert client.get("/api/stocks/600519/kline", params={"period": "monthly"}).status_code == 422
    assert client.get("/api/stocks/600519/kline", params={"limit": 0}).status_code == 422
    assert client.post("/api/watchlist", json={"symbol": "bad"}).status_code == 422


def test_watchlist_crud_and_database_persistence(api) -> None:
    client, provider, url = api
    assert client.get("/api/watchlist").json()["data"] == []
    assert client.post("/api/watchlist", json={"symbol": "600519"}).status_code == 201
    assert client.post("/api/watchlist", json={"symbol": "600519"}).status_code == 201
    assert [item["symbol"] for item in client.get("/api/watchlist").json()["data"]] == ["600519"]
    with TestClient(create_app(provider=provider, database_url=url)) as second:
        assert [item["symbol"] for item in second.get("/api/watchlist").json()["data"]] == ["600519"]
    assert client.delete("/api/watchlist/600519").status_code == 204
    assert client.delete("/api/watchlist/600519").status_code == 404
    assert client.get("/api/watchlist").json()["data"] == []


def test_provider_errors_map_to_503_and_504(api) -> None:
    client, provider, _ = api
    provider.error = DataSourceError("secret upstream detail")
    unavailable = client.get("/api/market/indices")
    assert unavailable.status_code == 503
    assert "secret" not in unavailable.text
    provider.error = ProviderTimeoutError("late")
    assert client.get("/api/stocks/600519/quote").status_code == 504


def test_expired_quote_cache_returns_stale_flag(api) -> None:
    client, provider, _ = api
    now = [0.0]
    client.app.state.stock_service = StockService(
        provider, quote_ttl=1, stale_ttl=3600, timer=lambda: now[0]
    )
    assert client.get("/api/stocks/600519/quote").json()["stale"] is False
    now[0] = 2.0
    provider.error = DataSourceError("offline")
    stale = client.get("/api/stocks/600519/quote")
    assert stale.status_code == 200
    assert stale.json()["stale"] is True
    assert stale.json()["data"]["symbol"] == "600519"

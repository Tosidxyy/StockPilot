"""Keep live data on EastMoney and fall back independently for historical candles."""

from typing import Literal, Sequence

from app.models.market import IntradayPoint, KlineItem, MarketIndex, StockQuote, SymbolSearchResult
from app.providers.base import MarketDataProvider
from app.providers.eastmoney import EastMoneyProvider
from app.providers.exceptions import DataSourceError
from app.providers.history import TencentHistory


class ResilientMarketProvider(MarketDataProvider):
    async def get_stock_announcements(self, symbol: str):
        return await self._primary.get_stock_announcements(symbol)

    async def get_announcement_content(self, item, previous=None):
        return await self._primary.get_announcement_content(item, previous)

    async def get_stock_news(self, symbol: str):
        return await self._primary.get_stock_news(symbol)

    async def get_market_news(self):
        return await self._primary.get_market_news()

    def __init__(
        self, primary: MarketDataProvider | None = None, history: TencentHistory | None = None,
    ) -> None:
        self._primary = primary if primary is not None else EastMoneyProvider()
        self._history = history if history is not None else TencentHistory()

    async def aclose(self) -> None:
        try:
            await self._primary.aclose()
        finally:
            await self._history.aclose()

    async def search_stocks(self, query: str, limit: int = 20) -> list[SymbolSearchResult]:
        return await self._primary.search_stocks(query, limit)

    async def get_quotes(self, symbols: Sequence[str]) -> list[StockQuote]:
        return await self._primary.get_quotes(symbols)

    async def get_indices(self) -> list[MarketIndex]:
        return await self._primary.get_indices()

    async def get_index_intraday(self, index_code: str = "000001") -> list[IntradayPoint]:
        return await self._primary.get_index_intraday(index_code)

    async def get_stock_intraday(self, symbol: str) -> list[IntradayPoint]:
        return await self._primary.get_stock_intraday(symbol)

    async def get_kline(
        self, symbol: str, period: Literal["daily", "weekly"] = "daily", limit: int = 120,
    ) -> list[KlineItem]:
        try:
            return await self._primary.get_kline(symbol, period, limit)
        except DataSourceError:
            return await self._history.get_kline(symbol, period, limit)

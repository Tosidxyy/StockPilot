"""Public quote failover and Tencent-first minutes, with successful cache upstream."""

from typing import Literal, Sequence
from time import monotonic
from cachetools import TTLCache

from app.models.market import IntradayPoint, KlineItem, MarketIndex, StockQuote, SymbolSearchResult
from app.providers.base import MarketDataProvider
from app.providers.eastmoney import EastMoneyProvider
from app.providers.exceptions import DataSourceError
from app.providers.history import TencentHistory
from app.providers.intraday import TencentIntraday
from app.providers.quotes import PublicQuotes


class ResilientMarketProvider(MarketDataProvider):
    async def get_market_breadth(self):
        return await self._primary.get_market_breadth()

    async def get_stock_money_flow(self, symbol: str):
        return await self._primary.get_stock_money_flow(symbol)

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
        intraday: TencentIntraday | None = None, *, timer=monotonic, quote_sources=None,
    ) -> None:
        self._primary = primary if primary is not None else EastMoneyProvider()
        self._history = history if history is not None else TencentHistory()
        self._intraday = intraday if intraday is not None else TencentIntraday()
        # Do not pay the failed preferred minute source's timeout every refresh.
        # Separate symbols and indices so an individual failure does not disable others.
        self._intraday_retry = TTLCache(maxsize=2048, ttl=30, timer=timer)
        self._quote_sources = quote_sources if quote_sources is not None else (
            [PublicQuotes("tencent"), PublicQuotes("sina"), self._primary] if primary is None else [self._primary])
        self._quote_retry = TTLCache(maxsize=8, ttl=30, timer=timer)

    async def aclose(self) -> None:
        for source in self._quote_sources:
            if source is not self._primary:
                await source.aclose()
        try:
            await self._primary.aclose()
        finally:
            try:
                await self._history.aclose()
            finally:
                await self._intraday.aclose()

    async def search_stocks(self, query: str, limit: int = 20) -> list[SymbolSearchResult]:
        return await self._primary.search_stocks(query, limit)

    async def get_quotes(self, symbols: Sequence[str]) -> list[StockQuote]:
        unique = list(dict.fromkeys(symbols))
        if len(unique) > 50:
            rows = []
            for offset in range(0, len(unique), 50):
                rows.extend(await self.get_quotes(unique[offset:offset+50]))
            return rows
        found = {}
        last_error = None
        for position, source in enumerate(self._quote_sources):
            missing = [symbol for symbol in unique if symbol not in found]
            if not missing:
                break
            if position in self._quote_retry:
                continue
            try:
                rows = await source.get_quotes(missing)
                found.update({row.symbol: row for row in rows if row.symbol in missing})
            except DataSourceError as error:
                self._quote_retry[position] = True
                last_error = error
        if not found and unique and last_error:
            raise last_error
        if not found and unique and len(self._quote_retry) == len(self._quote_sources):
            raise DataSourceError("All quote sources temporarily unavailable")
        return [found[symbol] for symbol in unique if symbol in found]

    async def get_indices(self) -> list[MarketIndex]:
        return await self._primary.get_indices()

    async def get_index_intraday(self, index_code: str = "000001") -> list[IntradayPoint]:
        return await self._minutes("index", index_code)

    async def get_stock_intraday(self, symbol: str) -> list[IntradayPoint]:
        return await self._minutes("stock", symbol)

    async def _minutes(self, kind, code):
        key = (kind, code)
        if key not in self._intraday_retry:
            try:
                points = await (self._intraday.get_stock_intraday(code) if kind == "stock" else self._intraday.get_index_intraday(code))
                if not points:
                    raise DataSourceError("Preferred minute source returned no points")
                return points
            except DataSourceError:
                self._intraday_retry[key] = True
        points = await (self._primary.get_stock_intraday(code) if kind == "stock" else self._primary.get_index_intraday(code))
        if not points:
            raise DataSourceError("Fallback minute source returned no points")
        return points

    async def get_kline(
        self, symbol: str, period: Literal["daily", "weekly"] = "daily", limit: int = 120,
    ) -> list[KlineItem]:
        try:
            return await self._primary.get_kline(symbol, period, limit)
        except DataSourceError:
            return await self._history.get_kline(symbol, period, limit)

"""Public quote failover and measured Sina-first minutes, with cached fallback."""

from typing import Literal, Sequence
from time import monotonic
from math import isfinite
from cachetools import TTLCache

from app.models.market import IntradayPoint, KlineItem, MarketIndex, StockQuote, SymbolSearchResult
from app.providers.base import MarketDataProvider
from app.providers.eastmoney import EastMoneyProvider
from app.providers.exceptions import DataSourceError
from app.providers.history import TencentHistory
from app.providers.intraday import TencentIntraday
from app.providers.quotes import INDEX_CODES, PublicQuotes
from app.providers.sina_intraday import SinaIntraday


class ResilientMarketProvider(MarketDataProvider):
    async def get_stock_comments(self, symbol: str):
        return await self._primary.get_stock_comments(symbol)

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
        intraday: TencentIntraday | None = None, *, timer=monotonic, quote_sources=None, minute_sources=None,
        index_sources=None,
    ) -> None:
        self._primary = primary if primary is not None else EastMoneyProvider()
        self._history = history if history is not None else TencentHistory()
        self._intraday = intraday if intraday is not None else TencentIntraday()
        # Default order reflects the current live checks. Explicit injections
        # retain the old two-source contract for isolated tests/custom providers.
        self._minute_sources = minute_sources if minute_sources is not None else (
            [SinaIntraday(), self._intraday, self._primary] if primary is None and intraday is None
            else [self._intraday, self._primary])
        # Do not pay the failed preferred minute source's timeout every refresh.
        # Separate symbols and indices so an individual failure does not disable others.
        self._intraday_retry = TTLCache(maxsize=2048, ttl=30, timer=timer)
        self._quote_sources = quote_sources if quote_sources is not None else (
            [PublicQuotes("tencent"), PublicQuotes("sina"), self._primary] if primary is None else [self._primary])
        self._quote_retry = TTLCache(maxsize=8, ttl=30, timer=timer)
        self._index_sources = index_sources if index_sources is not None else (
            self._quote_sources if primary is None else [self._primary])
        # Index failures must not disable stock quotes, or vice versa.
        self._index_retry = TTLCache(maxsize=8, ttl=30, timer=timer)

    async def aclose(self) -> None:
        for source in self._minute_sources:
            if source is not self._primary and source is not self._intraday:
                await source.aclose()
        for source in self._quote_sources:
            if source is not self._primary:
                await source.aclose()
        for source in self._index_sources:
            if source is not self._primary and all(source is not other for other in self._quote_sources + self._minute_sources):
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
        last_error = None
        for position, source in enumerate(self._index_sources):
            if position in self._index_retry:
                continue
            try:
                rows = await source.get_indices()
                found = {row.symbol: row for row in rows}
                if len(rows) != 3 or set(found) != set(INDEX_CODES.values()) or any(
                    row.value is None or not isfinite(row.value) or row.value <= 0 for row in rows
                ):
                    raise DataSourceError("Index source returned incomplete or invalid indices")
                return [found[symbol] for symbol in INDEX_CODES.values()]
            except DataSourceError as error:
                self._index_retry[position] = True
                last_error = error
        raise last_error or DataSourceError("All index sources temporarily unavailable")

    async def get_index_intraday(self, index_code: str = "000001") -> list[IntradayPoint]:
        return await self._minutes("index", index_code)

    async def get_stock_intraday(self, symbol: str) -> list[IntradayPoint]:
        return await self._minutes("stock", symbol)

    async def _minutes(self, kind, code):
        last_error = None
        for position, source in enumerate(self._minute_sources):
            key = (kind, code, position)
            if key in self._intraday_retry:
                continue
            try:
                points = await (source.get_stock_intraday(code) if kind == "stock" else source.get_index_intraday(code))
                if not points:
                    raise DataSourceError("Minute source returned no points")
                return points
            except DataSourceError as error:
                self._intraday_retry[key] = True
                last_error = error
        raise last_error or DataSourceError("All minute sources temporarily unavailable")

    async def get_kline(
        self, symbol: str, period: Literal["daily", "weekly"] = "daily", limit: int = 120,
    ) -> list[KlineItem]:
        try:
            return await self._primary.get_kline(symbol, period, limit)
        except DataSourceError:
            return await self._history.get_kline(symbol, period, limit)

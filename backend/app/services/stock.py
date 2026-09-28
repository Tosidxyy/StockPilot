"""Stock search, batched quotes and K-line access."""

from collections.abc import Callable, Sequence
from typing import Literal
from sqlalchemy.orm import Session, sessionmaker

from app.models.market import IntradayPoint, KlineItem, StockQuote, SymbolSearchResult
from app.providers.base import MarketDataProvider
from app.services.cache import AsyncTTLStore, CachedResult
from app.services.snapshots import SnapshotStore


class StockService:
    def __init__(
        self,
        provider: MarketDataProvider,
        *,
        quote_ttl: float = 1,
        intraday_ttl: float = 1,
        kline_ttl: float = 60,
        search_ttl: float = 300,
        stale_ttl: float = 3600,
        timer: Callable[[], float] | None = None,
        snapshot_sessions: sessionmaker[Session] | None = None,
    ) -> None:
        self._provider = provider
        self._intraday: AsyncTTLStore[list[IntradayPoint]] = AsyncTTLStore(
            ttl=intraday_ttl, stale_ttl=stale_ttl, timer=timer,
            snapshots=SnapshotStore(snapshot_sessions, "stock_intraday", list[IntradayPoint]) if snapshot_sessions else None,
        )
        self._quotes: AsyncTTLStore[list[StockQuote]] = AsyncTTLStore(
            ttl=quote_ttl, stale_ttl=stale_ttl, timer=timer,
            snapshots=SnapshotStore(snapshot_sessions, "stock_quotes", list[StockQuote]) if snapshot_sessions else None,
        )
        self._klines: AsyncTTLStore[list[KlineItem]] = AsyncTTLStore(
            ttl=kline_ttl, stale_ttl=stale_ttl, timer=timer,
            snapshots=SnapshotStore(snapshot_sessions, "stock_klines", list[KlineItem]) if snapshot_sessions else None,
        )
        self._search: AsyncTTLStore[list[SymbolSearchResult]] = AsyncTTLStore(
            ttl=search_ttl, stale_ttl=stale_ttl, timer=timer
        )

    async def search(self, query: str, limit: int = 20) -> CachedResult[list[SymbolSearchResult]]:
        normalized = query.strip()
        return await self._search.get(
            (normalized, limit), lambda: self._provider.search_stocks(normalized, limit)
        )

    async def aclose(self) -> None:
        for store in (self._quotes, self._intraday, self._klines, self._search):
            await store.aclose()

    async def get_quotes(self, symbols: Sequence[str], *, prefer_cached: bool = False) -> CachedResult[list[StockQuote]]:
        unique = tuple(dict.fromkeys(symbols))
        if not unique:
            return CachedResult([])
        if prefer_cached:
            cached = await self.cached_quotes(unique)
            if all(symbol in cached for symbol in unique):
                return CachedResult([cached[symbol].data for symbol in unique],
                                    stale=any(cached[symbol].stale for symbol in unique),
                                    cached_at=min(cached[symbol].cached_at for symbol in unique))
        return await self._quotes.get(unique, lambda: self._provider.get_quotes(unique))

    async def get_quote(self, symbol: str, *, prefer_cached: bool = False) -> CachedResult[StockQuote | None]:
        result = await self.get_quotes([symbol], prefer_cached=prefer_cached)
        return CachedResult(result.data[0] if result.data else None, stale=result.stale, cached_at=result.cached_at)

    async def get_kline(
        self, symbol: str, period: Literal["daily", "weekly"] = "daily", limit: int = 120,
        *, prefer_cached: bool = False,
    ) -> CachedResult[list[KlineItem]]:
        if prefer_cached:
            cached = await self.cached_kline(symbol, period, limit)
            if cached is not None:
                return CachedResult(cached.data[-limit:], stale=cached.stale, cached_at=cached.cached_at)
        return await self._klines.get(
            (symbol, period, limit), lambda: self._provider.get_kline(symbol, period, limit)
        )

    async def cached_quotes(self, symbols: Sequence[str]) -> dict[str, CachedResult[StockQuote]]:
        wanted = set(symbols)
        snapshots = await self._quotes.peek_all(lambda key: (
            isinstance(key, (tuple, list)) and bool(wanted.intersection(key))
        ))
        result = {}
        for _, cached in snapshots:
            for quote in cached.data:
                previous = result.get(quote.symbol)
                if quote.symbol in wanted and (previous is None or cached.cached_at > previous.cached_at):
                    result[quote.symbol] = CachedResult(quote, stale=cached.stale, cached_at=cached.cached_at)
        return result

    async def cached_kline(self, symbol: str, period: str, limit: int):
        cached = await self._klines.peek((symbol, period, limit), matches=lambda key: (
            isinstance(key, (tuple, list)) and len(key) == 3 and key[0] == symbol
            and key[1] == period and isinstance(key[2], int) and key[2] >= limit
        ))
        return CachedResult(cached.data[-limit:], stale=cached.stale, cached_at=cached.cached_at) if cached else None

    async def cached_resources(self, symbols: Sequence[str]) -> dict[tuple[str, str], CachedResult]:
        wanted = set(symbols)
        result = {(symbol, "quote"): value for symbol, value in (await self.cached_quotes(symbols)).items()}
        for key, cached in await self._intraday.peek_all(lambda key: isinstance(key, str) and key in wanted):
            result[(key, "intraday")] = cached
        for key, cached in await self._klines.peek_all(lambda key: (
            isinstance(key, (tuple, list)) and len(key) == 3 and key[0] in wanted
            and key[1] in ("daily", "weekly") and isinstance(key[2], int) and key[2] >= 120
        )):
            identity = (key[0], key[1])
            previous = result.get(identity)
            if previous is None or cached.cached_at > previous.cached_at:
                result[identity] = cached
        return result

    async def get_intraday(self, symbol: str, *, prefer_cached: bool = False) -> CachedResult[list[IntradayPoint]]:
        if prefer_cached:
            cached = await self.cached_intraday(symbol)
            if cached is not None:
                return cached
        return await self._intraday.get(symbol, lambda: self._provider.get_stock_intraday(symbol))

    async def cached_intraday(self, symbol: str):
        return await self._intraday.peek(symbol)

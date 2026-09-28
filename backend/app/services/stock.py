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

    async def get_quotes(self, symbols: Sequence[str], *, prefer_cached: bool = False) -> CachedResult[list[StockQuote]]:
        unique = tuple(dict.fromkeys(symbols))
        if not unique:
            return CachedResult([])
        if prefer_cached:
            cached = await self._quotes.peek(unique, matches=lambda key: (
                isinstance(key, (tuple, list)) and set(unique).issubset(key)
            ), accepts=lambda data: set(unique).issubset(item.symbol for item in data))
            if cached is not None:
                by_symbol = {item.symbol: item for item in cached.data}
                if all(symbol in by_symbol for symbol in unique):
                    return CachedResult([by_symbol[symbol] for symbol in unique], stale=cached.stale,
                                        cached_at=cached.cached_at)
        return await self._quotes.get(unique, lambda: self._provider.get_quotes(unique))

    async def get_quote(self, symbol: str, *, prefer_cached: bool = False) -> CachedResult[StockQuote | None]:
        result = await self.get_quotes([symbol], prefer_cached=prefer_cached)
        return CachedResult(result.data[0] if result.data else None, stale=result.stale, cached_at=result.cached_at)

    async def get_kline(
        self, symbol: str, period: Literal["daily", "weekly"] = "daily", limit: int = 120,
        *, prefer_cached: bool = False,
    ) -> CachedResult[list[KlineItem]]:
        if prefer_cached:
            cached = await self._klines.peek((symbol, period, limit), matches=lambda key: (
                isinstance(key, (tuple, list)) and len(key) == 3
                and key[0] == symbol and key[1] == period
                and isinstance(key[2], int) and key[2] >= limit
            ))
            if cached is not None:
                return CachedResult(cached.data[-limit:], stale=cached.stale, cached_at=cached.cached_at)
        return await self._klines.get(
            (symbol, period, limit), lambda: self._provider.get_kline(symbol, period, limit)
        )

    async def get_intraday(self, symbol: str) -> CachedResult[list[IntradayPoint]]:
        return await self._intraday.get(symbol, lambda: self._provider.get_stock_intraday(symbol))

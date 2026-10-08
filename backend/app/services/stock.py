"""Stock search, batched quotes and K-line access."""

from collections.abc import Callable, Sequence
from typing import Literal
from dataclasses import dataclass
from sqlalchemy.orm import Session, sessionmaker

from app.models.market import IntradayPoint, KlineItem, StockQuote, SymbolSearchResult
from app.providers.base import MarketDataProvider
from app.services.cache import AsyncTTLStore, CachedResult
from app.services.snapshots import SnapshotStore
from app.providers.exceptions import DataSourceError


@dataclass(frozen=True)
class SeriesMetadata:
    source: str


def series_metadata(data):
    return [SeriesMetadata(data[-1].source)] if data else []


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

    async def get_quotes(self, symbols: Sequence[str], *, prefer_cached: bool = False,
                         revalidate: bool = False) -> CachedResult[list[StockQuote]]:
        unique = tuple(dict.fromkeys(symbols))
        if not unique:
            return CachedResult([])
        if prefer_cached:
            cached = await self.cached_quotes(unique)
            if all(symbol in cached for symbol in unique):
                return CachedResult([cached[symbol].data for symbol in unique],
                                    stale=any(cached[symbol].stale for symbol in unique),
                                    cached_at=min(cached[symbol].cached_at for symbol in unique))
        try:
            return await self._quotes.get(unique, lambda: self._provider.get_quotes(unique), revalidate=revalidate)
        except DataSourceError:
            # A collector saves batches, while a detail page may request just one
            # symbol after restart. Preserve the newest covering successful rows.
            cached = await self.cached_quotes(unique)
            if not all(symbol in cached for symbol in unique):
                raise
            return CachedResult([cached[symbol].data for symbol in unique], stale=True,
                cached_at=min(cached[symbol].cached_at for symbol in unique))

    async def get_quote(self, symbol: str, *, prefer_cached: bool = False) -> CachedResult[StockQuote | None]:
        result = await self.get_quotes([symbol], prefer_cached=prefer_cached)
        return CachedResult(result.data[0] if result.data else None, stale=result.stale, cached_at=result.cached_at)

    async def get_kline(
        self, symbol: str, period: Literal["daily", "weekly"] = "daily", limit: int = 120,
        *, prefer_cached: bool = False, revalidate: bool = False,
    ) -> CachedResult[list[KlineItem]]:
        if prefer_cached:
            cached = await self.cached_kline(symbol, period, limit)
            if cached is not None:
                return CachedResult(cached.data[-limit:], stale=cached.stale, cached_at=cached.cached_at)
        return await self._klines.get(
            (symbol, period, limit), lambda: self._provider.get_kline(symbol, period, limit), revalidate=revalidate
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
                    failed = self._quotes.failed_since(cached.cached_at, lambda key: (
                        isinstance(key, (tuple, list)) and quote.symbol in key
                    ))
                    result[quote.symbol] = CachedResult(quote, stale=cached.stale or failed, cached_at=cached.cached_at)
        return result

    async def cached_kline(self, symbol: str, period: str, limit: int):
        cached = await self._klines.peek((symbol, period, limit), matches=lambda key: (
            isinstance(key, (tuple, list)) and len(key) == 3 and key[0] == symbol
            and key[1] == period and isinstance(key[2], int) and key[2] >= limit
        ))
        return CachedResult(cached.data[-limit:], stale=cached.stale, cached_at=cached.cached_at) if cached else None

    async def cached_resources(self, symbols: Sequence[str], *, metadata_only: bool = False) -> dict[tuple[str, str], CachedResult]:
        wanted = set(symbols)
        result = {(symbol, "quote"): value for symbol, value in (await self.cached_quotes(symbols)).items()}
        project = series_metadata if metadata_only else None
        intraday = await self._intraday.peek_all(lambda key: isinstance(key, str) and key in wanted,
                                               prefer_memory=metadata_only, project=project)
        missing = wanted - {key for key, _ in intraday}
        if metadata_only and missing:
            intraday.extend(await self._intraday.peek_all(lambda key: isinstance(key, str) and key in missing,
                                                         prefer_memory=True, project=project))
        for key, cached in intraday:
            result[(key, "intraday")] = cached
        def matches(key):
            return (
            isinstance(key, (tuple, list)) and len(key) == 3 and key[0] in wanted
            and key[1] in ("daily", "weekly") and isinstance(key[2], int) and key[2] >= 120
            )
        klines = await self._klines.peek_all(matches, prefer_memory=metadata_only, project=project)
        missing = {(symbol, period) for symbol in wanted for period in ("daily", "weekly")} - {
            (key[0], key[1]) for key, _ in klines}
        if metadata_only and missing:
            klines.extend(await self._klines.peek_all(lambda key: matches(key) and (key[0], key[1]) in missing,
                                                     prefer_memory=True, project=project))
        for key, cached in klines:
            identity = (key[0], key[1])
            previous = result.get(identity)
            if previous is None or cached.cached_at > previous.cached_at:
                result[identity] = cached
        return result

    async def get_intraday(self, symbol: str, *, prefer_cached: bool = False,
                           revalidate: bool = False) -> CachedResult[list[IntradayPoint]]:
        if prefer_cached:
            cached = await self.cached_intraday(symbol)
            if cached is not None:
                return cached
        return await self._intraday.get(symbol, lambda: self._provider.get_stock_intraday(symbol), revalidate=revalidate)

    async def cached_intraday(self, symbol: str):
        return await self._intraday.peek(symbol)

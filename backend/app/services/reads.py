"""Choose background snapshots for watchlist pages and on-demand reads elsewhere."""

from dataclasses import dataclass
from typing import Generic, TypeVar

from starlette.concurrency import run_in_threadpool

from app.database.models import utc_now
from app.models.collection import CollectionOverview, CollectionState, ResourceCollectionStatus, StockCollectionStatus
from app.services.cache import CachedResult
from app.services.collector import WatchlistCollector
from app.services.stock import StockService
from app.services.watchlist import WatchlistService

T = TypeVar("T")


@dataclass(frozen=True)
class StockReadResult(CachedResult[T], Generic[T]):
    state: CollectionState | None = None


def combine_states(states: list[CollectionState]) -> CollectionState:
    if not states:
        return "warming"
    if any(state in ("ready", "stale") for state in states):
        if any(state in ("warming", "unavailable", "partial") for state in states):
            return "partial"
        return "stale" if "stale" in states else "ready"
    return "warming" if "warming" in states else "unavailable"


class StockReadService:
    def __init__(self, stocks: StockService, watchlist: WatchlistService, collector: WatchlistCollector | None):
        self.stocks = stocks
        self.watchlist = watchlist
        self.collector = collector

    async def symbols(self) -> list[str]:
        return [item.symbol for item in await run_in_threadpool(self.watchlist.list_entries)]

    async def is_background(self, symbols: list[str]) -> bool:
        return self.collector is not None and set(symbols).issubset(await self.symbols())

    def resource_status(self, symbol: str, resource: str, cached: CachedResult | None) -> ResourceCollectionStatus:
        observation = self.collector.observation(symbol, resource) if self.collector else None
        if cached is None:
            pending = self.collector and self.collector.is_pending(symbol, resource)
            state = "unavailable" if observation and observation.failed and not pending else "warming"
        elif self.collector is None:
            state = "stale" if cached.stale else "ready"
        else:
            # Store TTL controls fetches; UI freshness follows the collector's schedule.
            max_age = max(5, 2 * self.collector.interval(resource))
            too_old = cached.cached_at is None or (utc_now() - cached.cached_at).total_seconds() >= max_age
            state = "stale" if too_old or (observation and observation.failed) else "ready"
        source = None
        if cached and resource in ("intraday", "daily", "weekly") and cached.data:
            source = cached.data[-1].source
        return ResourceCollectionStatus(state=state, cached_at=cached.cached_at if cached else None,
                                        last_attempt_at=observation.attempted_at if observation else None, source=source)

    def result(self, symbol: str, resource: str, cached: CachedResult | None, empty):
        state = self.resource_status(symbol, resource, cached).state
        return StockReadResult(cached.data if cached else empty, stale=state == "stale",
                               cached_at=cached.cached_at if cached else None, state=state)

    async def quote(self, symbol: str, *, prefer_cached: bool = False):
        if await self.is_background([symbol]):
            return self.result(symbol, "quote", (await self.stocks.cached_quotes([symbol])).get(symbol), None)
        return await self.stocks.get_quote(symbol, prefer_cached=prefer_cached)

    async def quotes(self, symbols: list[str], *, prefer_cached: bool = False):
        if await self.is_background(symbols):
            cached = await self.stocks.cached_quotes(symbols)
            statuses = [self.resource_status(symbol, "quote", cached.get(symbol)).state for symbol in symbols]
            return StockReadResult([cached[symbol].data for symbol in symbols if symbol in cached],
                                   stale="stale" in statuses,
                                   cached_at=min(item.cached_at for item in cached.values()) if cached else None,
                                   state=combine_states(statuses))
        return await self.stocks.get_quotes(symbols, prefer_cached=prefer_cached)

    async def intraday(self, symbol: str):
        if await self.is_background([symbol]):
            return self.result(symbol, "intraday", await self.stocks.cached_intraday(symbol), [])
        return await self.stocks.get_intraday(symbol)

    async def kline(self, symbol: str, period: str, limit: int, *, prefer_cached: bool = False):
        if limit <= 120 and await self.is_background([symbol]):
            return self.result(symbol, period, await self.stocks.cached_kline(symbol, period, limit), [])
        # Explicit requests beyond the prefetch coverage are still fulfilled on demand.
        return await self.stocks.get_kline(symbol, period, limit, prefer_cached=prefer_cached)

    async def overview(self) -> CollectionOverview:
        symbols = await self.symbols()
        cached = await self.stocks.cached_resources(symbols) if symbols else {}
        items = []
        for symbol in symbols:
            resources = {resource: self.resource_status(symbol, resource, cached.get((symbol, resource)))
                         for resource in ("quote", "intraday", "daily", "weekly")}
            items.append(StockCollectionStatus(symbol=symbol, state=combine_states([v.state for v in resources.values()]),
                                               resources=resources))
        return CollectionOverview(enabled=self.collector is not None, items=items)

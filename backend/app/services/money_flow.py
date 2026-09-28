"""One shared bounded snapshot for flow pages, background jobs and tools."""

from time import monotonic
from cachetools import TTLCache
from starlette.concurrency import run_in_threadpool
from app.database.models import utc_now
from app.models.money_flow import MoneyFlowSeries
from app.providers.exceptions import DataSourceError
from app.services.cache import AsyncTTLStore
from app.services.reads import StockReadResult
from app.services.snapshots import SnapshotStore


class MoneyFlowService:
    def __init__(self, provider, sessions, watchlist, *, background=False, timer=monotonic):
        self.provider, self.watchlist, self.background = provider, watchlist, background
        self.collector = None
        self._failed = TTLCache(maxsize=256, ttl=3600, timer=timer)
        self._store = AsyncTTLStore(ttl=60, stale_ttl=3600, timer=timer,
            snapshots=SnapshotStore(sessions, "stock_money_flow", MoneyFlowSeries))

    async def is_background(self, symbol):
        return self.background and any(item.symbol == symbol for item in await run_in_threadpool(self.watchlist.list_entries))

    async def fetch(self, symbol):
        try:
            result = await self._store.get(symbol, lambda: self.provider.get_stock_money_flow(symbol))
        except DataSourceError:
            self._failed[symbol] = True
            raise
        if result.stale:
            self._failed[symbol] = True
        else:
            self._failed.pop(symbol, None)
        return result

    async def get(self, symbol, limit=30, *, prefer_cached=False):
        if not 1 <= limit <= 30:
            raise ValueError("limit must be 1..30 trading days")
        background = await self.is_background(symbol)
        cached = await self._store.peek(symbol) if background or prefer_cached else None
        if background:
            if cached is None:
                state = "unavailable" if symbol in self._failed else "warming"
                return StockReadResult(MoneyFlowSeries(symbol=symbol), state=state)
            age = (utc_now() - cached.cached_at).total_seconds()
            interval = self.collector.interval() if self.collector else 60
            stale = age >= 2 * interval or symbol in self._failed
        else:
            cached = cached or await self.fetch(symbol)
            stale = cached.stale or symbol in self._failed
        series = cached.data.model_copy(update={"items": cached.data.items[-limit:]})
        return StockReadResult(series, stale=stale, cached_at=cached.cached_at, state="stale" if stale else "ready")

    async def aclose(self):
        await self._store.aclose()

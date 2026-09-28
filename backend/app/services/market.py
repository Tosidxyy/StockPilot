"""Market index access with short TTL and stale fallback."""

from collections.abc import Callable
from sqlalchemy.orm import Session, sessionmaker

from app.models.market import IntradayPoint, MarketIndex
from app.models.breadth import MarketBreadth
from app.providers.base import MarketDataProvider
from app.services.cache import AsyncTTLStore, CachedResult
from app.services.snapshots import SnapshotStore


class MarketService:
    def __init__(
        self,
        provider: MarketDataProvider,
        *,
        index_ttl: float = 1,
        intraday_ttl: float = 1,
        stale_ttl: float = 3600,
        breadth_stale_ttl: float = 3600,
        timer: Callable[[], float] | None = None,
        snapshot_sessions: sessionmaker[Session] | None = None,
    ) -> None:
        self._provider = provider
        self._breadth: AsyncTTLStore[MarketBreadth] = AsyncTTLStore(ttl=30, stale_ttl=breadth_stale_ttl, timer=timer,
            snapshots=SnapshotStore(snapshot_sessions, "market_breadth", MarketBreadth) if snapshot_sessions else None)
        self._indices: AsyncTTLStore[list[MarketIndex]] = AsyncTTLStore(
            ttl=index_ttl, stale_ttl=stale_ttl, timer=timer,
            snapshots=SnapshotStore(snapshot_sessions, "market_indices", list[MarketIndex]) if snapshot_sessions else None,
        )
        self._intraday: AsyncTTLStore[list[IntradayPoint]] = AsyncTTLStore(
            ttl=intraday_ttl, stale_ttl=stale_ttl, timer=timer,
            snapshots=SnapshotStore(snapshot_sessions, "index_intraday", list[IntradayPoint]) if snapshot_sessions else None,
        )

    async def get_indices(self, *, prefer_cached: bool = False) -> CachedResult[list[MarketIndex]]:
        if prefer_cached:
            cached = await self._indices.peek("indices")
            if cached is not None:
                return cached
        return await self._indices.get("indices", self._provider.get_indices)

    async def aclose(self) -> None:
        await self._breadth.aclose()
        await self._indices.aclose()
        await self._intraday.aclose()

    async def get_index_intraday(self, index_code: str = "000001") -> CachedResult[list[IntradayPoint]]:
        return await self._intraday.get(
            index_code, lambda: self._provider.get_index_intraday(index_code)
        )

    async def get_breadth(self, *, prefer_cached=False):
        if prefer_cached:
            cached = await self._breadth.peek("breadth")
            if cached is not None:
                return cached
        return await self._breadth.get("breadth", self._load_breadth)

    async def _load_breadth(self):
        result = await self._provider.get_market_breadth()
        previous = await self._breadth.peek("breadth")
        if previous:
            old = {e.exchange: e for e in previous.data.exchanges}
            result.exchanges = [old[e.exchange].model_copy(update={"stale": True})
                if e.as_of is None and old[e.exchange].as_of is not None else e for e in result.exchanges]
            for name in ("limit_up", "limit_down"):
                pool, former = getattr(result, name), getattr(previous.data, name)
                if pool.count is None and former.count is not None:
                    setattr(result, name, former.model_copy(update={"stale": True}))
        return result

"""Market index access with short TTL and stale fallback."""

from collections.abc import Callable

from app.models.market import IntradayPoint, MarketIndex
from app.providers.base import MarketDataProvider
from app.services.cache import AsyncTTLStore, CachedResult


class MarketService:
    def __init__(
        self,
        provider: MarketDataProvider,
        *,
        index_ttl: float = 5,
        intraday_ttl: float = 1,
        stale_ttl: float = 3600,
        timer: Callable[[], float] | None = None,
    ) -> None:
        self._provider = provider
        self._indices: AsyncTTLStore[list[MarketIndex]] = AsyncTTLStore(
            ttl=index_ttl, stale_ttl=stale_ttl, timer=timer
        )
        self._intraday: AsyncTTLStore[list[IntradayPoint]] = AsyncTTLStore(
            ttl=intraday_ttl, stale_ttl=stale_ttl, timer=timer
        )

    async def get_indices(self) -> CachedResult[list[MarketIndex]]:
        return await self._indices.get("indices", self._provider.get_indices)

    async def get_index_intraday(self, index_code: str = "000001") -> CachedResult[list[IntradayPoint]]:
        return await self._intraday.get(
            index_code, lambda: self._provider.get_index_intraday(index_code)
        )

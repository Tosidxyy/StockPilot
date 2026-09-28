"""REST response and request envelopes."""

from typing import Generic, TypeVar
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.market import MarketIndex

T = TypeVar("T")


class DataResponse(BaseModel, Generic[T]):
    data: T
    stale: bool = False
    cached_at: datetime | None = None


class MarketOverviewResponse(BaseModel):
    indices: list[MarketIndex]
    watchlist_count: int
    stale: bool = False
    cached_at: datetime | None = None


class WatchlistAddRequest(BaseModel):
    symbol: str = Field(pattern=r"^[03468][0-9]{5}$")

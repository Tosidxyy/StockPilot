"""REST response and request envelopes."""

from typing import Generic, TypeVar
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.market import MarketIndex
from app.models.collection import CollectionState

T = TypeVar("T")


class DataResponse(BaseModel, Generic[T]):
    data: T
    stale: bool = False
    cached_at: datetime | None = None


class StockDataResponse(DataResponse[T], Generic[T]):
    collection_state: CollectionState | None = None


class MarketOverviewResponse(BaseModel):
    indices: list[MarketIndex]
    watchlist_count: int
    stale: bool = False
    cached_at: datetime | None = None


class WatchlistAddRequest(BaseModel):
    symbol: str = Field(pattern=r"^[03468][0-9]{5}$")

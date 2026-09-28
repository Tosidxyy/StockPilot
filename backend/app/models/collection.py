"""Public collection metadata without upstream exception details."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

CollectionState = Literal["warming", "ready", "stale", "partial", "unavailable"]


class ResourceCollectionStatus(BaseModel):
    state: CollectionState
    cached_at: datetime | None = None
    last_attempt_at: datetime | None = None
    source: str | None = None


class StockCollectionStatus(BaseModel):
    symbol: str
    state: CollectionState
    resources: dict[str, ResourceCollectionStatus]


class CollectionOverview(BaseModel):
    enabled: bool
    items: list[StockCollectionStatus]

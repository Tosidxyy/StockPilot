"""News metadata and source excerpts; excerpts are never presented as full text."""

from datetime import datetime
from pydantic import BaseModel, Field
from app.models.collection import CollectionState


class NewsItem(BaseModel):
    id: str
    title: str
    source: str
    published_at: datetime
    url: str
    excerpt: str | None = None
    content_available: bool = False
    symbols: list[str] = Field(default_factory=list)
    association: str = "keyword_search"
    provider: str = "eastmoney"


class NewsBatch(BaseModel):
    items: list[NewsItem]
    complete: bool = True
    truncated: bool = False


class NewsPage(BaseModel):
    items: list[NewsItem]
    total: int
    page: int
    page_size: int
    has_more: bool
    source_truncated: bool
    partial: bool = False
    coverage: str = "最近30天内已采集的新闻，每次最多采集源头前100条，不保证完整历史覆盖"
    collection_state: CollectionState

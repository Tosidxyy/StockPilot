"""Announcement metadata and bounded public document text."""

from datetime import date, datetime
from typing import Literal
from pydantic import BaseModel, Field
from app.models.collection import CollectionState


class AnnouncementCategory(BaseModel):
    code: str
    name: str


class AnnouncementItem(BaseModel):
    id: str
    title: str
    notice_date: date
    disclosed_at: datetime | None = None
    source: str = "东方财富 · 公司公告"
    url: str
    symbols: list[str] = Field(default_factory=list)
    categories: list[AnnouncementCategory] = Field(default_factory=list)
    attachment_urls: list[str] = Field(default_factory=list)
    text_status: Literal["pending", "ready", "partial", "unavailable"] = "pending"
    text: str | None = None
    text_source: str | None = None
    text_reason: str | None = None
    text_hash: str | None = None
    source_text_hash: str | None = None
    text_length: int = 0
    pages_extracted: int = 0
    total_pages: int | None = None
    text_fetched_at: datetime | None = None
    text_stale: bool = False


class AnnouncementBatch(BaseModel):
    items: list[AnnouncementItem]
    complete: bool = True
    truncated: bool = False


class AnnouncementPage(BaseModel):
    items: list[AnnouncementItem]
    categories: list[AnnouncementCategory] = Field(default_factory=list)
    total: int
    page: int
    page_size: int
    has_more: bool
    source_truncated: bool
    partial: bool = False
    coverage: str = "最近90天内已采集公告，每次最多100条；不保证完整历史覆盖。正文仅提取公开附件，不提供OCR"
    collection_state: CollectionState

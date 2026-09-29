"""Public forum samples, explicitly separate from verified news and quotations."""

from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field

Sentiment = Literal["bullish", "bearish", "mixed", "unknown"]


class ForumComment(BaseModel):
    id: str
    symbol: str
    text: str
    published_at: datetime
    url: str
    kind: Literal["post_title", "reply"]
    sentiment: Sentiment = "unknown"


class CommentBatch(BaseModel):
    items: list[ForumComment]
    partial: bool = False


class SentimentReport(BaseModel):
    symbol: str
    items: list[ForumComment]
    counts: dict[Sentiment, int]
    sample_count: int
    post_count: int
    reply_count: int
    duplicate_count: int
    sample_start: datetime | None
    sample_end: datetime | None
    partial: bool = False
    source: str = "东方财富股吧"
    method: str = "关键词规则 v1；混合表示多空词并存，未判定包含疑问、否定及未命中；不代表模型分析或分类准确率。"
    coverage: str = "公开首页最近24小时的用户发帖标题及页面附带回复，最多200条；排除资讯/公告标题与跨股票内容，不保证全部评论覆盖。"
    refresh_seconds: int = Field(default=60, ge=60)

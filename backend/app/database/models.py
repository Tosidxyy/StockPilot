"""V0.1 persistence schema; no Agent execution logic lives here."""

from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import Date, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class DocumentIndex(Base):
    __tablename__ = "document_index"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(6), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    document_id: Mapped[str] = mapped_column(String(100))
    date: Mapped[date] = mapped_column(Date, index=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class DocumentChunk(Base):
    __tablename__ = "document_chunk"
    id: Mapped[int] = mapped_column(primary_key=True)
    document_key: Mapped[str] = mapped_column(ForeignKey("document_index.key", ondelete="CASCADE"), index=True)
    evidence_id: Mapped[str] = mapped_column(String(25), unique=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    start: Mapped[int] = mapped_column(Integer)
    end: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text)


class AnnouncementRecord(Base):
    __tablename__ = "announcement_record"
    id: Mapped[str] = mapped_column(String(20), primary_key=True)
    notice_date: Mapped[date] = mapped_column(Date, index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    body: Mapped[str | None] = mapped_column(Text)


class AnnouncementScopeItem(Base):
    __tablename__ = "announcement_scope_item"
    scope: Mapped[str] = mapped_column(String(6), primary_key=True)
    announcement_id: Mapped[str] = mapped_column(ForeignKey("announcement_record.id", ondelete="CASCADE"), primary_key=True)


class AnnouncementFetchState(Base):
    __tablename__ = "announcement_fetch_state"
    scope: Mapped[str] = mapped_column(String(6), primary_key=True)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    failed: Mapped[bool] = mapped_column(default=False)
    partial: Mapped[bool] = mapped_column(default=False)
    truncated: Mapped[bool] = mapped_column(default=False)


class NewsRecord(Base):
    __tablename__ = "news_record"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class NewsScopeItem(Base):
    __tablename__ = "news_scope_item"
    scope: Mapped[str] = mapped_column(String(10), primary_key=True)
    news_id: Mapped[str] = mapped_column(ForeignKey("news_record.id", ondelete="CASCADE"), primary_key=True)


class NewsFetchState(Base):
    __tablename__ = "news_fetch_state"
    scope: Mapped[str] = mapped_column(String(10), primary_key=True)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    failed: Mapped[bool] = mapped_column(default=False)
    partial: Mapped[bool] = mapped_column(default=False)
    truncated: Mapped[bool] = mapped_column(default=False)


class MarketSnapshot(Base):
    __tablename__ = "market_snapshot"

    namespace: Mapped[str] = mapped_column(String(40), primary_key=True)
    cache_key: Mapped[str] = mapped_column(Text, primary_key=True)
    payload: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    saved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SentimentLabel(Base):
    __tablename__ = "sentiment_label"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    label: Mapped[str] = mapped_column(String(12))
    classified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class SentimentAnalysisRecord(Base):
    __tablename__ = "sentiment_analysis"
    symbol: Mapped[str] = mapped_column(String(6), primary_key=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class WatchlistItem(Base):
    __tablename__ = "watchlist"
    __table_args__ = (UniqueConstraint("symbol", name="uq_watchlist_symbol"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(6), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class ChatSession(Base):
    __tablename__ = "chat_session"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    title: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class ChatMessage(Base):
    __tablename__ = "chat_message"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("chat_session.id", ondelete="CASCADE"), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class AgentTrace(Base):
    __tablename__ = "agent_trace"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("chat_session.id", ondelete="CASCADE"), nullable=False, index=True)
    step_index: Mapped[int] = mapped_column(Integer, nullable=False)
    tool_name: Mapped[str] = mapped_column(String(100), nullable=False)
    tool_input: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    tool_output_summary: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class ChatEvidence(Base):
    __tablename__ = "chat_evidence"
    message_id: Mapped[int] = mapped_column(ForeignKey("chat_message.id", ondelete="CASCADE"), primary_key=True)
    evidence_id: Mapped[str] = mapped_column(String(25), primary_key=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)

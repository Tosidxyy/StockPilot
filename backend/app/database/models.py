"""V0.1 persistence schema; no Agent execution logic lives here."""

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class MarketSnapshot(Base):
    __tablename__ = "market_snapshot"

    namespace: Mapped[str] = mapped_column(String(40), primary_key=True)
    cache_key: Mapped[str] = mapped_column(Text, primary_key=True)
    payload: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    saved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


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

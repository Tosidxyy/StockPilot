"""Bounded SQLite snapshots of successful market queries, for outage fallback."""

import asyncio
import json
import logging
from collections.abc import Callable, Hashable
from datetime import datetime, timedelta, timezone
from typing import Generic, TypeVar

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import delete, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.database.models import MarketSnapshot, utc_now

T = TypeVar("T")
logger = logging.getLogger(__name__)


class SnapshotStore(Generic[T]):
    def __init__(self, sessions: sessionmaker[Session], namespace: str, schema: object,
                 *, maxsize: int = 256, max_age: timedelta = timedelta(days=14),
                 clock: Callable[[], datetime] = utc_now):
        self._sessions = sessions
        self._namespace = namespace
        self._adapter = TypeAdapter(schema)
        self._maxsize = maxsize
        self._max_age = max_age
        self._clock = clock

    async def save(self, key: Hashable, data: T) -> datetime | None:
        if not data:
            return None
        return await asyncio.to_thread(self._save, key, data)

    def _save(self, key: Hashable, data: T) -> datetime | None:
        saved_at = self._clock()
        encoded = json.dumps(key, ensure_ascii=False)
        try:
            with self._sessions.begin() as session:
                row = session.get(MarketSnapshot, (self._namespace, encoded))
                if row is None:
                    row = MarketSnapshot(namespace=self._namespace, cache_key=encoded)
                    session.add(row)
                row.payload = self._adapter.dump_python(data, mode="json")
                row.saved_at = saved_at
                session.flush()
                session.execute(delete(MarketSnapshot).where(
                    MarketSnapshot.namespace == self._namespace,
                    MarketSnapshot.saved_at < saved_at - self._max_age,
                ))
                excess = session.scalars(select(MarketSnapshot.cache_key).where(
                    MarketSnapshot.namespace == self._namespace,
                ).order_by(MarketSnapshot.saved_at.desc(), MarketSnapshot.cache_key).offset(self._maxsize)).all()
                if excess:
                    session.execute(delete(MarketSnapshot).where(
                        MarketSnapshot.namespace == self._namespace,
                        MarketSnapshot.cache_key.in_(excess),
                    ))
            return saved_at
        except SQLAlchemyError:
            logger.warning("Market snapshot write failed")
            return None

    async def read(self, key: Hashable) -> tuple[T, datetime] | None:
        return await asyncio.to_thread(self._read, key)

    async def read_all(self, matches: Callable[[object], bool]) -> list[tuple[object, T, datetime]]:
        """One bounded database read for a multi-stock status/cache response."""
        def read_rows():
            results = []
            try:
                with self._sessions() as session:
                    rows = session.scalars(select(MarketSnapshot).where(
                        MarketSnapshot.namespace == self._namespace,
                    ).order_by(MarketSnapshot.saved_at.desc()).limit(self._maxsize))
                    for row in rows:
                        try:
                            key = json.loads(row.cache_key)
                            if matches(key):
                                decoded = self._decode(row)
                                if decoded is not None:
                                    results.append((key, *decoded))
                        except (ValueError, TypeError):
                            continue
            except SQLAlchemyError:
                logger.warning("Market snapshot read failed")
            return results
        return await asyncio.to_thread(read_rows)

    async def read_latest(self, matches: Callable[[object], bool],
                          accepts: Callable[[T], bool] | None = None) -> tuple[T, datetime] | None:
        return await asyncio.to_thread(self._read_latest, matches, accepts)

    def _read_latest(self, matches: Callable[[object], bool],
                     accepts: Callable[[T], bool] | None) -> tuple[T, datetime] | None:
        try:
            with self._sessions() as session:
                rows = session.scalars(select(MarketSnapshot).where(
                    MarketSnapshot.namespace == self._namespace,
                ).order_by(MarketSnapshot.saved_at.desc()).limit(self._maxsize))
                for row in rows:
                    try:
                        if not matches(json.loads(row.cache_key)):
                            continue
                        result = self._decode(row)
                        if result is not None and (accepts is None or accepts(result[0])):
                            return result
                    except (ValueError, TypeError):
                        continue
        except SQLAlchemyError:
            logger.warning("Market snapshot read failed")
        return None

    def _decode(self, row: MarketSnapshot) -> tuple[T, datetime] | None:
        saved_at = row.saved_at.replace(tzinfo=timezone.utc)
        age = self._clock() - saved_at
        if age < timedelta(0) or age > self._max_age:
            return None
        data = self._adapter.validate_python(row.payload)
        return (data, saved_at) if data else None

    def _read(self, key: Hashable) -> tuple[T, datetime] | None:
        try:
            with self._sessions() as session:
                row = session.get(MarketSnapshot, (self._namespace, json.dumps(key, ensure_ascii=False)))
                if row is None:
                    return None
                return self._decode(row)
        except (SQLAlchemyError, ValidationError):
            logger.warning("Market snapshot read failed")
            return None

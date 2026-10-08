"""Bounded fresh and stale caches for provider results."""

import asyncio
import json
from collections.abc import Awaitable, Callable, Hashable
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from time import monotonic
from typing import Generic, TypeVar

from cachetools import TTLCache

from app.providers.exceptions import DataSourceError
from app.database.models import utc_now
from app.services.snapshots import SnapshotStore

T = TypeVar("T")


@dataclass(frozen=True)
class CachedResult(Generic[T]):
    data: T
    stale: bool = False
    cached_at: datetime | None = None


@dataclass(frozen=True)
class RetryState:
    attempts: int
    retry_at: float
    error_type: type[DataSourceError]
    message: str
    failed_at: datetime


class AsyncTTLStore(Generic[T]):
    def __init__(
        self,
        *,
        ttl: float,
        stale_ttl: float = 3600,
        maxsize: int = 256,
        timer: Callable[[], float] | None = None,
        snapshots: SnapshotStore[T] | None = None,
    ) -> None:
        if ttl <= 0 or stale_ttl <= ttl or maxsize <= 0:
            raise ValueError("cache TTL and capacity must be positive; stale_ttl must exceed ttl")
        kwargs = {"timer": timer} if timer is not None else {}
        self._fresh: TTLCache[Hashable, T] = TTLCache(maxsize=maxsize, ttl=ttl, **kwargs)
        self._stale: TTLCache[Hashable, T] = TTLCache(maxsize=maxsize, ttl=stale_ttl, **kwargs)
        self._saved_at: TTLCache[Hashable, datetime] = TTLCache(maxsize=maxsize, ttl=stale_ttl, **kwargs)
        self._snapshots = snapshots
        self._failures: TTLCache[Hashable, RetryState] = TTLCache(
            maxsize=maxsize, ttl=stale_ttl, **kwargs
        )
        self._timer = timer or monotonic
        self._ttl = ttl
        self._restored = TTLCache(maxsize=maxsize, ttl=stale_ttl, **kwargs)
        self._pending: dict[Hashable, asyncio.Task[CachedResult[T]]] = {}

    def _known_failure(self, matches: Callable[[object], bool], saved_at: datetime) -> bool:
        return any(matches(key) and saved_at <= failure.failed_at
                   for key, failure in list(self._failures.items()))

    def failed_since(self, saved_at: datetime, matches: Callable[[object], bool]) -> bool:
        """Let projections of a batched snapshot check failures for their own resource."""
        return self._known_failure(matches, saved_at)

    async def peek(
        self, key: Hashable, *, matches: Callable[[object], bool] | None = None,
        accepts: Callable[[T], bool] | None = None,
    ) -> CachedResult[T] | None:
        """Read the newest valid memory/SQLite snapshot without fetching or refreshing its age."""
        match = matches or (lambda candidate: candidate == key)
        newest: CachedResult[T] | None = None
        if self._snapshots is not None:
            stored = await self._snapshots.read_latest(match, accepts) if matches else await self._snapshots.read(key)
            if stored is not None:
                data, saved_at = stored
                newest = CachedResult(data, stale=(utc_now() - saved_at).total_seconds() >= self._ttl
                                      or self._known_failure(match, saved_at),
                                      cached_at=saved_at)
        for candidate, data in list(self._stale.items()):
            saved_at = self._saved_at.get(candidate)
            if data and saved_at is not None and match(candidate) and (accepts is None or accepts(data)):
                if newest is None or saved_at >= newest.cached_at:
                    stale = candidate not in self._fresh and (candidate not in self._restored or
                        (utc_now() - saved_at).total_seconds() >= self._ttl)
                    newest = CachedResult(data, stale=stale or self._known_failure(match, saved_at), cached_at=saved_at)
        return CachedResult(deepcopy(newest.data), stale=newest.stale, cached_at=newest.cached_at) if newest else None

    async def aclose(self) -> None:
        """Cancel shared loaders before their provider/database is closed."""
        tasks = list(self._pending.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._pending.clear()

    async def peek_all(self, matches: Callable[[object], bool], *, prefer_memory: bool = False,
                       project: Callable[[T], object] | None = None) -> list[tuple[object, CachedResult]]:
        """Read covering snapshots without refreshing timestamps or invoking loaders."""
        results = {}
        for key, data in list(self._stale.items()):
            saved_at = self._saved_at.get(key)
            if data and saved_at is not None and matches(key):
                encoded = json.dumps(key)
                stale = key not in self._fresh and (key not in self._restored or
                    (utc_now() - saved_at).total_seconds() >= self._ttl)
                results[encoded] = (key, CachedResult(project(data) if project else data,
                    stale=stale or self._known_failure(lambda candidate: json.dumps(candidate) == encoded, saved_at),
                    cached_at=saved_at))
        if self._snapshots is not None and (not prefer_memory or not results):
            for key, data, saved_at in await self._snapshots.read_all(matches):
                encoded = json.dumps(key)
                previous = results.get(encoded)
                if previous is None or saved_at > previous[1].cached_at:
                    results[encoded] = (key, CachedResult(project(data) if project else data,
                        stale=(utc_now() - saved_at).total_seconds() >= self._ttl or self._known_failure(
                            lambda candidate: json.dumps(candidate) == encoded, saved_at), cached_at=saved_at))
                if prefer_memory:
                    normalized = tuple(key) if isinstance(key, list) else key
                    self._stale[normalized] = data
                    self._saved_at[normalized] = saved_at
                    self._restored[normalized] = True
        return deepcopy(list(results.values()))

    async def get(self, key: Hashable, loader: Callable[[], Awaitable[T]], *, revalidate: bool = False) -> CachedResult[T]:
        """Scheduled revalidation bypasses fresh TTL, retaining coalescing and failure backoff."""
        if not revalidate and key in self._fresh:
            return CachedResult(deepcopy(self._fresh[key]), cached_at=self._saved_at.get(key))
        task = self._pending.get(key)
        if task is None:
            task = asyncio.create_task(self._load(key, loader))
            self._pending[key] = task
            task.add_done_callback(lambda completed: self._finish(key, completed))
        # A disconnected caller must not cancel the fetch shared by other callers.
        result = await asyncio.shield(task)
        return CachedResult(deepcopy(result.data), stale=result.stale, cached_at=result.cached_at)

    async def _fallback(self, key: Hashable) -> CachedResult[T] | None:
        if key in self._stale:
            return CachedResult(self._stale[key], stale=True, cached_at=self._saved_at.get(key))
        if self._snapshots is not None:
            stored = await self._snapshots.read(key)
            if stored is not None:
                data, saved_at = stored
                return CachedResult(data, stale=True, cached_at=saved_at)
        return None

    def _finish(self, key: Hashable, task: asyncio.Task[CachedResult[T]]) -> None:
        if self._pending.get(key) is task:
            del self._pending[key]
        # Retrieve failures even when all waiting callers have disconnected.
        if not task.cancelled():
            task.exception()

    async def _load(self, key: Hashable, loader: Callable[[], Awaitable[T]]) -> CachedResult[T]:
        failure = self._failures.get(key)
        if failure is not None and self._timer() < failure.retry_at:
            fallback = await self._fallback(key)
            if fallback is not None:
                return fallback
            raise failure.error_type(failure.message)
        try:
            data = await loader()
        except DataSourceError as error:
            self._fresh.pop(key, None)
            attempts = min((failure.attempts if failure else 0) + 1, 5)
            self._failures[key] = RetryState(
                attempts, self._timer() + min(2 ** attempts, 30), type(error), str(error), utc_now()
            )
            fallback = await self._fallback(key)
            if fallback is not None:
                return fallback
            raise
        self._failures.pop(key, None)
        self._restored.pop(key, None)
        saved_at = utc_now()
        if self._snapshots is not None:
            saved_at = await self._snapshots.save(key, data) or saved_at
        self._fresh[key] = deepcopy(data)
        self._stale[key] = deepcopy(data)
        self._saved_at[key] = saved_at
        return CachedResult(deepcopy(data), cached_at=saved_at)

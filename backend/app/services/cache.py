"""Bounded fresh and stale caches for provider results."""

import asyncio
from collections.abc import Awaitable, Callable, Hashable
from copy import deepcopy
from dataclasses import dataclass
from time import monotonic
from typing import Generic, TypeVar

from cachetools import TTLCache

from app.providers.exceptions import DataSourceError

T = TypeVar("T")


@dataclass(frozen=True)
class CachedResult(Generic[T]):
    data: T
    stale: bool = False


@dataclass(frozen=True)
class RetryState:
    attempts: int
    retry_at: float
    error_type: type[DataSourceError]
    message: str


class AsyncTTLStore(Generic[T]):
    def __init__(
        self,
        *,
        ttl: float,
        stale_ttl: float = 3600,
        maxsize: int = 256,
        timer: Callable[[], float] | None = None,
    ) -> None:
        if ttl <= 0 or stale_ttl <= ttl or maxsize <= 0:
            raise ValueError("cache TTL and capacity must be positive; stale_ttl must exceed ttl")
        kwargs = {"timer": timer} if timer is not None else {}
        self._fresh: TTLCache[Hashable, T] = TTLCache(maxsize=maxsize, ttl=ttl, **kwargs)
        self._stale: TTLCache[Hashable, T] = TTLCache(maxsize=maxsize, ttl=stale_ttl, **kwargs)
        self._failures: TTLCache[Hashable, RetryState] = TTLCache(
            maxsize=maxsize, ttl=stale_ttl, **kwargs
        )
        self._timer = timer or monotonic
        self._pending: dict[Hashable, asyncio.Task[CachedResult[T]]] = {}

    async def get(self, key: Hashable, loader: Callable[[], Awaitable[T]]) -> CachedResult[T]:
        if key in self._fresh:
            return CachedResult(deepcopy(self._fresh[key]))
        task = self._pending.get(key)
        if task is None:
            task = asyncio.create_task(self._load(key, loader))
            self._pending[key] = task
            task.add_done_callback(lambda completed: self._finish(key, completed))
        # A disconnected caller must not cancel the fetch shared by other callers.
        result = await asyncio.shield(task)
        return CachedResult(deepcopy(result.data), stale=result.stale)

    def _finish(self, key: Hashable, task: asyncio.Task[CachedResult[T]]) -> None:
        if self._pending.get(key) is task:
            del self._pending[key]
        # Retrieve failures even when all waiting callers have disconnected.
        if not task.cancelled():
            task.exception()

    async def _load(self, key: Hashable, loader: Callable[[], Awaitable[T]]) -> CachedResult[T]:
        failure = self._failures.get(key)
        if failure is not None and self._timer() < failure.retry_at:
            if key in self._stale:
                return CachedResult(self._stale[key], stale=True)
            raise failure.error_type(failure.message)
        try:
            data = await loader()
        except DataSourceError as error:
            attempts = min((failure.attempts if failure else 0) + 1, 5)
            self._failures[key] = RetryState(
                attempts, self._timer() + min(2 ** attempts, 30), type(error), str(error)
            )
            if key in self._stale:
                return CachedResult(self._stale[key], stale=True)
            raise
        self._failures.pop(key, None)
        self._fresh[key] = deepcopy(data)
        self._stale[key] = deepcopy(data)
        return CachedResult(deepcopy(data))

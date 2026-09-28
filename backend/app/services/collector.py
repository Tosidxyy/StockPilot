"""Proactively collect supported watchlist data while the backend is running."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, time, timedelta, timezone
from dataclasses import dataclass
from time import monotonic

from starlette.concurrency import run_in_threadpool

from app.providers.exceptions import DataSourceError
from app.services.stock import StockService
from app.services.cache import CachedResult
from app.services.watchlist import WatchlistService

logger = logging.getLogger(__name__)
BEIJING = timezone(timedelta(hours=8))
JobKey = tuple[str, str | tuple[str, ...]]


@dataclass(frozen=True)
class CollectionAttempt:
    attempted_at: datetime
    failed: bool




def is_market_session(now: datetime) -> bool:
    """Weekday session heuristic; this does not include a holiday calendar."""
    local = now.astimezone(BEIJING)
    return local.weekday() < 5 and (
        time(9, 15) <= local.time() < time(11, 30)
        or time(13) <= local.time() < time(15, 5)
    )


class WatchlistCollector:
    def __init__(
        self, stocks: StockService, watchlist: WatchlistService, *, workers: int = 4,
        timer: Callable[[], float] = monotonic,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        if workers < 1:
            raise ValueError("workers must be positive")
        self._stocks = stocks
        self._watchlist = watchlist
        self._workers = workers
        self._timer = timer
        self._clock = clock
        self._runner: asyncio.Task[None] | None = None
        self._pending: dict[JobKey, asyncio.Task[None]] = {}
        self._attempted: dict[JobKey, float] = {}
        self._symbols: tuple[str, ...] = ()
        self._synced_at: float | None = None
        self._observations: dict[tuple[str, str], CollectionAttempt] = {}

    def observation(self, symbol: str, resource: str) -> CollectionAttempt | None:
        return self._observations.get((symbol, resource))

    def is_pending(self, symbol: str, resource: str) -> bool:
        kind = "quotes" if resource == "quote" else resource
        return any(not task.done() and key[0] == kind and (
            symbol in key[1] if kind == "quotes" else symbol == key[1]
        ) for key, task in self._pending.items())

    def interval(self, resource: str) -> int:
        if not is_market_session(self._clock()):
            return 300
        return 60 if resource in ("daily", "weekly") else 2

    def request_refresh(self, symbols: set[str]) -> None:
        for key in list(self._attempted):
            covered = set(key[1]) if key[0] == "quotes" else {key[1]}
            if covered.intersection(symbols):
                del self._attempted[key]

    def start(self) -> None:
        if self._runner is None:
            self._runner = asyncio.create_task(self._run(), name="watchlist-collector")

    async def stop(self) -> None:
        tasks = list(self._pending.values())
        if self._runner is not None:
            tasks.append(self._runner)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._pending.clear()
        self._runner = None

    async def _run(self) -> None:
        while True:
            try:
                await self.poll()
            except Exception:
                logger.exception("Watchlist collector scan failed")
            await asyncio.sleep(0.1)

    async def poll(self) -> None:
        """Scan membership and dispatch only available slots; never enqueue a full watchlist."""
        now = self._timer()
        if self._synced_at is None or now - self._synced_at >= 2:
            entries = await run_in_threadpool(self._watchlist.list_entries)
            self._symbols = tuple(entry.symbol for entry in entries)
            self._synced_at = now
        symbols = self._symbols
        self._observations = {key: value for key, value in self._observations.items() if key[0] in symbols}
        active = is_market_session(self._clock())
        live_interval = 2 if active else 300
        history_interval = 60 if active else 300
        jobs: dict[JobKey, tuple[str, float, Callable[[], Awaitable[object]]]] = {}
        for offset in range(0, len(symbols), 50):
            batch = symbols[offset:offset + 50]
            jobs[("quotes", batch)] = (
                "quotes", live_interval, lambda batch=batch: self._stocks.get_quotes(batch),
            )
        for symbol in symbols:
            jobs[("intraday", symbol)] = (
                "intraday", live_interval, lambda symbol=symbol: self._stocks.get_intraday(symbol),
            )
            for period in ("daily", "weekly"):
                jobs[(period, symbol)] = (
                    "history", history_interval,
                    lambda symbol=symbol, period=period: self._stocks.get_kline(symbol, period, 120),
                )
        # Removed entries retain their stored snapshots, but get no new scheduled requests.
        self._attempted = {key: value for key, value in self._attempted.items() if key in jobs}
        for key, task in list(self._pending.items()):
            if task.done():
                del self._pending[key]
        capacities = {"quotes": 1, "intraday": self._workers, "history": 2}
        for key in self._pending:
            lane = "history" if key[0] in ("daily", "weekly") else key[0]
            capacities[lane] -= 1
        # Oldest attempt first prevents a slow source from starving later stocks.
        for key in sorted(jobs, key=lambda item: self._attempted.get(item, float("-inf"))):
            lane, interval, loader = jobs[key]
            last = self._attempted.get(key)
            if capacities[lane] <= 0 or key in self._pending or (last is not None and now - last < interval):
                continue
            self._attempted[key] = now
            capacities[lane] -= 1
            self._pending[key] = asyncio.create_task(self._fetch(key, loader))

    async def _fetch(self, key: JobKey, loader: Callable[[], Awaitable[CachedResult]]) -> None:
        attempted_at = self._clock()
        symbols = key[1] if key[0] == "quotes" else (key[1],)
        resource = "quote" if key[0] == "quotes" else key[0]
        try:
            result = await loader()
            available = {item.symbol for item in result.data} if key[0] == "quotes" else set(symbols) if result.data else set()
            for symbol in symbols:
                if symbol in self._symbols:
                    self._observations[(symbol, resource)] = CollectionAttempt(
                        attempted_at, failed=result.stale or symbol not in available,
                    )
        except DataSourceError:
            # Service owns backoff and stale snapshots; another stock/lane can still succeed.
            logger.warning("Watchlist collection unavailable: %s", key)
            for symbol in symbols:
                if symbol in self._symbols:
                    self._observations[(symbol, resource)] = CollectionAttempt(attempted_at, failed=True)
        except Exception:
            logger.exception("Watchlist collection failed: %s", key)
            for symbol in symbols:
                if symbol in self._symbols:
                    self._observations[(symbol, resource)] = CollectionAttempt(attempted_at, failed=True)

"""Collect watchlist announcements independently from high-frequency market lanes."""

import asyncio
import logging
from time import monotonic
from starlette.concurrency import run_in_threadpool
from app.providers.exceptions import DataSourceError
from app.services.announcements import AnnouncementService

logger = logging.getLogger(__name__)


class AnnouncementCollector:
    def __init__(self, announcements: AnnouncementService, *, timer=monotonic):
        self.announcements, self._timer = announcements, timer
        self._attempted: dict[str, float] = {}
        self._pending: dict[str, asyncio.Task] = {}
        self._runner = None

    def start(self):
        self._runner = asyncio.create_task(self._run(), name="watchlist-announcements")

    def request_refresh(self, symbols: set[str]):
        for symbol in symbols:
            self._attempted.pop(symbol, None)

    async def _run(self):
        while True:
            try:
                await self.poll()
            except Exception:
                logger.exception("Announcement collection scan failed")
            await asyncio.sleep(2)

    async def poll(self):
        symbols = {item.symbol for item in await run_in_threadpool(self.announcements.watchlist.list_entries)}
        self._attempted = {s: t for s, t in self._attempted.items() if s in symbols}
        self._pending = {s: task for s, task in self._pending.items() if not task.done()}
        now = self._timer()
        for symbol in sorted(symbols, key=lambda s: (self._attempted.get(s, float("-inf")), s)):
            if len(self._pending) >= 2:
                break
            if symbol in self._pending or now - self._attempted.get(symbol, float("-inf")) < 600:
                continue
            self._attempted[symbol] = now
            self._pending[symbol] = asyncio.create_task(self._collect(symbol))

    async def _collect(self, symbol):
        try:
            await self.announcements.fetch(symbol, force=True)
        except DataSourceError:
            logger.warning("Watchlist announcements unavailable: %s", symbol)
        except Exception:
            logger.exception("Watchlist announcements collection failed")

    async def stop(self):
        tasks = list(self._pending.values()) + ([self._runner] if self._runner else [])
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._pending.clear()
        self._runner = None

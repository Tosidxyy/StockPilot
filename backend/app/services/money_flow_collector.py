"""Collect flows in their own two-request lane; no page visit required."""

import asyncio
import logging
from datetime import datetime, timezone
from time import monotonic
from starlette.concurrency import run_in_threadpool
from app.providers.exceptions import DataSourceError
from app.services.collector import is_market_session

logger = logging.getLogger(__name__)


class MoneyFlowCollector:
    def __init__(self, flows, *, timer=monotonic, clock=lambda: datetime.now(timezone.utc)):
        self.flows, self._timer, self._clock = flows, timer, clock
        self._attempted, self._pending = {}, {}
        self._runner = None

    def interval(self):
        return 60 if is_market_session(self._clock()) else 300

    def start(self):
        if self._runner is None:
            self._runner = asyncio.create_task(self._run(), name="watchlist-money-flow")

    def request_refresh(self, symbols):
        for symbol in symbols:
            self._attempted.pop(symbol, None)

    async def _run(self):
        while True:
            try:
                await self.poll()
            except Exception:
                logger.exception("Money flow collection scan failed")
            await asyncio.sleep(2)

    async def poll(self):
        symbols = {item.symbol for item in await run_in_threadpool(self.flows.watchlist.list_entries)}
        self._attempted = {s: t for s, t in self._attempted.items() if s in symbols}
        self._pending = {s: task for s, task in self._pending.items() if not task.done()}
        now = self._timer()
        for symbol in sorted(symbols, key=lambda s: (self._attempted.get(s, float("-inf")), s)):
            if len(self._pending) >= 2:
                break
            if symbol in self._pending or now - self._attempted.get(symbol, float("-inf")) < self.interval():
                continue
            self._attempted[symbol] = now
            self._pending[symbol] = asyncio.create_task(self._collect(symbol))

    async def _collect(self, symbol):
        try:
            await self.flows.fetch(symbol)
        except DataSourceError:
            logger.warning("Watchlist money flow unavailable: %s", symbol)
        except Exception:
            logger.exception("Watchlist money flow collection failed")

    async def stop(self):
        tasks = list(self._pending.values()) + ([self._runner] if self._runner else [])
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._pending.clear()
        self._runner = None

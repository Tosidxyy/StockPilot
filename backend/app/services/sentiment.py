"""Bounded, persistent forum cache and transparent lightweight opinion counts."""

import asyncio
import logging
import re
from datetime import timedelta
from time import monotonic
import unicodedata

from starlette.concurrency import run_in_threadpool
from cachetools import TTLCache

from app.database.models import utc_now
from app.models.sentiment import SentimentReport
from app.providers.exceptions import DataSourceError
from app.services.cache import AsyncTTLStore, CachedResult
from app.services.snapshots import SnapshotStore
from app.services.watchlist import _validate_symbol

logger = logging.getLogger(__name__)
POSITIVE = ("看好", "看多", "利好", "涨停", "大涨", "反弹", "拉升", "加仓", "抄底", "突破", "低估")
NEGATIVE = ("看空", "看跌", "不看好", "利空", "跌停", "大跌", "暴跌", "崩盘", "割肉", "清仓", "出货", "减仓", "套牢", "高估", "破位", "下跌", "砸盘", "跑路", "垃圾", "失望", "拉胯", "坑人", "缺德", "不堪一击")


def classify(text):
    # Do not invert arbitrary negations or treat questions as expressed conviction.
    if re.search(r"[?？]|(?:不|没|别|不会|不能|不要|不是|没有).{0,3}(?:" + "|".join(POSITIVE + NEGATIVE) + r")", text.replace("不看好", "看空")):
        return "unknown"
    negative = any(word in text for word in NEGATIVE)
    positive = any(word in text.replace("不看好", "") for word in POSITIVE)
    return "mixed" if positive and negative else "bullish" if positive else "bearish" if negative else "unknown"


def summarize(symbol, items, *, partial=False):
    unique, duplicates = {}, 0
    for item in sorted(items, key=lambda item: (item.published_at, item.id), reverse=True):
        # Full-width variants deduplicate, but a question and an assertion differ.
        normalized = "".join(c for c in unicodedata.normalize("NFKC", item.text) if not c.isspace())
        if normalized in unique: duplicates += 1; continue
        unique[normalized] = item.model_copy(update={"sentiment": classify(item.text)})
    sample = list(unique.values())[:200]
    counts = {label: sum(item.sentiment == label for item in sample) for label in ("bullish", "bearish", "mixed", "unknown")}
    return SentimentReport(symbol=symbol, items=sample, counts=counts, sample_count=len(sample),
        post_count=sum(item.kind == "post_title" for item in sample), reply_count=sum(item.kind == "reply" for item in sample),
        duplicate_count=duplicates, sample_start=sample[-1].published_at if sample else None,
        sample_end=sample[0].published_at if sample else None, partial=partial)


class SentimentService:
    def __init__(self, provider, sessions, watchlist, *, timer=None):
        self.provider, self.watchlist = provider, watchlist
        self.store = AsyncTTLStore(ttl=60, stale_ttl=172800, maxsize=256, timer=timer,
            snapshots=SnapshotStore(sessions, "stock_sentiment", SentimentReport, max_age=timedelta(days=2)))
        self._slots = asyncio.Semaphore(2)
        self._failed_at = TTLCache(maxsize=256, ttl=172800, timer=timer or monotonic)

    async def get(self, symbol: str, *, prefer_cached=False) -> CachedResult[SentimentReport]:
        symbol = _validate_symbol(symbol)
        if prefer_cached:
            existing = await self.store.peek(symbol)
            if existing is not None:
                if symbol in self._failed_at and self._failed_at[symbol] == existing.cached_at:
                    return CachedResult(existing.data, stale=True, cached_at=existing.cached_at)
                return existing

        async def load():
            async with self._slots:
                batch = await self.provider.get_stock_comments(symbol)
            # Empty successful samples must not be confused with an outage.
            previous = await self.store.peek(symbol)
            now = utc_now()
            combined = {item.id: item for item in previous.data.items if now - timedelta(hours=24) <= item.published_at <= now} if previous else {}
            combined.update({item.id: item for item in batch.items if item.symbol == symbol and now - timedelta(hours=24) <= item.published_at <= now + timedelta(minutes=5)})
            return summarize(symbol, list(combined.values()), partial=batch.partial)

        result = await self.store.get(symbol, load)
        if result.stale: self._failed_at[symbol] = result.cached_at
        else: self._failed_at.pop(symbol, None)
        return result

    async def aclose(self): await self.store.aclose()


class SentimentCollector:
    """Independent minute cadence; never occupy the two-second quote lane."""
    def __init__(self, service, *, timer=monotonic):
        self.service, self.timer = service, timer
        self.attempted, self.pending = {}, {}
        self.runner = None

    def start(self): self.runner = asyncio.create_task(self._run(), name="watchlist-sentiment")

    async def _run(self):
        while True:
            try: await self.poll()
            except Exception: logger.exception("Forum collection scan failed")
            await asyncio.sleep(2)

    async def poll(self):
        symbols = {entry.symbol for entry in await run_in_threadpool(self.service.watchlist.list_entries)}
        self.attempted = {s: t for s, t in self.attempted.items() if s in symbols}
        self.pending = {s: task for s, task in self.pending.items() if not task.done()}
        for symbol in sorted(symbols, key=lambda s: (self.attempted.get(s, float("-inf")), s)):
            if len(self.pending) >= 2: break
            if symbol in self.pending or self.timer() - self.attempted.get(symbol, float("-inf")) < 60: continue
            self.attempted[symbol] = self.timer()
            self.pending[symbol] = asyncio.create_task(self._collect(symbol))

    async def _collect(self, symbol):
        try: await self.service.get(symbol)
        except DataSourceError: logger.warning("Forum sample unavailable: %s", symbol)
        except Exception: logger.exception("Forum sample collection failed")

    async def stop(self):
        tasks = list(self.pending.values()) + ([self.runner] if self.runner else [])
        for task in tasks: task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.pending.clear()
        self.runner = None

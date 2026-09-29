"""Persist, deduplicate and read a bounded local news collection."""

import asyncio
from datetime import date, datetime, time, timedelta, timezone
from time import monotonic

from sqlalchemy import delete, func, select
from starlette.concurrency import run_in_threadpool

from app.database.models import NewsFetchState, NewsRecord, NewsScopeItem, utc_now
from app.models.news import NewsBatch, NewsItem, NewsPage
from app.providers.base import MarketDataProvider
from app.providers.exceptions import DataSourceError
from app.services.reads import StockReadResult
from app.services.watchlist import WatchlistService

BEIJING = timezone(timedelta(hours=8))


def aware(value: datetime | None):
    return value.replace(tzinfo=timezone.utc) if value and value.tzinfo is None else value


class NewsService:
    def __init__(self, provider: MarketDataProvider, sessions, watchlist: WatchlistService,
                 *, background: bool = False, clock=utc_now, timer=monotonic):
        self._provider, self._sessions, self.watchlist = provider, sessions, watchlist
        self.background, self._clock, self._timer = background, clock, timer
        self._pending: dict[str, asyncio.Task] = {}
        self._retry: dict[str, tuple[int, float]] = {}
        self._write_lock = asyncio.Lock()
        self.collector = None

    def _state(self, scope: str):
        with self._sessions() as session:
            return session.get(NewsFetchState, scope)

    async def fetch(self, scope: str, *, force: bool = False):
        state = await run_in_threadpool(self._state, scope)
        if not force and state and state.fetched_at and not state.failed and (
            self._clock() - aware(state.fetched_at)
        ).total_seconds() < 300:
            return
        failure = self._retry.get(scope)
        if failure and self._timer() < failure[1]:
            if state and state.fetched_at:
                return
            raise DataSourceError("News source is waiting to retry")
        task = self._pending.get(scope)
        if task is None:
            task = asyncio.create_task(self._fetch(scope))
            self._pending[scope] = task
            task.add_done_callback(lambda completed: self._finish(scope, completed))
        await asyncio.shield(task)

    def _finish(self, scope, task):
        if self._pending.get(scope) is task:
            del self._pending[scope]
        if not task.cancelled():
            task.exception()

    async def _fetch(self, scope: str):
        attempted = self._clock()
        try:
            batch = await (self._provider.get_market_news() if scope == "market" else self._provider.get_stock_news(scope))
        except DataSourceError:
            count = min(self._retry.get(scope, (0, 0))[0] + 1, 5)
            self._retry[scope] = (count, self._timer() + min(2 ** count, 30))
            async with self._write_lock:
                await run_in_threadpool(self._save_failure, scope, attempted)
            raise
        self._retry.pop(scope, None)
        async with self._write_lock:
            await run_in_threadpool(self._save, scope, batch, attempted)

    def _save_failure(self, scope, attempted):
        with self._sessions.begin() as session:
            row = session.get(NewsFetchState, scope)
            if row is None:
                row = NewsFetchState(scope=scope)
                session.add(row)
            row.attempted_at, row.failed = attempted, True

    def _save(self, scope: str, batch: NewsBatch, attempted: datetime):
        saved, cutoff = self._clock(), self._clock() - timedelta(days=30)
        with self._sessions.begin() as session:
            for item in batch.items:
                if item.published_at < cutoff or item.published_at > saved + timedelta(days=1):
                    continue
                row = session.get(NewsRecord, item.id)
                if row is None:
                    row = NewsRecord(id=item.id)
                    session.add(row)
                # Search excerpts depend on the keyword: keep each scope's actual fragment.
                excerpts = dict((row.payload or {}).get("scope_excerpts", {}))
                excerpts[scope] = item.excerpt
                row.published_at = item.published_at
                row.payload = {**item.model_dump(mode="json"), "scope_excerpts": excerpts}
                session.flush()
                if session.get(NewsScopeItem, (scope, item.id)) is None:
                    session.add(NewsScopeItem(scope=scope, news_id=item.id))
            row = session.get(NewsFetchState, scope)
            if row is None:
                row = NewsFetchState(scope=scope)
                session.add(row)
            row.attempted_at, row.fetched_at, row.failed = attempted, saved, False
            row.partial, row.truncated = not batch.complete, batch.truncated
            session.flush()
            session.execute(delete(NewsRecord).where(NewsRecord.published_at < cutoff))
            excess = session.scalars(select(NewsRecord.id).order_by(
                NewsRecord.published_at.desc(), NewsRecord.id.desc()).offset(10000)).all()
            if excess:
                session.execute(delete(NewsRecord).where(NewsRecord.id.in_(excess)))
            # Removed watchlists retain their collected articles, not unlimited per-symbol state.
            stale_scopes = session.scalars(select(NewsFetchState.scope).where(
                NewsFetchState.attempted_at < cutoff)).all()
            if stale_scopes:
                session.execute(delete(NewsScopeItem).where(NewsScopeItem.scope.in_(stale_scopes)))
                session.execute(delete(NewsFetchState).where(NewsFetchState.scope.in_(stale_scopes)))

    async def get(self, symbol: str | None = None, *, page: int = 1, page_size: int = 10,
                  start: date | None = None, end: date | None = None, keyword: str = "", prefer_cached: bool = False):
        if not 1 <= page <= 1000 or not 1 <= page_size <= 50 or len(keyword) > 100:
            raise ValueError("Invalid news pagination/filter")
        if start and end and start > end:
            raise ValueError("News start date must not exceed end date")
        scope = symbol or "market"
        if prefer_cached:
            state = await run_in_threadpool(self._state, scope)
            if state and state.fetched_at:
                return await run_in_threadpool(self._read, scope, page, page_size, start, end, keyword)
        watched = self.background and symbol and any(
            item.symbol == symbol for item in await run_in_threadpool(self.watchlist.list_entries))
        if not watched:
            try:
                await self.fetch(scope)
            except DataSourceError:
                state = await run_in_threadpool(self._state, scope)
                if state is None or state.fetched_at is None:
                    raise
        return await run_in_threadpool(self._read, scope, page, page_size, start, end, keyword)

    def _read(self, scope, page, page_size, start, end, keyword):
        now = self._clock()
        with self._sessions() as session:
            status = session.get(NewsFetchState, scope)
            saved = aware(status.fetched_at) if status else None
            stale = bool(saved and (status.failed or (now - saved).total_seconds() >= 300))
            state = ("stale" if stale else "partial" if status.partial else "ready") if saved else (
                "unavailable" if status and status.failed else "warming")
            query = select(NewsRecord).join(NewsScopeItem).where(
                NewsScopeItem.scope == scope, NewsRecord.published_at >= now - timedelta(days=30))
            if start:
                query = query.where(NewsRecord.published_at >= datetime.combine(start, time.min, BEIJING).astimezone(timezone.utc))
            if end:
                query = query.where(NewsRecord.published_at < datetime.combine(end + timedelta(days=1), time.min, BEIJING).astimezone(timezone.utc))
            # Treat SQL wildcard characters as literal text; source excerpts are searchable metadata.
            if keyword.strip():
                query = query.where(NewsRecord.payload["title"].as_string().contains(keyword.strip(), autoescape=True) |
                                    func.coalesce(NewsRecord.payload["scope_excerpts"][scope].as_string(),
                                                  NewsRecord.payload["excerpt"].as_string()).contains(keyword.strip(), autoescape=True))
            total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
            rows = session.scalars(query.order_by(NewsRecord.published_at.desc(), NewsRecord.id.desc()).offset(
                (page - 1) * page_size).limit(page_size)).all()
            ids = [row.id for row in rows]
            associations = session.execute(select(NewsScopeItem.news_id, NewsScopeItem.scope).where(
                NewsScopeItem.news_id.in_(ids), NewsScopeItem.scope != "market")).all() if ids else []
            symbols = {news_id: [] for news_id in ids}
            for news_id, stock in associations:
                symbols[news_id].append(stock)
            items = [NewsItem.model_validate(row.payload).model_copy(update={
                "symbols": sorted(symbols[row.id]), "association": "market_column" if scope == "market" else "keyword_search",
                "excerpt": row.payload.get("scope_excerpts", {}).get(scope, row.payload.get("excerpt")),
            }) for row in rows]
            data = NewsPage(items=items, total=total, page=page, page_size=page_size,
                            has_more=page * page_size < total, source_truncated=bool(status and status.truncated),
                            partial=bool(status and status.partial), collection_state=state)
            return StockReadResult(data, stale=stale, cached_at=saved, state=state)

    async def aclose(self):
        tasks = list(self._pending.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._pending.clear()

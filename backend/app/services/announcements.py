"""Persist announcement metadata first, then proactively collect public text."""

import asyncio
from datetime import date, timedelta
from time import monotonic
from sqlalchemy import delete, func, select
from starlette.concurrency import run_in_threadpool
from app.database.models import AnnouncementRecord, AnnouncementScopeItem, AnnouncementFetchState, utc_now
from app.models.announcements import AnnouncementBatch, AnnouncementItem, AnnouncementPage
from app.providers.exceptions import DataSourceError
from app.services.news import aware, BEIJING
from app.services.reads import StockReadResult


class AnnouncementService:
    def __init__(self, provider, sessions, watchlist, *, background=False, clock=utc_now, timer=monotonic):
        self._provider, self._sessions, self.watchlist = provider, sessions, watchlist
        self.background, self._clock, self._timer = background, clock, timer
        self._pending, self._documents, self._retry = {}, {}, {}
        self._write_lock, self._text_slots = asyncio.Lock(), asyncio.Semaphore(2)
        self.collector = None

    def _state(self, scope):
        with self._sessions() as session:
            return session.get(AnnouncementFetchState, scope)

    async def fetch(self, scope, *, force=False):
        state = await run_in_threadpool(self._state, scope)
        if not force and state and state.fetched_at and not state.failed and (self._clock() - aware(state.fetched_at)).total_seconds() < 600:
            return
        failure = self._retry.get(scope)
        if failure and self._timer() < failure[1]:
            if state and state.fetched_at:
                return
            raise DataSourceError("Announcements waiting to retry")
        if scope not in self._pending:
            task = asyncio.create_task(self._fetch(scope))
            self._pending[scope] = task
            task.add_done_callback(lambda done: self._finish(self._pending, scope, done))
        await asyncio.shield(self._pending[scope])

    def _finish(self, tasks, key, task):
        if tasks.get(key) is task:
            tasks.pop(key)
        if not task.cancelled():
            task.exception()

    async def _fetch(self, scope):
        attempted = self._clock()
        try:
            batch = await self._provider.get_stock_announcements(scope)
        except DataSourceError:
            count = min(self._retry.get(scope, (0, 0))[0] + 1, 5)
            self._retry[scope] = (count, self._timer() + min(2 ** count, 30))
            async with self._write_lock:
                await run_in_threadpool(self._save_failure, scope, attempted)
            raise
        self._retry.pop(scope, None)
        async with self._write_lock:
            ids = await run_in_threadpool(self._save, scope, batch, attempted)
        # Shared document IDs are collected once even when associated with several stocks.
        for doc_id in ids:
            if doc_id not in self._documents:
                task = asyncio.create_task(self._content(doc_id))
                self._documents[doc_id] = task
                task.add_done_callback(lambda done, key=doc_id: self._finish(self._documents, key, done))

    def _save_failure(self, scope, attempted):
        with self._sessions.begin() as session:
            row = session.get(AnnouncementFetchState, scope)
            if row is None:
                row = AnnouncementFetchState(scope=scope)
                session.add(row)
            row.attempted_at, row.failed = attempted, True

    def _save(self, scope, batch: AnnouncementBatch, attempted):
        today = self._clock().astimezone(BEIJING).date()
        cutoff, ids = today - timedelta(days=90), []
        with self._sessions.begin() as session:
            for item in batch.items:
                if item.notice_date < cutoff or item.notice_date > today + timedelta(days=1):
                    continue
                row = session.get(AnnouncementRecord, item.id)
                previous = AnnouncementItem.model_validate(row.payload) if row else None
                if row is None:
                    row = AnnouncementRecord(id=item.id)
                    session.add(row)
                if previous:
                    updated = previous.model_copy(update={key: getattr(item, key) for key in
                        ("title", "notice_date", "disclosed_at", "url", "categories", "symbols")})
                    if previous.title != item.title or previous.notice_date != item.notice_date:
                        updated = updated.model_copy(update={"text_stale": bool(row.body)})
                else:
                    updated = item
                row.notice_date, row.payload = item.notice_date, updated.model_dump(mode="json", exclude={"text"})
                session.flush()
                # Associations come from explicit source stock codes, never headline keywords.
                session.execute(delete(AnnouncementScopeItem).where(AnnouncementScopeItem.announcement_id == item.id,
                    AnnouncementScopeItem.scope.not_in(item.symbols)))
                for symbol in item.symbols:
                    if session.get(AnnouncementScopeItem, (symbol, item.id)) is None:
                        session.add(AnnouncementScopeItem(scope=symbol, announcement_id=item.id))
                ids.append(item.id)
            status = session.get(AnnouncementFetchState, scope)
            if status is None:
                status = AnnouncementFetchState(scope=scope)
                session.add(status)
            status.attempted_at, status.fetched_at, status.failed = attempted, self._clock(), False
            status.partial, status.truncated = not batch.complete, batch.truncated
            session.flush()
            session.execute(delete(AnnouncementRecord).where(AnnouncementRecord.notice_date < cutoff))
            # At most 1,000 bodies, each at most 300,000 characters; 10,000 metadata records.
            excess = session.scalars(select(AnnouncementRecord.id).order_by(AnnouncementRecord.notice_date.desc(), AnnouncementRecord.id.desc()).offset(10000)).all()
            if excess:
                session.execute(delete(AnnouncementRecord).where(AnnouncementRecord.id.in_(excess)))
            body_excess = session.scalars(select(AnnouncementRecord).where(AnnouncementRecord.body.is_not(None)).order_by(AnnouncementRecord.notice_date.desc(), AnnouncementRecord.id.desc()).offset(1000)).all()
            for row in body_excess:
                row.body = None
                row.payload = {**row.payload, "text_status": "unavailable", "text_reason": "正文缓存容量上限，保留原文入口", "text_length": 0}
            eligible = set(session.scalars(select(AnnouncementRecord.id).order_by(AnnouncementRecord.notice_date.desc(), AnnouncementRecord.id.desc()).limit(1000)).all())
            for doc_id in set(ids) - eligible:
                record = session.get(AnnouncementRecord, doc_id)
                record.payload = {**record.payload, "text_status": "unavailable", "text_reason": "正文缓存容量上限，保留原文入口"}
            old_scopes = session.scalars(select(AnnouncementFetchState.scope).where(AnnouncementFetchState.attempted_at < self._clock() - timedelta(days=90))).all()
            if old_scopes:
                session.execute(delete(AnnouncementFetchState).where(AnnouncementFetchState.scope.in_(old_scopes)))
            return [doc_id for doc_id in ids if doc_id in eligible]

    def _document(self, doc_id, symbol=None):
        with self._sessions() as session:
            if symbol and session.get(AnnouncementScopeItem, (symbol, doc_id)) is None:
                return None
            row = session.get(AnnouncementRecord, doc_id)
            if row is None or row.notice_date < self._clock().astimezone(BEIJING).date() - timedelta(days=90):
                return None
            item = AnnouncementItem.model_validate({**row.payload, "text": row.body})
            status = session.get(AnnouncementFetchState, symbol) if symbol else None
            outdated = bool(status and (status.failed or status.fetched_at and
                (self._clock() - aware(status.fetched_at)).total_seconds() >= 600))
            outdated |= bool(item.text_fetched_at and (self._clock() - item.text_fetched_at).total_seconds() >= 86400)
            return item.model_copy(update={"text_stale": item.text_stale or bool(item.text and outdated)})

    async def _content(self, doc_id):
        async with self._text_slots:
            previous = await run_in_threadpool(self._document, doc_id)
            if previous is None:
                return
            try:
                item = await self._provider.get_announcement_content(previous, previous)
            except DataSourceError:
                item = previous.model_copy(update={"text_status": previous.text_status if previous.text else "unavailable",
                    "text_stale": bool(previous.text), "text_reason": "正文源暂不可用，已保留原文入口及此前成功正文"})
            async with self._write_lock:
                await run_in_threadpool(self._save_content, item)

    def _save_content(self, item):
        with self._sessions.begin() as session:
            row = session.get(AnnouncementRecord, item.id)
            if row:
                # Metadata may have been updated by another symbol while text was downloading.
                fields = ("attachment_urls", "text_status", "text_source", "text_reason", "text_hash", "source_text_hash",
                          "text_length", "pages_extracted", "total_pages", "text_fetched_at", "text_stale")
                row.payload = {**row.payload, **item.model_dump(mode="json", include=set(fields))}
                row.body = item.text
                if row.payload["title"] != item.title or row.notice_date != item.notice_date:
                    row.payload = {**row.payload, "text_stale": bool(item.text)}
                # Concurrent batches can change the newest 1,000 while this PDF is downloading.
                eligible = set(session.scalars(select(AnnouncementRecord.id).order_by(AnnouncementRecord.notice_date.desc(), AnnouncementRecord.id.desc()).limit(1000)).all())
                if item.id not in eligible:
                    row.body = None
                    row.payload = {**row.payload, "text_status": "unavailable", "text_reason": "正文缓存容量上限，保留原文入口", "text_length": 0}

    async def get(self, symbol, *, page=1, page_size=10, start: date | None = None, end: date | None = None, keyword="", category=""):
        if not 1 <= page <= 1000 or not 1 <= page_size <= 50 or len(keyword) > 100 or len(category) > 40:
            raise ValueError("Invalid announcement pagination/filter")
        if start and end and start > end:
            raise ValueError("公告起始日期不得晚于结束日期")
        watched = self.background and any(item.symbol == symbol for item in await run_in_threadpool(self.watchlist.list_entries))
        if not watched:
            try:
                await self.fetch(symbol)
            except DataSourceError:
                state = await run_in_threadpool(self._state, symbol)
                if not state or not state.fetched_at:
                    raise
        return await run_in_threadpool(self._read, symbol, page, page_size, start, end, keyword, category)

    def _read(self, scope, page, page_size, start, end, keyword, category):
        with self._sessions() as session:
            status = session.get(AnnouncementFetchState, scope)
            saved = aware(status.fetched_at) if status else None
            stale = bool(saved and (status.failed or (self._clock() - saved).total_seconds() >= 600))
            state = ("stale" if stale else "partial" if status.partial else "ready") if saved else ("unavailable" if status and status.failed else "warming")
            query = select(AnnouncementRecord).join(AnnouncementScopeItem).where(AnnouncementScopeItem.scope == scope,
                AnnouncementRecord.notice_date >= self._clock().astimezone(BEIJING).date() - timedelta(days=90))
            category_options = {}
            for values in session.scalars(query.with_only_columns(AnnouncementRecord.payload["categories"])):
                for value in values or []:
                    category_options[value["code"]] = value
            if start:
                query = query.where(AnnouncementRecord.notice_date >= start)
            if end:
                query = query.where(AnnouncementRecord.notice_date <= end)
            if keyword.strip():
                query = query.where(AnnouncementRecord.payload["title"].as_string().contains(keyword.strip(), autoescape=True))
            if category.strip():
                # JSON array membership, exact source category code (no substring matches).
                categories = func.json_each(AnnouncementRecord.payload, "$.categories").table_valued("value")
                query = query.where(select(1).select_from(categories).where(func.json_extract(categories.c.value, "$.code") == category).exists())
            total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
            rows = session.scalars(query.order_by(AnnouncementRecord.notice_date.desc(), AnnouncementRecord.id.desc()).offset((page - 1) * page_size).limit(page_size)).all()
            items = [AnnouncementItem.model_validate(row.payload) for row in rows]
            data = AnnouncementPage(items=items, categories=list(category_options.values()), total=total, page=page, page_size=page_size, has_more=page * page_size < total,
                source_truncated=bool(status and status.truncated), partial=bool(status and status.partial), collection_state=state)
            return StockReadResult(data, stale=stale, cached_at=saved, state=state)

    async def document(self, symbol, doc_id):
        await self.get(symbol, page_size=1)
        # Watchlist text is proactively collected; opening it does not initiate a new source request.
        return await run_in_threadpool(self._document, doc_id, symbol)

    async def aclose(self):
        tasks = list(self._pending.values()) + list(self._documents.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._pending.clear()
        self._documents.clear()

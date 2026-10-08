"""Only explicit POST requests start classification; reads never call the model."""

import asyncio
import hashlib
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import delete, select
from pydantic import ValidationError

from app.database.models import SentimentAnalysisRecord, SentimentLabel, utc_now
from app.models.sentiment_analysis import AnalysisView, SentimentAnalysis
from app.services.sentiment_classifier import PROMPT_VERSION


class NoSentimentSamples(Exception):
    pass


class SentimentNotConfigured(Exception):
    pass


class SentimentAnalysisService:
    def __init__(self, raw, sessions, classifier):
        self.raw, self.sessions, self.classifier = raw, sessions, classifier
        self._jobs, self._views = {}, {}
        self._slots = asyncio.Semaphore(2)

    async def view(self, symbol):
        if symbol not in self._views:
            def read():
                with self.sessions() as session:
                    row = session.get(SentimentAnalysisRecord, symbol)
                    try:
                        return SentimentAnalysis.model_validate(row.payload) if row else None
                    except ValidationError:
                        return None
            result = await asyncio.to_thread(read)
            self._views.setdefault(symbol, AnalysisView(symbol=symbol,
                status="ready" if result else "idle" if self.classifier.configured else "unconfigured", result=result))
            while len(self._views) > 256:
                old = next((key for key in self._views if key != symbol and key not in self._jobs), None)
                if old is None:
                    break
                self._views.pop(old)
        return self._views[symbol].model_copy(deep=True, update={"checked_at": utc_now()})

    async def start(self, symbol):
        if not self.classifier.configured:
            raise SentimentNotConfigured()
        if symbol in self._jobs:
            return await self.view(symbol)
        cached = await self.raw.store.peek(symbol)
        now = utc_now()
        items = [item for item in cached.data.items if now - timedelta(hours=24) <= item.published_at <= now][:200] if cached else []
        if not items or not cached.cached_at:
            raise NoSentimentSamples()
        previous = await self.view(symbol)
        # Recheck after database awaits, so simultaneous button clicks share one job.
        if symbol in self._jobs:
            return await self.view(symbol)
        if len(self._jobs) >= 8:
            raise RuntimeError("已有较多统计任务，请稍后再试。")
        job_id = uuid4().hex
        self._views[symbol] = AnalysisView(symbol=symbol, status="running", job_id=job_id,
                                         total=len(items), result=previous.result)
        task = asyncio.create_task(self._run(symbol, items, cached, job_id))
        self._jobs[symbol] = task
        return await self.view(symbol)

    def _key(self, symbol, text):
        return hashlib.sha256((symbol + "\0" + self.classifier.model_name + "\0" + PROMPT_VERSION + "\0" + text).encode()).hexdigest()

    async def _run(self, symbol, items, cached, job_id):
        try:
            async with self._slots, asyncio.timeout(120):
                keys = {item.id: self._key(symbol, item.text) for item in items}
                def read_labels():
                    with self.sessions() as session:
                        rows = session.scalars(select(SentimentLabel).where(
                            SentimentLabel.key.in_(list(keys.values())),
                            SentimentLabel.classified_at >= utc_now() - timedelta(days=30)))
                        return {row.key: row.label for row in rows if row.label in ("positive", "negative", "neutral")}
                known = await asyncio.to_thread(read_labels)
                labels = {item.id: known[keys[item.id]] for item in items if keys[item.id] in known}
                reused = len(labels)
                self._views[symbol].completed = reused
                missing = [item for item in items if item.id not in labels]
                for offset in range(0, len(missing), 40):
                    batch = missing[offset:offset + 40]
                    predicted = await self.classifier.classify([{"id": item.id, "text": item.text} for item in batch])
                    if set(predicted) != {item.id for item in batch} or any(
                        label not in ("positive", "negative", "neutral") for label in predicted.values()):
                        raise ValueError("invalid classification coverage")
                    def save_labels():
                        with self.sessions.begin() as session:
                            for item in batch:
                                session.merge(SentimentLabel(key=keys[item.id], label=predicted[item.id], classified_at=utc_now()))
                            session.flush()
                            session.execute(delete(SentimentLabel).where(SentimentLabel.classified_at < utc_now() - timedelta(days=30)))
                            excess = session.scalars(select(SentimentLabel.key).order_by(
                                SentimentLabel.classified_at.desc(), SentimentLabel.key).offset(10000)).all()
                            if excess:
                                session.execute(delete(SentimentLabel).where(SentimentLabel.key.in_(excess)))
                    await asyncio.to_thread(save_labels)
                    labels.update(predicted)
                    self._views[symbol].completed = len(labels)
                result = SentimentAnalysis(symbol=symbol, model=self.classifier.model_name, sample_count=len(items),
                    counts={label: sum(value == label for value in labels.values()) for label in ("positive", "negative", "neutral")},
                    labels=labels, reused_count=reused, sample_start=min(item.published_at for item in items),
                    preview=[{**item.model_dump(), "sentiment": labels[item.id]} for item in items[:8]],
                    sample_end=max(item.published_at for item in items), source_cached_at=cached.cached_at,
                    source_stale=cached.stale, analysed_at=utc_now())
                def save_result():
                    with self.sessions.begin() as session:
                        session.merge(SentimentAnalysisRecord(symbol=symbol, payload=result.model_dump(mode="json")))
                        session.flush()
                        excess = session.scalars(select(SentimentAnalysisRecord.symbol).order_by(
                            SentimentAnalysisRecord.payload["analysed_at"].as_string().desc(),
                            SentimentAnalysisRecord.symbol).offset(256)).all()
                        if excess:
                            session.execute(delete(SentimentAnalysisRecord).where(SentimentAnalysisRecord.symbol.in_(excess)))
                await asyncio.to_thread(save_result)
                self._views[symbol] = AnalysisView(symbol=symbol, status="ready", job_id=job_id,
                                                 completed=len(items), total=len(items), result=result)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Keep the last complete result; missing labels never become neutral.
            self._views[symbol].status = "failed"
            self._views[symbol].error = "情绪统计未完成，请重试。上次成功结果仍保留。"
        finally:
            self._jobs.pop(symbol, None)

    async def aclose(self):
        tasks = list(self._jobs.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.classifier.aclose()

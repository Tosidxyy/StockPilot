"""Retrieve bounded evidence from collected SQLite records, without HTTP requests."""
import asyncio
import hashlib
import html
import json
import re
import unicodedata
from datetime import date, datetime, timedelta

from sqlalchemy import delete, exists, func, or_, select, text
from starlette.concurrency import run_in_threadpool
from app.database.models import (DocumentIndex, DocumentChunk, NewsRecord, NewsScopeItem,
    NewsFetchState, AnnouncementRecord, AnnouncementScopeItem, AnnouncementFetchState, utc_now)
from app.services.news import BEIJING, aware

MAX_CHUNKS = 100000
MAX_DOCUMENTS = 1000


def normalize(value):
    value = html.unescape(value or "")
    value = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", "", value, flags=re.S | re.I)
    value = re.sub(r"<[^>]+>", "", value)
    value = unicodedata.normalize("NFKC", value).replace("\r\n", "\n").replace("\r", "\n")
    value = "".join(c for c in value if not unicodedata.category(c).startswith("C") or c in "\n\t")
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"[^\S\n]+", " ", value)).strip()


def chunks(value):
    start = 0
    while start < len(value):
        end = min(start + 800, len(value))
        boundary = value.rfind("\n", start + 400, end)
        if end < len(value) and boundary > start:
            end = boundary + 1
        yield start, end, value[start:end]
        if end == len(value):
            break
        start = end - 120


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class DocumentService:
    def __init__(self, sessions, *, clock=utc_now):
        self._sessions, self._clock = sessions, clock
        self._lock = asyncio.Lock()

    async def search(self, symbol, query, *, kind="all", start: date | None = None,
                     end: date | None = None, limit=6):
        if re.fullmatch(r"[03468][0-9]{5}", symbol or "") is None:
            raise ValueError("股票代码必须为六位 A 股代码")
        query = normalize(query)
        if not 1 <= len(query) <= 100 or len(query.split()) > 8 or kind not in ("all", "news", "announcement") or not 1 <= limit <= 10:
            raise ValueError("检索关键词需1–100字、最多8个词，limit需1–10")
        if start and end and start > end:
            raise ValueError("起始日期不得晚于结束日期")
        async with self._lock:
            return await run_in_threadpool(self._search, symbol, query, kind, start, end, limit)

    async def recent(self, symbol, *, kind="all", start=None, end=None, limit=6, document_id=None, document_ids=None):
        """Browse actual first chunks for summaries; no keyword or source request."""
        if (symbol != "market" and re.fullmatch(r"[03468][0-9]{5}", symbol or "") is None) or kind not in ("all","news","announcement"):
            raise ValueError("资料范围无效")
        if symbol == "market" and kind != "news" or not 1 <= limit <= 10 or start and end and start > end:
            raise ValueError("资料筛选无效")
        if document_ids is not None and len(document_ids) > 50:
            raise ValueError("资料数量超限")
        async with self._lock:
            return await run_in_threadpool(self._search,symbol,"",kind,start,end,limit,document_id,document_ids)

    def _sources(self, session, symbol, kind, now):
        documents, states, unavailable, truncated = [], {}, 0, False
        kinds = ("news", "announcement") if kind == "all" else (kind,)
        for category in kinds:
            model, relation, state_model, field = (NewsRecord, NewsScopeItem, NewsFetchState, NewsRecord.published_at) if category == "news" else (
                AnnouncementRecord, AnnouncementScopeItem, AnnouncementFetchState, AnnouncementRecord.notice_date)
            id_field = relation.news_id if category == "news" else relation.announcement_id
            cutoff = now - timedelta(days=30) if category == "news" else now.astimezone(BEIJING).date() - timedelta(days=90)
            rows = session.scalars(select(model).join(relation, id_field == model.id).where(
                relation.scope == symbol, field >= cutoff).order_by(field.desc(), model.id.desc()).limit(MAX_DOCUMENTS + 1)).all()
            truncated |= len(rows) > MAX_DOCUMENTS
            state = session.get(state_model, symbol)
            saved = aware(state.fetched_at) if state else None
            stale = bool(saved and (state.failed or (now - saved).total_seconds() >= (300 if category == "news" else 600)))
            states[category] = {"state": "unavailable" if not saved and state and state.failed else "not_collected" if not saved else
                "stale" if stale else "partial" if state.partial else "ready",
                "cached_at": saved.isoformat() if saved else None, "source_truncated": bool(state and state.truncated)}
            for row in rows[:MAX_DOCUMENTS]:
                p = row.payload
                title = normalize(p["title"])
                if category == "news":
                    body = normalize(p.get("scope_excerpts", {}).get(symbol, p.get("excerpt")))
                    date_value = aware(row.published_at).astimezone(BEIJING).date()
                    status, association = "excerpt", "market_column" if symbol == "market" else "keyword_search"
                    published = aware(row.published_at).isoformat()
                    body_stale = stale
                else:
                    body = normalize(row.body)
                    date_value, published = row.notice_date, p.get("disclosed_at")
                    status, association = p.get("text_status", "unavailable"), "source_security_code"
                    body_saved = p.get("text_fetched_at")
                    old_text = bool(body_saved and (now - aware(datetime.fromisoformat(body_saved))).total_seconds() >= 86400)
                    body_stale = stale or bool(p.get("text_stale")) or old_text
                if not body:
                    unavailable += 1
                bounded = body[:300000] or title
                metadata = {"document_id": row.id, "kind": category, "symbol": symbol, "title": title,
                    "source": p.get("source", "东方财富"), "url": p["url"], "date": date_value.isoformat(),
                    "published_at": published, "association": association,
                    "text_status": status if body else "metadata_only", "text_stale": body_stale,
                    "body_available": bool(body), "index_text_truncated": len(body) > 300000,
                    "text_reason": p.get("text_reason"), "text_cached_at": p.get("text_fetched_at") if category == "announcement" else None}
                key = digest(f"{category}:{symbol}:{row.id}")
                # Freshness is evaluated per read, not baked into the indexed text version.
                fingerprint = digest(json.dumps({k:v for k,v in metadata.items() if k != "text_stale"}, sort_keys=True, ensure_ascii=False) + bounded)
                documents.append((key, fingerprint, metadata, bounded))
        return documents, states, unavailable, truncated

    def _prune(self, session, now):
        for kind, relation, id_field, cutoff in (
            ("news", NewsScopeItem, NewsScopeItem.news_id, now.astimezone(BEIJING).date() - timedelta(days=30)),
            ("announcement", AnnouncementScopeItem, AnnouncementScopeItem.announcement_id, now.astimezone(BEIJING).date() - timedelta(days=90))):
            associated = exists(select(id_field).where(id_field == DocumentIndex.document_id, relation.scope == DocumentIndex.symbol))
            session.execute(delete(DocumentIndex).where(DocumentIndex.kind == kind,
                or_(DocumentIndex.date < cutoff, ~associated)))

    def _search(self, symbol, query, kind, start, end, limit, document_id=None, document_ids=None):
        now = self._clock()
        with self._sessions.begin() as session:
            documents, states, unavailable, truncated = self._sources(session, symbol, kind, now)
            self._prune(session, now)
            current = {r.key:r for r in session.scalars(select(DocumentIndex).where(DocumentIndex.symbol == symbol)).all()}
            for key, fingerprint, metadata, body in documents:
                former = current.get(key)
                if former and former.fingerprint == fingerprint:
                    continue
                if former:
                    session.execute(delete(DocumentIndex).where(DocumentIndex.key == key))
                session.add(DocumentIndex(key=key, symbol=symbol, kind=metadata["kind"], document_id=metadata["document_id"],
                    date=date.fromisoformat(metadata["date"]), fingerprint=fingerprint, payload=metadata))
                session.flush()
                session.add_all([DocumentChunk(document_key=key, evidence_id="E"+digest(f"{key}:{fingerprint}:{i}:{part}")[:24],
                    ordinal=i, start=a, end=b, title=metadata["title"], text=part) for i,(a,b,part) in enumerate(chunks(body))])
            session.flush()
            # Derived index has a global bound; source records remain governed by their own retention.
            total = session.scalar(select(func.count()).select_from(DocumentChunk)) or 0
            if total > MAX_CHUNKS:
                sizes = session.execute(select(DocumentIndex.key,func.count(DocumentChunk.id)).join(DocumentChunk).group_by(
                    DocumentIndex.key).order_by(DocumentIndex.date,DocumentIndex.key)).all()
                for key, size in sizes:
                    if total <= MAX_CHUNKS: break
                    session.execute(delete(DocumentIndex).where(DocumentIndex.key == key)); total -= size
            active_keys = set(session.scalars(select(DocumentIndex.key).where(DocumentIndex.symbol == symbol)).all())
            truncated |= any(key not in active_keys for key, *_ in documents)
            # Source metadata, current freshness and version are authoritative even for unchanged chunks.
            live = {key:(fingerprint,meta) for key,fingerprint,meta,_ in documents}
            terms = query.split()
            fts = bool(session.scalar(text("SELECT count(*) FROM sqlite_master WHERE name='document_fts' AND type='table'")))
            use_fts = fts and bool(terms) and all(len(t) >= 3 for t in terms)
            conditions = [DocumentIndex.symbol == symbol]
            if kind != "all": conditions.append(DocumentIndex.kind == kind)
            if start: conditions.append(DocumentIndex.date >= start)
            if end: conditions.append(DocumentIndex.date <= end)
            if document_id: conditions.append(DocumentIndex.document_id == document_id)
            if document_ids is not None: conditions.append(DocumentIndex.document_id.in_(document_ids))
            if not query and not document_id: conditions.append(DocumentChunk.ordinal == 0)
            request = select(DocumentChunk,DocumentIndex).join(DocumentIndex)
            if use_fts:
                expression = " AND ".join('"'+t.replace('"','""')+'"' for t in terms)
                request = request.where(DocumentChunk.id.in_(select(text("rowid")).select_from(text("document_fts")).where(
                    text("document_fts MATCH :phrase")))).params(phrase=expression)
            else:
                for term in terms:
                    request = request.where(or_(DocumentChunk.text.contains(term,autoescape=True),DocumentChunk.title.contains(term,autoescape=True)))
            rows = session.execute(request.where(*conditions).order_by(DocumentIndex.date.desc(),
                DocumentIndex.payload["published_at"].as_string().desc(),DocumentIndex.document_id.desc(),
                DocumentIndex.key,DocumentChunk.ordinal).limit(201)).all()
            candidates_truncated = len(rows) > 200
            evidence, per_doc, seen_text = [], {}, set()
            for chunk, doc in rows[:200]:
                if doc.key not in live or live[doc.key][0] != doc.fingerprint: continue
                per_document = 10 if document_id else 1 if not query else 2
                if per_doc.get(doc.key,0) >= per_document or digest(chunk.text) in seen_text: continue
                meta = live[doc.key][1]
                # Exact literal post-filter makes FTS and short-keyword behavior consistent.
                if not all(t.casefold() in (chunk.title+' '+chunk.text).casefold() for t in terms): continue
                evidence.append({**meta,"evidence_id":chunk.evidence_id,"ordinal":chunk.ordinal,
                    "start":chunk.start,"end":chunk.end,"text":chunk.text})
                per_doc[doc.key]=per_doc.get(doc.key,0)+1; seen_text.add(digest(chunk.text))
                if len(evidence) == limit: break
            earliest = now.astimezone(BEIJING).date() - timedelta(days=30 if kind == "news" else 90)
            outside = bool(start and start < earliest or end and end < earliest or start and start > now.astimezone(BEIJING).date())
            return {"symbol":symbol,"query":query,"kind":kind,"start":start.isoformat() if start else None,
                "end":end.isoformat() if end else None,"evidence":evidence,"source_states":states,
                "document_id": document_id, "limit": limit,
                "body_unavailable_documents":unavailable,"index_truncated":truncated,
                "candidates_truncated":candidates_truncated,"outside_window":outside,
                "search_mode":"fts5_trigram" if use_fts else "literal_keywords",
                "coverage":"仅本地已采集资料：新闻30天的标题/来源片段（非全文），公告90天的可用提取文本；每类每股最近1000篇、索引最多100000片段。关键词按空格分隔、全部匹配，不是语义搜索。",
                "reason": "ok" if evidence else "outside_window" if outside else "no_local_documents" if not documents else "no_match",
                "untrusted_content":True}

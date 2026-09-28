"""EastMoney public announcements; all upstream fields stay in this adapter."""

import hashlib
import io
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit
import httpx
from pypdf import PdfReader, apply_configuration
from starlette.concurrency import run_in_threadpool
from app.models.announcements import AnnouncementBatch, AnnouncementCategory, AnnouncementItem
from app.providers.exceptions import DataSourceError, ProviderTimeoutError

BEIJING = timezone(timedelta(hours=8))
MAX_PDF_BYTES = 10 * 1024 * 1024
MAX_TEXT = 300_000
MAX_PAGES = 150


def attachment_url(value: str) -> str:
    parts = urlsplit(value)
    if parts.scheme != "https" or parts.hostname != "pdf.dfcfw.com" or parts.username or parts.password or parts.port not in (None, 443):
        raise ValueError("Invalid public attachment URL")
    if not parts.path.startswith("/pdf/") or not parts.path.lower().endswith(".pdf"):
        raise ValueError("Invalid PDF path")
    return value


def text_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def parse_item(row: dict) -> AnnouncementItem:
    doc_id = row["art_code"]
    if not re.fullmatch(r"AN[0-9]{18}", doc_id):
        raise ValueError("Invalid announcement ID")
    symbols = sorted({entry["stock_code"] for entry in row["codes"]
                      if re.fullmatch(r"[03468][0-9]{5}", entry["stock_code"])})
    if not symbols or not row["title"].strip():
        raise ValueError("Missing A-share association/title")
    disclosed = row.get("display_time")
    return AnnouncementItem(id=doc_id, title=row["title"].strip(),
        notice_date=datetime.strptime(row["notice_date"][:10], "%Y-%m-%d").date(),
        disclosed_at=datetime.strptime(disclosed[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=BEIJING).astimezone(timezone.utc) if disclosed else None,
        url=f"https://data.eastmoney.com/notices/detail/{symbols[0]}/{doc_id}.html", symbols=symbols,
        categories=[AnnouncementCategory(code=c["column_code"], name=c["column_name"]) for c in row.get("columns", [])])


def extract_pdf(raw: bytes) -> dict:
    with apply_configuration(maximum_declared_stream_length=MAX_PDF_BYTES,
        zlib_maximum_output_length=2 * 1024 * 1024, lzw_maximum_output_length=2 * 1024 * 1024,
        array_based_stream_maximum_output_length=2 * 1024 * 1024,
        run_length_maximum_output_length=2 * 1024 * 1024, page_tree_maximum_entries=10000,
        xform_maximum_invocations_per_extraction=1000):
        return _extract_pdf(raw)


def _extract_pdf(raw: bytes) -> dict:
    if not raw.startswith(b"%PDF-") or len(raw) > MAX_PDF_BYTES:
        raise ValueError("Invalid/oversized PDF")
    reader = PdfReader(io.BytesIO(raw), strict=False)
    if reader.is_encrypted:
        raise ValueError("Encrypted PDF")
    total, chunks, count, blank, length = len(reader.pages), [], 0, False, 0
    for page in reader.pages[:MAX_PAGES]:
        # Bound decoded streams as well as the downloaded file.
        contents = page.get_contents()
        if contents and len(contents.get_data()) > 2 * 1024 * 1024:
            break
        value = (page.extract_text() or "").replace("\x00", "").strip()
        blank |= not bool(value)
        remaining = MAX_TEXT - length
        chunks.append(value[:remaining])
        length += len(chunks[-1])
        count += 1
        if len(value) > remaining or length >= MAX_TEXT:
            break
    joined = "\n\n".join(chunks).strip()
    value = joined[:MAX_TEXT]
    complete = count == total and not blank and len(joined) < MAX_TEXT and length < MAX_TEXT
    return dict(text=value or None, text_status="ready" if value and complete else "partial" if value else "unavailable",
                text_reason=None if complete and value else "附件存在空白/扫描页或达到提取上限，未确认完整正文",
                pages_extracted=count, total_pages=total, text_length=len(value),
                text_hash=text_hash(value) if value else None, text_source="public_pdf")


class EastMoneyAnnouncements:
    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    async def _json(self, url, params):
        try:
            response = await self.client.get(url, params=params, timeout=15,
                                            headers={"Referer": "https://data.eastmoney.com/"})
            response.raise_for_status()
            payload = response.json()
            if payload.get("success") != 1 or not isinstance(payload.get("data"), dict):
                raise ValueError("Invalid announcement response")
            return payload["data"]
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError("Announcement source timed out") from exc
        except (httpx.HTTPError, ValueError, TypeError, AttributeError) as exc:
            raise DataSourceError("Announcement source unavailable") from exc

    async def collect(self, symbol: str) -> AnnouncementBatch:
        items, complete, truncated = {}, True, False
        cutoff = (datetime.now(BEIJING) - timedelta(days=90)).date()
        for page in range(1, 6):
            try:
                data = await self._json("https://np-anotice-stock.eastmoney.com/api/security/ann",
                    dict(sr=-1, page_size=20, page_index=page, ann_type="A", stock_list=symbol))
                rows = data["list"]
                if not isinstance(rows, list):
                    raise ValueError("Invalid announcement list")
            except (DataSourceError, KeyError, ValueError) as exc:
                if page == 1:
                    raise DataSourceError("Announcement list unavailable") from exc
                complete, truncated = False, True
                break
            older = False
            for row in rows:
                try:
                    item = parse_item(row)
                    if symbol not in item.symbols:
                        complete = False
                        continue
                    if item.notice_date < cutoff:
                        older = True
                        continue
                    items[item.id] = item
                except (KeyError, TypeError, ValueError):
                    complete = False
            if older or len(rows) < 20:
                break
            if page == 5:
                truncated = int(data.get("total_hits", 101)) > 100
        return AnnouncementBatch(items=list(items.values()), complete=complete, truncated=truncated)

    async def _pdf(self, url):
        attachment_url(url)
        chunks, size = [], 0
        async with self.client.stream("GET", url, timeout=20, follow_redirects=False) as response:
            response.raise_for_status()
            if int(response.headers.get("content-length", 0)) > MAX_PDF_BYTES:
                raise ValueError("PDF too large")
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > MAX_PDF_BYTES:
                    raise ValueError("PDF too large")
                chunks.append(chunk)
        return await run_in_threadpool(extract_pdf, b"".join(chunks))

    async def content(self, item: AnnouncementItem, previous: AnnouncementItem | None = None) -> AnnouncementItem:
        now = datetime.now(timezone.utc)
        data = await self._json("https://np-cnotice-stock.eastmoney.com/api/content/ann",
                                dict(art_code=item.id, client_source="web", page_index=1))
        if data.get("art_code") != item.id:
            raise DataSourceError("Announcement document mismatch")
        if not isinstance(data.get("attach_list", []), list) or not isinstance(data.get("notice_content", ""), (str, type(None))):
            raise DataSourceError("Invalid announcement content")
        urls = []
        for candidate in [data.get("attach_url"), *(entry.get("attach_url") for entry in data.get("attach_list", []) if isinstance(entry, dict))]:
            try:
                url = attachment_url(candidate or "")
                if url not in urls:
                    urls.append(url)
            except (ValueError, TypeError, AttributeError):
                continue
        source_text = str(data.get("notice_content") or "").strip()[:MAX_TEXT]
        fingerprint = text_hash(source_text)
        if previous and not previous.text_stale and previous.text_status == "ready" and previous.attachment_urls == urls and previous.source_text_hash == fingerprint and previous.text_fetched_at and (now - previous.text_fetched_at).total_seconds() < 86400:
            return previous.model_copy(update={"text_stale": False})
        result = dict(text=source_text or None, text_status="partial" if source_text else "unavailable",
            text_source="source_excerpt" if source_text else None, text_reason="网页正文未确认完整，公开附件未能完整提取",
            text_length=len(source_text), text_hash=text_hash(source_text) if source_text else None,
            pages_extracted=0, total_pages=None)
        # Multiple attachments may be different documents; never call the first PDF all attachments.
        if urls:
            try:
                result = await self._pdf(urls[0])
                if len(urls) > 1 and result["text_status"] == "ready":
                    result.update(text_status="partial", text_reason="仅提取首个附件，其他附件请查看原文")
                if not result["text"] and source_text:
                    result.update(text=source_text, text_status="partial", text_source="source_excerpt",
                                  text_length=len(source_text), text_hash=text_hash(source_text))
            except Exception:
                # Malformed public PDFs must not discard otherwise valid announcement metadata.
                if previous and previous.text and previous.text_status == "ready":
                    return previous.model_copy(update={"attachment_urls": urls, "text_stale": True,
                        "text_reason": "本次公开附件提取失败，显示此前成功正文，未确认最新版本"})
        return item.model_copy(update={**result, "attachment_urls": urls, "source_text_hash": fingerprint,
                                       "text_fetched_at": now, "text_stale": False})

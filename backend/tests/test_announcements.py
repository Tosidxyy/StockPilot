import asyncio
import io
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic_ai import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.messages import ToolReturnPart
from pydantic_ai.models.function import FunctionModel
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from sqlalchemy import func, select

from app.agent.tools import get_stock_announcements
from app.database.models import AnnouncementRecord, AnnouncementScopeItem
from app.database.session import create_database_engine, create_session_factory, init_db
from app.main import create_app
from app.models.announcements import AnnouncementBatch, AnnouncementItem, AnnouncementCategory
from app.providers.announcements import EastMoneyAnnouncements, parse_item, extract_pdf, attachment_url
from app.providers.exceptions import DataSourceError
from app.services.announcements import AnnouncementService
from app.services.announcement_collector import AnnouncementCollector
from app.services.watchlist import WatchlistService
from test_api import FakeProvider
from test_services import Clock


def item(index=1, **updates):
    today = datetime.now(timezone(timedelta(hours=8))).date()
    return AnnouncementItem(id=f"AN20260928{index:010d}", title=f"公告 {index}", notice_date=today,
        url=f"https://data.eastmoney.com/notices/detail/600519/AN20260928{index:010d}.html",
        symbols=["600519", "300750"], categories=[AnnouncementCategory(code="001", name="定期报告")]).model_copy(update=updates)


def source_row(index=1, **updates):
    return {"art_code": item(index).id, "title": "测试公告", "notice_date": str(item().notice_date) + " 00:00:00",
        "display_time": "2026-09-27 20:30:11:380", "codes": [{"stock_code": "600519"}, {"stock_code": "03750"}],
        "columns": [{"column_code": "001", "column_name": "定期报告"}], **updates}


def pdf(*texts, encrypted=False):
    writer = PdfWriter()
    for text in texts:
        page = writer.add_blank_page(width=300, height=400)
        if text:
            font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
            page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
            stream = DecodedStreamObject()
            stream.set_data(f"BT /F1 12 Tf 10 100 Td ({text}) Tj ET".encode())
            page[NameObject("/Contents")] = writer._add_object(stream)
    if encrypted:
        writer.encrypt("private")
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def test_metadata_dates_categories_and_explicit_associations():
    result = parse_item(source_row())
    assert result.notice_date == item().notice_date
    assert result.disclosed_at.hour == 12 and result.disclosed_at.day == 27
    assert result.symbols == ["600519"] and result.categories[0].name == "定期报告"
    assert result.text_status == "pending" and result.text is None
    with pytest.raises(ValueError):
        parse_item(source_row(art_code="../evil"))


@pytest.mark.parametrize("url", ["http://pdf.dfcfw.com/pdf/a.pdf", "https://evil.com/pdf/a.pdf", "https://pdf.dfcfw.com.evil.com/pdf/a.pdf", "https://user:pw@pdf.dfcfw.com/pdf/a.pdf", "https://pdf.dfcfw.com:444/pdf/a.pdf", "https://pdf.dfcfw.com/private/a.pdf"])
def test_attachment_host_and_path_validation(url):
    with pytest.raises(ValueError):
        attachment_url(url)


def test_pdf_digital_scanned_partial_encrypted_and_limits(monkeypatch):
    complete = extract_pdf(pdf("First page", "Second page"))
    assert complete["text_status"] == "ready" and complete["pages_extracted"] == complete["total_pages"] == 2
    assert "Second page" in complete["text"] and complete["text_hash"]
    assert extract_pdf(pdf(None))["text_status"] == "unavailable"
    assert extract_pdf(pdf("Digital", None))["text_status"] == "partial"
    with pytest.raises(ValueError):
        extract_pdf(pdf("Secret", encrypted=True))
    with pytest.raises(ValueError):
        extract_pdf(b"not a PDF")
    monkeypatch.setattr("app.providers.announcements.MAX_PAGES", 1)
    assert extract_pdf(pdf("First", "Second"))["text_status"] == "partial"


def test_list_bounds_and_successful_empty():
    async def run():
        pages = []
        def transport(request):
            page = int(request.url.params["page_index"])
            pages.append(page)
            return httpx.Response(200, json={"success": 1, "data": {"list": [source_row(page * 20 + i) for i in range(20)], "total_hits": 1000}})
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            batch = await EastMoneyAnnouncements(client).collect("600519")
            assert pages == [1, 2, 3, 4, 5] and len(batch.items) == 100 and batch.truncated
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"success": 1, "data": {"list": []}}))) as client:
            batch = await EastMoneyAnnouncements(client).collect("600519")
            assert batch.items == [] and batch.complete and not batch.truncated
    asyncio.run(run())


def test_pdf_character_and_download_bounds(monkeypatch):
    monkeypatch.setattr("app.providers.announcements.MAX_TEXT", 5)
    value = extract_pdf(pdf("First page", "Second page"))
    assert len(value["text"]) <= 5 and value["text_status"] == "partial"
    async def run():
        monkeypatch.setattr("app.providers.announcements.MAX_PDF_BYTES", 10)
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=pdf("Text")))) as client:
            with pytest.raises(ValueError):
                await EastMoneyAnnouncements(client)._pdf("https://pdf.dfcfw.com/pdf/a.pdf")
    asyncio.run(run())


def test_list_pagination_dedup_old_boundary_and_partial_failure():
    async def run():
        calls = []
        def transport(request):
            page = int(request.url.params["page_index"])
            calls.append(page)
            if page == 2:
                return httpx.Response(503)
            return httpx.Response(200, json={"success": 1, "data": {"list": [source_row()] * 20, "total_hits": 100}})
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            batch = await EastMoneyAnnouncements(client).collect("600519")
        assert calls == [1, 2] and len(batch.items) == 1 and not batch.complete and batch.truncated
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"success": 1, "data": {"list": [source_row(notice_date="2020-01-01 00:00:00")]}}))) as client:
            batch = await EastMoneyAnnouncements(client).collect("600519")
            assert batch.items == [] and batch.complete
    asyncio.run(run())


@pytest.mark.parametrize("payload", [{"success": 0, "data": {}}, {"success": 1, "data": {}}, {"success": 1, "data": {"list": None}}])
def test_source_failure_is_not_successful_empty(payload):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))) as client:
            with pytest.raises(DataSourceError):
                await EastMoneyAnnouncements(client).collect("600519")
    asyncio.run(run())


def test_pdf_body_reuse_link_correction_and_excerpt_fallback():
    async def run():
        version, requests = [1], []
        def transport(request):
            requests.append(str(request.url))
            if request.url.host == "pdf.dfcfw.com":
                return httpx.Response(200, content=pdf(f"version {version[0]}"))
            url = f"https://pdf.dfcfw.com/pdf/doc{version[0]}.pdf"
            return httpx.Response(200, json={"success": 1, "data": {"art_code": item().id, "notice_content": "web excerpt", "attach_url": url}})
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            source = EastMoneyAnnouncements(client)
            first = await source.content(item())
            same = await source.content(item(), first)
            assert first.text_status == "ready" and same.text_hash == first.text_hash and same.text_fetched_at == first.text_fetched_at
            assert len(requests) == 3  # Detail checked, PDF not downloaded twice.
            version[0] = 2
            updated = await source.content(item(), first)
            assert updated.text_hash != first.text_hash and "version 2" in updated.text
        def broken_pdf(request):
            if request.url.host == "pdf.dfcfw.com":
                return httpx.Response(302, headers={"location": "http://127.0.0.1/private"})
            return httpx.Response(200, json={"success": 1, "data": {"art_code": item().id, "notice_content": "partial source text", "attach_url": "https://pdf.dfcfw.com/pdf/doc.pdf"}})
        async with httpx.AsyncClient(transport=httpx.MockTransport(broken_pdf)) as client:
            fallback = await EastMoneyAnnouncements(client).content(item())
            assert fallback.text_status == "partial" and fallback.text_source == "source_excerpt"
            assert fallback.text == "partial source text" and fallback.attachment_urls
    asyncio.run(run())


class AnnouncementProvider(FakeProvider):
    def __init__(self):
        super().__init__()
        self.calls, self.content_calls = [], []
        self.batch = AnnouncementBatch(items=[item(i) for i in range(1, 24)])
        self.error, self.text_error = False, False
        self.version = 1

    async def get_stock_announcements(self, symbol):
        self.calls.append(symbol)
        await asyncio.sleep(0)
        if self.error or symbol == "000001":
            raise DataSourceError("private upstream failure")
        return self.batch

    async def get_announcement_content(self, current, previous=None):
        self.content_calls.append(current.id)
        await asyncio.sleep(0)
        if self.text_error:
            raise DataSourceError("private text failure")
        return current.model_copy(update={"text": f"公告正文版本{self.version}", "text_status": "ready", "text_length": 8,
            "text_hash": str(self.version), "text_fetched_at": datetime.now(timezone.utc), "text_stale": False})


async def drain(service):
    while service._documents:
        await asyncio.gather(*list(service._documents.values()))
        await asyncio.sleep(0)


def setup(tmp_path, provider, **kwargs):
    engine = create_database_engine(f"sqlite:///{(tmp_path/'ann.db').as_posix()}")
    init_db(engine)
    sessions = create_session_factory(engine)
    watchlist = WatchlistService(sessions)
    return engine, sessions, watchlist, AnnouncementService(provider, sessions, watchlist, **kwargs)


def test_service_filters_associations_corrections_and_persistent_bodies(tmp_path):
    async def run():
        provider = AnnouncementProvider()
        engine, sessions, watchlist, service = setup(tmp_path, provider)
        await asyncio.gather(service.fetch("600519"), service.fetch("300750"))
        await drain(service)
        with sessions() as db:
            assert db.scalar(select(func.count()).select_from(AnnouncementRecord)) == 23
            assert db.scalar(select(func.count()).select_from(AnnouncementScopeItem)) == 46
        page = await service.get("600519", page_size=10, category="001")
        assert page.data.total == 23 and page.data.has_more and page.data.items[0].text is None
        assert (await service.get("600519", keyword="公告 23")).data.total == 1
        assert (await service.get("600519", keyword="%")).data.total == 0
        assert (await service.get("600519", category="00")).data.total == 0
        assert (await service.get("600519", start=item().notice_date + timedelta(days=1))).data.total == 0
        old = await service.document("600519", item(1).id)
        assert old.text == "公告正文版本1"
        provider.version = 2
        provider.batch = AnnouncementBatch(items=[item(1, title="更正公告", symbols=["600519"])], complete=False, truncated=True)
        await service.fetch("600519", force=True); await drain(service)
        assert (await service.get("600519", keyword="更正")).state == "partial"
        assert (await service.document("600519", item(1).id)).text_hash == "2"
        assert await service.document("300750", item(1).id) is None
        saved = (await service.get("600519")).cached_at
        await service.aclose()
        provider.error = True
        restarted = AnnouncementService(provider, sessions, watchlist)
        with pytest.raises(DataSourceError):
            await restarted.fetch("600519", force=True)
        result = await restarted.get("600519")
        assert result.stale and result.cached_at == saved and result.data.total == 23
        persisted = await restarted.document("600519", item(1).id)
        assert persisted.text_hash == "2" and persisted.text_stale
        before = len(provider.calls)
        await restarted.get("600519")
        assert len(provider.calls) == before
        await restarted.aclose(); engine.dispose()
    asyncio.run(run())


def test_empty_success_text_failure_and_retention(tmp_path):
    async def run():
        provider = AnnouncementProvider()
        provider.batch = AnnouncementBatch(items=[])
        engine, _, _, service = setup(tmp_path, provider)
        empty = await service.get("600519")
        assert empty.state == "ready" and empty.data.total == 0 and empty.cached_at
        provider.batch = AnnouncementBatch(items=[item(), item(2, notice_date=item().notice_date - timedelta(days=91))])
        provider.text_error = True
        await service.fetch("600519", force=True); await drain(service)
        unavailable = await service.document("600519", item().id)
        assert unavailable.text_status == "unavailable" and unavailable.url and unavailable.text is None
        assert (await service.get("600519")).data.total == 1
        provider.text_error = False
        await service.fetch("600519", force=True); await drain(service)
        prior = await service.document("600519", item().id)
        provider.text_error = True
        await service.fetch("600519", force=True); await drain(service)
        old = await service.document("600519", item().id)
        assert old.text_stale and old.text == prior.text and old.text_fetched_at == prior.text_fetched_at
        await service.aclose(); engine.dispose()
    asyncio.run(run())


def test_watchlist_collects_text_without_opening_detail_and_600_second_interval(tmp_path):
    async def run():
        provider, timer = AnnouncementProvider(), Clock()
        engine, _, watchlist, service = setup(tmp_path, provider, background=True, timer=timer)
        watchlist.add("600519"); watchlist.add("000001")
        collector = AnnouncementCollector(service, timer=timer)
        assert (await service.get("600519")).state == "warming" and provider.calls == []
        await collector.poll(); await asyncio.gather(*collector._pending.values()); await drain(service)
        assert (await service.document("600519", item().id)).text_status == "ready"
        assert (await service.get("000001")).state == "unavailable"
        before = len(provider.content_calls)
        await service.get("600519"); await service.document("600519", item().id)
        assert len(provider.content_calls) == before and len(provider.calls) == 2
        timer.now = 599
        await collector.poll(); assert len(provider.calls) == 2
        watchlist.remove("000001"); watchlist.add("300750")
        await collector.poll(); await asyncio.gather(*collector._pending.values()); await drain(service)
        timer.now = 601
        await collector.poll(); await asyncio.gather(*collector._pending.values()); await drain(service)
        assert provider.calls.count("600519") == 2 and provider.calls.count("000001") == 1
        await collector.stop(); await service.aclose()
        assert not service._documents and not service._pending
        engine.dispose()
    asyncio.run(run())


def test_api_tool_parity_and_registered_agent_trace(tmp_path):
    provider = AnnouncementProvider()
    def model(messages, _):
        if any(isinstance(part, ToolReturnPart) for part in messages[-1].parts):
            return ModelResponse(parts=[TextPart("测试公告回答")])
        return ModelResponse(parts=[ToolCallPart("get_stock_announcements", {"symbol": "600519", "days": 30, "limit": 10})])
    with TestClient(create_app(provider=provider, database_url=f"sqlite:///{(tmp_path/'api.db').as_posix()}", agent_model=FunctionModel(model))) as client:
        page = client.get("/api/stocks/600519/announcements").json()
        assert page["data"]["total"] == 23
        service = client.app.state.announcement_service
        client.portal.call(drain, service)
        current = client.get("/api/stocks/600519/announcements").json()
        deps = SimpleNamespace(announcements=service)
        tool = client.portal.call(get_stock_announcements, deps, "600519", 30, 10)
        assert tool["items"] == current["data"]["items"]
        assert datetime.fromisoformat(tool["cached_at"]) == datetime.fromisoformat(current["cached_at"])
        body = client.get(f"/api/stocks/600519/announcements/{item().id}")
        assert body.status_code == 200 and body.json()["data"]["text_status"] == "ready"
        detail_tool = client.portal.call(get_stock_announcements, deps, "600519", 30, 10, item().id)
        assert detail_tool["document"]["text"] == body.json()["data"]["text"]
        for path in ("/api/stocks/bad/announcements", "/api/stocks/600519/announcements?page=0", "/api/stocks/600519/announcements?start=2026-09-29&end=2026-09-28", "/api/stocks/600519/announcements/bad"):
            assert client.get(path).status_code == 422
        assert client.get(f"/api/stocks/600519/announcements/{item(999).id}").status_code == 404
        assert client.get("/api/stocks/000001/announcements").status_code == 503
        response = client.post("/api/agent/chat", json={"message": "600519最近公告"})
        assert response.status_code == 200, response.text
        assert "get_stock_announcements" in str(client.get("/api/agent/traces/recent").json())

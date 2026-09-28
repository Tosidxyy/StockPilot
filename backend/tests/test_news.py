import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic_ai import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.messages import ToolReturnPart
from pydantic_ai.models.function import FunctionModel
from sqlalchemy import func, select

from app.agent.tools import get_stock_news, get_market_news
from app.database.models import NewsRecord, NewsScopeItem
from app.database.session import create_database_engine, create_session_factory, init_db
from app.main import create_app
from app.models.news import NewsBatch, NewsItem
from app.providers.news import EastMoneyNews, parse_item
from app.providers.exceptions import DataSourceError, ProviderTimeoutError
from app.services.news import NewsService
from app.services.news_collector import NewsCollector
from app.services.watchlist import WatchlistService
from test_api import FakeProvider
from test_services import Clock


def row(code="202609283885257088", **extra):
    return {"code": code, "date": "2026-09-28 16:48:00", "showTime": "2026-09-28 16:48:00",
            "title": "<em>测试</em>&amp;新闻", "content": "来源片段 <b>非全文</b>", "summary": "栏目摘要",
            "mediaName": "测试媒体", "url": f"http://finance.eastmoney.com/a/{code}.html", **extra}


def item(index=1, title=None):
    return NewsItem(id=f"eastmoney:{index}", title=title or f"新闻 {index}", source="测试源",
                    url=f"https://finance.eastmoney.com/a/{index}.html",
                    published_at=datetime.now(timezone.utc) - timedelta(minutes=index), excerpt="来源片段")


class NewsProvider(FakeProvider):
    def __init__(self):
        super().__init__()
        self.news_calls = []
        self.batch = NewsBatch(items=[item(i) for i in range(1, 24)])
        self.news_error = False

    async def get_stock_news(self, symbol):
        self.news_calls.append(symbol)
        await asyncio.sleep(0)
        if self.news_error or symbol == "000001":
            raise DataSourceError("private upstream failure")
        return self.batch.model_copy(update={"items": [entry.model_copy(update={"excerpt": f"来源片段 {symbol}"}) for entry in self.batch.items]})

    async def get_market_news(self):
        return await self.get_stock_news("market")


def test_source_mapping_and_link_validation():
    news = parse_item(row(), "600519")
    assert news.title == "测试&新闻" and news.excerpt == "来源片段 非全文"
    assert news.published_at.hour == 8 and news.published_at.utcoffset() == timedelta(0)
    assert news.url.startswith("https://") and not news.content_available
    assert news.symbols == ["600519"] and news.association == "keyword_search"
    market = parse_item(row(), None)
    assert market.excerpt == "栏目摘要" and market.symbols == []
    for invalid in ("javascript:alert(1)", "https://evil.com/article", "https://eastmoney.com.evil.com/a", "https://user:pw@finance.eastmoney.com/a"):
        with pytest.raises(ValueError):
            parse_item(row(url=invalid), None)
    with pytest.raises(ValueError):
        parse_item(row(date="invalid"), "600519")


def test_search_pagination_jsonp_and_partial_source_failure():
    calls = []
    def transport(request):
        import json
        param = json.loads(request.url.params["param"])["param"]["cmsArticleWebOld"]
        calls.append(param)
        if param["pageIndex"] == 2:
            return httpx.Response(503)
        return httpx.Response(200, text='stockpilot(' + json.dumps({"code": 0, "bizCode": "", "hitsTotal": 25,
                           "result": {"cmsArticleWebOld": [row(), row()]}}) + ');')
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            batch = await EastMoneyNews(client).collect("600519")
        assert len(batch.items) == 1 and not batch.complete and batch.truncated
        assert calls[0]["sort"] == "time" and calls[1]["pageIndex"] == 2
    asyncio.run(run())


@pytest.mark.parametrize("payload", [{"code": 0, "data": None}, {"code": "1", "data": {"list": "wrong"}}, {"code": "1"}])
def test_invalid_market_payload_is_failure_not_empty(payload):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))) as client:
            with pytest.raises(DataSourceError):
                await EastMoneyNews(client).collect(None)
    asyncio.run(run())


def test_successful_empty_market_and_timeout():
    async def run():
        def transport(request):
            assert request.url.params["column"] == "353" and request.url.params["req_trace"]
            return httpx.Response(200, json={"code": "1", "data": {"list": []}})
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            batch = await EastMoneyNews(client).collect(None)
            assert batch.items == [] and batch.complete and not batch.truncated
        def timeout(_):
            raise httpx.ReadTimeout("private timeout")
        async with httpx.AsyncClient(transport=httpx.MockTransport(timeout)) as client:
            with pytest.raises(ProviderTimeoutError):
                await EastMoneyNews(client).collect("600519")
    asyncio.run(run())


@pytest.mark.parametrize("symbol", [None, "600519"])
def test_source_collection_is_bounded_to_five_pages(symbol):
    calls = []
    def transport(request):
        import json
        page = int(request.url.params["page_index"]) if symbol is None else json.loads(request.url.params["param"])["param"]["cmsArticleWebOld"]["pageIndex"]
        calls.append(page)
        rows = [row(str(202609280000000000 + page * 100 + i)) for i in range(20)]
        if symbol is None:
            return httpx.Response(200, json={"code": "1", "data": {"list": rows}})
        return httpx.Response(200, json={"code": 0, "bizCode": "", "hitsTotal": 300, "result": {"cmsArticleWebOld": rows}})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            batch = await EastMoneyNews(client).collect(symbol)
        assert len(batch.items) == 100 and batch.truncated and batch.complete
        assert calls == [1, 2, 3, 4, 5]
    asyncio.run(run())


def test_retention_excludes_old_and_implausibly_future_news(tmp_path):
    async def run():
        engine = create_database_engine(f"sqlite:///{(tmp_path / 'retention.db').as_posix()}")
        init_db(engine)
        sessions = create_session_factory(engine)
        provider = NewsProvider()
        old = item(24).model_copy(update={"published_at": datetime.now(timezone.utc)-timedelta(days=31)})
        future = item(25).model_copy(update={"published_at": datetime.now(timezone.utc)+timedelta(days=2)})
        provider.batch = NewsBatch(items=[item(1), old, future])
        news = NewsService(provider, sessions, WatchlistService(sessions))
        assert (await news.get("600519")).data.total == 1
        with sessions() as session:
            assert session.scalar(select(func.count()).select_from(NewsRecord)) == 1
        await news.aclose(); engine.dispose()
    asyncio.run(run())


def test_news_dedup_association_filter_pagination_and_update(tmp_path):
    async def run():
        engine = create_database_engine(f"sqlite:///{(tmp_path / 'news.db').as_posix()}")
        init_db(engine)
        sessions = create_session_factory(engine)
        provider = NewsProvider()
        news = NewsService(provider, sessions, WatchlistService(sessions))
        await asyncio.gather(news.fetch("600519"), news.fetch("600519"))
        assert provider.news_calls == ["600519"]
        await news.fetch("300750")
        await news.fetch("market")
        with sessions() as session:
            assert session.scalar(select(func.count()).select_from(NewsRecord)) == 23
            assert session.scalar(select(func.count()).select_from(NewsScopeItem)) == 69
        result = await news.get("600519", page=2, page_size=10)
        assert result.data.total == 23 and result.data.has_more and len(result.data.items) == 10
        assert result.data.items[0].symbols == ["300750", "600519"]
        assert result.data.items[0].excerpt == "来源片段 600519"
        assert (await news.get("300750")).data.items[0].excerpt == "来源片段 300750"
        assert (await news.get("600519", keyword="300750")).data.total == 0
        assert (await news.get("600519", keyword="600519")).data.total == 23
        assert (await news.get("600519", keyword="新闻 23")).data.total == 1
        assert (await news.get("600519", keyword="%")).data.total == 0
        assert (await news.get("600519", start=datetime.now(timezone.utc).date()+timedelta(days=2))).data.total == 0
        provider.batch = NewsBatch(items=[item(1, "更正后的新闻")], complete=False, truncated=True)
        await news.fetch("600519", force=True)
        updated = await news.get("600519", page_size=50)
        assert updated.state == "partial" and updated.data.partial and updated.data.source_truncated
        assert updated.data.items[0].title == "更正后的新闻" and updated.data.total == 23
        with pytest.raises(ValueError):
            await news.get(start=datetime(2026, 9, 29).date(), end=datetime(2026, 9, 28).date())
        await news.aclose()
        engine.dispose()
    asyncio.run(run())


def test_restart_and_failures_keep_original_time_and_empty_success(tmp_path):
    async def run():
        engine = create_database_engine(f"sqlite:///{(tmp_path / 'restart.db').as_posix()}")
        init_db(engine)
        sessions = create_session_factory(engine)
        watchlist = WatchlistService(sessions)
        provider, timer = NewsProvider(), Clock()
        news = NewsService(provider, sessions, watchlist, timer=timer)
        saved = await news.get("600519")
        await news.aclose()
        provider.news_error = True
        news = NewsService(provider, sessions, watchlist, timer=timer)
        with pytest.raises(DataSourceError):
            await news.fetch("600519", force=True)
        old = await news.get("600519")
        assert old.stale and old.cached_at == saved.cached_at and old.data.items == saved.data.items
        before = len(provider.news_calls)
        await news.get("600519")
        assert len(provider.news_calls) == before  # Retry backoff retained.
        with pytest.raises(DataSourceError):
            await news.get("300750")
        provider.news_error = False
        provider.batch = NewsBatch(items=[])
        empty = await news.get()
        assert empty.state == "ready" and not empty.data.items and empty.cached_at
        await news.aclose()
        provider.news_error = True
        news = NewsService(provider, sessions, watchlist, background=True)
        watchlist.add("600519")
        assert (await news.get("600519")).cached_at == saved.cached_at
        assert len(provider.news_calls) == before + 2
        await news.aclose()
        engine.dispose()
    asyncio.run(run())


def test_background_news_without_visiting_detail_and_membership_changes(tmp_path):
    async def run():
        engine = create_database_engine(f"sqlite:///{(tmp_path / 'collector.db').as_posix()}")
        init_db(engine)
        sessions = create_session_factory(engine)
        watchlist = WatchlistService(sessions)
        watchlist.add("600519"); watchlist.add("000001")
        provider, timer = NewsProvider(), Clock()
        news = NewsService(provider, sessions, watchlist, background=True, timer=timer)
        collector = NewsCollector(news, timer=timer)
        cold = await news.get("600519")
        assert cold.state == "warming" and provider.news_calls == []
        await collector.poll(); await asyncio.gather(*collector._pending.values())
        assert (await news.get("600519")).state == "ready"
        assert (await news.get("000001")).state == "unavailable"
        calls = list(provider.news_calls)
        await news.get("600519", page=2)
        await collector.poll()
        assert provider.news_calls == calls
        watchlist.remove("000001"); watchlist.add("300750")
        await collector.poll(); await asyncio.gather(*collector._pending.values())
        assert provider.news_calls[-1] == "300750"
        timer.now = 301
        await collector.poll(); await asyncio.gather(*collector._pending.values())
        assert provider.news_calls.count("000001") == 1
        collector.request_refresh({"600519"})
        await collector.poll(); await asyncio.gather(*collector._pending.values())
        assert provider.news_calls.count("600519") == 3
        await collector.stop(); await news.aclose(); engine.dispose()
    asyncio.run(run())


def test_api_and_tool_share_news_and_agent_trace(tmp_path):
    provider = NewsProvider()
    def model(messages, _):
        if any(isinstance(part, ToolReturnPart) for part in messages[-1].parts):
            return ModelResponse(parts=[TextPart("测试新闻回答（非全文）")])
        return ModelResponse(parts=[ToolCallPart("get_stock_news", {"symbol": "600519", "days": 7, "limit": 5})])
    with TestClient(create_app(provider=provider, database_url=f"sqlite:///{(tmp_path/'api.db').as_posix()}",
                              agent_model=FunctionModel(model))) as client:
        assert client.get("/health").status_code == 200
        page = client.get("/api/stocks/600519/news").json()
        assert page["data"]["total"] == 23 and page["collection_state"] == "ready"
        assert client.get("/api/stocks/600519/news?page=2").json()["data"]["items"][0]["id"] != page["data"]["items"][0]["id"]
        deps = SimpleNamespace(news=client.app.state.news_service)
        tool = client.portal.call(get_stock_news, deps, "600519", 7, 10)
        assert tool["items"] == page["data"]["items"]
        assert datetime.fromisoformat(tool["cached_at"]) == datetime.fromisoformat(page["cached_at"])
        assert len(provider.news_calls) == 1
        assert client.get("/api/market/news").status_code == 200
        assert client.portal.call(get_market_news, deps)["items"]
        for path in ("/api/stocks/bad/news", "/api/market/news?page=0", "/api/market/news?start=2026-09-29&end=2026-09-28"):
            assert client.get(path).status_code == 422
        assert client.get("/api/stocks/000001/news").status_code == 503
        assert "private" not in client.get("/api/stocks/000001/news").text
        response = client.post("/api/agent/chat", json={"message": "600519最近新闻"})
        assert response.status_code == 200, response.text
        traces = client.get("/api/agent/traces/recent").json()
        assert "get_stock_news" in str(traces) and "非全文" in str(traces)

import asyncio
import json
from datetime import timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from app.agent.citations import finalize_documents
from app.agent.execution import invoke_tool
from app.agent.tools import AgentDependencies, get_stock_sentiment
from app.database.models import utc_now
from app.database.session import create_database_engine, create_session_factory, init_db
from app.main import create_app
from app.models.sentiment import CommentBatch, ForumComment
from app.providers.comments import BEIJING, EastMoneyComments, parse_comments
from app.providers.exceptions import DataSourceError
from app.services.market import MarketService
from app.services.sentiment import SentimentCollector, SentimentService, classify, summarize
from app.services.stock import StockService
from app.services.watchlist import WatchlistService
from test_api import FakeProvider
from test_services import Clock


def post(identifier=1, *, code="300750", text="看好反弹", age=0, kind=0):
    return {"post_id": identifier, "stockbar_code": code, "post_type": kind, "post_title": text,
        "post_publish_time": (utc_now().astimezone(BEIJING)-timedelta(hours=age)).strftime("%Y-%m-%d %H:%M:%S")}


def page(rows, extra=None, code="300750"):
    return "<script>var article_list="+json.dumps({"rc":1,"bar_code":code,"re":rows})+";" + (
        "var other_list="+json.dumps({"rc":1,"re":extra})+";" if extra is not None else "")+"</script>"


def test_parser_excludes_news_other_stocks_and_old_posts_and_anonymizes():
    reply = {"reply_id":12,"reply_text":"<b>看空</b><script>steal()</script>",
        "reply_time":post()["post_publish_time"],"reply_user":{"user_id":"private"},"reply_ar":"IP region"}
    body = page([post(),post(2,code="600519"),post(3,age=25),post(4,kind=1),post(5,kind=20)],
        [{"post_id":4,"post_guba":{"stockbar_code":"300750"},"post_type":1,"reply_list":[reply]}])
    batch = parse_comments(body,"300750")
    assert {item.id for item in batch.items} == {"post:1","reply:12"}
    assert next(item for item in batch.items if item.kind == "reply").text == "看空"
    assert "private" not in batch.model_dump_json() and "IP region" not in batch.model_dump_json()
    assert all(item.published_at.utcoffset() == timedelta(hours=8) for item in batch.items)


def test_parser_malformed_row_partial_and_empty_success():
    batch = parse_comments(page([post(),dict(post(2),post_publish_time="broken"),"bad"]),"300750")
    assert batch.partial and len(batch.items)==1
    assert parse_comments(page([]),"300750").items == []


@pytest.mark.parametrize("body",["<html>verification</html>",page([],code="600519"),
    '<script>var article_list={"rc":0,"re":[]};</script>',
    '<script>var article_list=execute();</script>',"x"*2_000_001],ids=["challenge","wrong-stock","failure","javascript","oversize"])
def test_source_errors_not_empty_sentiment(body):
    with pytest.raises(DataSourceError):parse_comments(body,"300750")


@pytest.mark.parametrize("text,expected",[("看好，利好", "bullish"),("不看好", "bearish"),("看空暴跌", "bearish"),
    ("利好但要出货", "mixed"),("会涨停吗？", "unknown"),("不会涨停", "unknown"),("别看空", "unknown"),
    ("今天回购了吗", "unknown"),("不会大跌", "unknown"),("真是垃圾", "bearish"),
    ("不是垃圾", "unknown"),("不堪一击", "bearish")])
def test_honest_rule_labels(text,expected): assert classify(text)==expected


def sample(text="看好", identifier="post:1", symbol="300750", age=0):
    return ForumComment(id=identifier,symbol=symbol,text=text,published_at=utc_now()-timedelta(hours=age),
        url=f"https://guba.eastmoney.com/news,{symbol},1.html",kind="post_title")


class CommentsProvider(FakeProvider):
    def __init__(self):
        super().__init__()
        self.calls=[]
        self.items=[sample()]

    async def get_stock_comments(self,symbol):
        self.calls.append(symbol)
        self._check()
        return CommentBatch(items=self.items)


def setup(tmp_path):
    engine=create_database_engine(f"sqlite:///{(tmp_path/'sentiment.db').as_posix()}")
    init_db(engine)
    sessions=create_session_factory(engine)
    watchlist=WatchlistService(sessions)
    return engine,sessions,watchlist


def test_duplicate_text_not_amplified():
    report=summarize("300750",[sample("看好!"),sample("看好！",identifier="post:2"),sample("看空",identifier="post:3")])
    assert report.sample_count==2 and report.duplicate_count==1
    assert report.counts=={"bullish":1,"bearish":1,"mixed":0,"unknown":0}


def test_cache_merge_outage_restart_and_tool_reads_without_http(tmp_path):
    async def run():
        engine,sessions,watchlist=setup(tmp_path)
        timer=Clock(); provider=CommentsProvider()
        service=SentimentService(provider,sessions,watchlist,timer=timer)
        first=await service.get("300750")
        timer.now=61
        provider.items=[sample("看空",identifier="post:2"),sample("cross",symbol="600519",identifier="post:3")]
        second=await service.get("300750")
        assert second.data.sample_count==2 and second.data.counts["bearish"]==1
        timer.now=122
        provider.error=DataSourceError("offline")
        failed=await service.get("300750")
        assert failed.stale and failed.cached_at==second.cached_at and failed.data==second.data
        restarted=SentimentService(provider,sessions,watchlist,timer=timer)
        fallback=await restarted.get("300750")
        assert fallback.stale and fallback.data==second.data
        deps=AgentDependencies(stocks=StockService(provider),market=MarketService(provider),watchlist=watchlist,sentiment=restarted)
        calls=len(provider.calls)
        result=await invoke_tool(deps,"get_stock_sentiment",{"symbol":"300750"},lambda:get_stock_sentiment(deps,"300750"))
        assert len(provider.calls)==calls and result["stale"]
        assert result["evidence"][0]["citation_ref"]=="[S1]"
        answer=finalize_documents("股吧样本中有偏空观点。[S1]",deps.document_searches,"股吧情绪")
        assert "用户发帖标题，非正文" in answer and "https://guba.eastmoney.com" in answer
        assert first.data.sample_count==1
        await service.aclose();await restarted.aclose();engine.dispose()
    asyncio.run(run())


def test_watchlist_preloads_without_detail_and_respects_minute_cadence(tmp_path):
    async def run():
        engine,sessions,watchlist=setup(tmp_path);watchlist.add("300750")
        provider=CommentsProvider();timer=Clock()
        service=SentimentService(provider,sessions,watchlist,timer=timer)
        collector=SentimentCollector(service,timer=timer)
        await collector.poll();await asyncio.gather(*collector.pending.values())
        assert provider.calls==["300750"]
        timer.now=59;await collector.poll();assert provider.calls==["300750"]
        timer.now=61;await collector.poll();await asyncio.gather(*collector.pending.values())
        assert provider.calls==["300750","300750"]
        watchlist.remove("300750");timer.now=122;await collector.poll();assert len(provider.calls)==2
        await collector.stop();await service.aclose();engine.dispose()
    asyncio.run(run())


def test_api_health_validates_codes_and_preserves_empty_sample(tmp_path):
    provider=CommentsProvider();provider.items=[]
    app=create_app(provider=provider,database_url=f"sqlite:///{(tmp_path/'api.db').as_posix()}",prefetch_enabled=False)
    with TestClient(app) as client:
        assert client.get("/health").status_code==200
        assert client.get("/api/stocks/invalid/sentiment").status_code==422
        result=client.get("/api/stocks/300750/sentiment")
        assert result.status_code==200 and result.json()["data"]["sample_count"]==0
        assert result.json()["collection_state"]=="ready"
        client.get("/api/stocks/300750/sentiment");assert len(provider.calls)==1
        provider.error=DataSourceError("offline")
        assert client.get("/api/stocks/600519/sentiment").status_code==503


def test_provider_public_html_request_and_failure_structure():
    async def run():
        requests=[]
        def respond(request):
            requests.append(request)
            return httpx.Response(200,text=page([post()]))
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            batch=await EastMoneyComments(client).collect("300750")
            assert len(batch.items)==1 and requests[0].method=="GET"
            assert str(requests[0].url)=="https://guba.eastmoney.com/list,300750.html"
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _:httpx.Response(403))) as client:
            with pytest.raises(DataSourceError):await EastMoneyComments(client).collect("300750")
    asyncio.run(run())

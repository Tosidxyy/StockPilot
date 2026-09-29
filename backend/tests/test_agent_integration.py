"""Bounded information tools and combined answers under source/budget failures."""
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic_ai import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.messages import ToolReturnPart
from pydantic_ai.models.function import FunctionModel, DeltaToolCall

from app.agent.tools import get_stock_news, get_stock_announcements, get_watchlist_insights
from app.agent.execution import data_notes
from app.agent.citations import finalize_documents, INVALID_CITATIONS
from app.database.models import NewsFetchState, AnnouncementFetchState, NewsRecord, NewsScopeItem
from app.database.session import create_database_engine, create_session_factory, init_db
from app.main import create_app
from app.services.news import NewsService
from app.services.announcements import AnnouncementService
from app.services.documents import DocumentService
from app.services.watchlist import WatchlistService
from app.models.news import NewsBatch
from app.models.announcements import AnnouncementBatch
from app.providers.exceptions import DataSourceError
from test_news import NewsProvider
from test_announcements import AnnouncementProvider
from test_api import FakeProvider
from test_documents import seed, NOW
from test_agent_stream import events


@pytest.fixture
def db(tmp_path):
    url=f"sqlite:///{(tmp_path/'integration.db').as_posix()}"
    engine=create_database_engine(url); init_db(engine)
    sessions=create_session_factory(engine)
    yield url,engine,sessions
    engine.dispose()


@pytest.mark.parametrize("kind", ["news", "announcement"])
@pytest.mark.parametrize("empty", [False, True])
def test_stale_info_cache_and_successful_empty_never_refetch(db,kind,empty):
    _,_,sessions=db
    provider=NewsProvider() if kind=="news" else AnnouncementProvider()
    if empty: provider.batch=NewsBatch(items=[]) if kind=="news" else AnnouncementBatch(items=[])
    watch=WatchlistService(sessions)
    stamp=datetime.now(timezone.utc)-timedelta(hours=2)
    service=(NewsService if kind=="news" else AnnouncementService)(provider,sessions,watch,clock=lambda:stamp)
    async def run():
        await service.fetch("600519")
        if kind=="announcement":
            await asyncio.gather(*list(service._documents.values()))
        calls=len(provider.news_calls if kind=="news" else provider.calls)
        service._clock=lambda:stamp+timedelta(hours=2)
        deps=SimpleNamespace(**{kind if kind=="news" else "announcements":service})
        out=await (get_stock_news(deps,"600519") if kind=="news" else get_stock_announcements(deps,"600519"))
        assert out['stale'] and out['cached_at']==stamp.isoformat()
        assert len(provider.news_calls if kind=="news" else provider.calls)==calls
        assert bool(out['items']) is not empty
    asyncio.run(run())


def test_recent_browse_market_scope_doc_filter_and_long_body(db):
    _,_,sessions=db; seed(sessions,body="回购正文"*5000)
    with sessions.begin() as s:
        s.add(NewsScopeItem(scope="market",news_id="n1"))
        s.add(NewsRecord(id="n2",published_at=NOW,payload={"title":"第二新闻","source":"媒体","url":"https://finance.eastmoney.com/a/2.html","excerpt":"第二片段"}))
        s.add(NewsScopeItem(scope="600519",news_id="n2"))
    service=DocumentService(sessions,clock=lambda:NOW)
    async def run():
        out=await service.recent("600519",limit=3)
        assert len(out['evidence'])==3 and len({e['document_id'] for e in out['evidence']})==3
        assert all(e['ordinal']==0 for e in out['evidence'])
        market=await service.recent("market",kind="news")
        assert market['evidence'][0]['association']=="market_column"
        body=await service.recent("600519",kind="announcement",document_id="AN1",limit=10)
        assert body['document_id']=="AN1" and all(e['document_id']=="AN1" for e in body['evidence'])
        assert not (await service.recent("300750",kind="announcement",document_id="AN1"))['evidence']
        with pytest.raises(ValueError): await service.recent("market",kind="all")
        with pytest.raises(ValueError): await service.recent("600519",limit=11)
    asyncio.run(run())


def test_watchlist_insights_limits_missing_categories_and_membership(db):
    _,_,sessions=db; seed(sessions)
    watch=WatchlistService(sessions)
    for code in ("600519","300750","002414"): watch.add(code)
    deps=SimpleNamespace(watchlist=watch,documents=DocumentService(sessions,clock=lambda:NOW),document_searches=[])
    async def run():
        out=await get_watchlist_insights(deps,stock_limit=2)
        assert out['watchlist_total']==3 and out['remaining_count']==1 and out['partial']
        assert len(out['stocks'])==2 and len(out['evidence'])<=4
        assert len(deps.document_searches)==4
        for row in out['stocks']:
            assert all(len(row[k]['evidence'])<=1 for k in ('news','announcement'))
        watch.remove("600519")
        out=await get_watchlist_insights(deps,stock_limit=10)
        assert out['remaining_count']==0 and all(row['symbol']!='600519' for row in out['stocks'])
        for row in watch.list_entries(): watch.remove(row.symbol)
        assert (await get_watchlist_insights(deps))['watchlist_total']==0
        with pytest.raises(ValueError): await get_watchlist_insights(deps,stock_limit=11)
    asyncio.run(run())


class MixedProvider(FakeProvider):
    async def get_quotes(self,symbols):
        raise DataSourceError("private key upstream failure")


def combined_model(*,stream=False,empty=False,fabricate=False):
    calls=[ToolCallPart("get_stock_quote",{"symbol":"600519"}),
        ToolCallPart("get_stock_kline",{"symbol":"600519","limit":5}),
        ToolCallPart("search_stock_documents",{"symbol":"002414" if empty else "600519","query":"回购"})]
    def make_answer(parts):
        returns=[p.content for p in parts if isinstance(p,ToolReturnPart) and p.tool_name=='search_stock_documents']
        if not returns: return None
        evidence=returns[0]['evidence']
        if not evidence: return "**K线**：收盘价来自实际K线；资讯无可引用证据。"
        return f"**K线**：收盘价来自实际K线；回购记录 [{'Efabricated' if fabricate else evidence[0]['evidence_id']}]。"
    def function(messages,_info):
        answer=make_answer(messages[-1].parts)
        return ModelResponse(parts=[TextPart(answer)]) if answer else ModelResponse(parts=calls)
    async def streaming(messages,_info):
        answer=make_answer(messages[-1].parts)
        if answer:
            yield answer[:8]; yield answer[8:]
        else:
            import json
            yield {i:DeltaToolCall(name=p.tool_name,json_args=json.dumps(p.args)) for i,p in enumerate(calls)}
    return FunctionModel(stream_function=streaming) if stream else FunctionModel(function)


@pytest.mark.parametrize("stream",[False,True])
@pytest.mark.parametrize("empty,fabricate",[(False,False),(True,False),(False,True)])
def test_combined_partial_failure_evidence_stream_and_persistence(db,stream,empty,fabricate):
    url,_,sessions=db; seed(sessions)
    with TestClient(create_app(provider=MixedProvider(),database_url=url,prefetch_enabled=False,
        agent_model=combined_model(stream=stream,empty=empty,fabricate=fabricate))) as client:
        r=client.post('/api/agent/chat'+('/stream' if stream else ''),json={'message':'600519 K线及公告资料联合分析'})
        assert r.status_code==200,r.text
        if stream:
            emitted=events(r); assert emitted[-1][0]=='done'
            result=emitted[-1][1]
            assert ''.join(d['text'] for name,d in emitted if name=='delta')==result['answer']
            assert 'Efabricated' not in r.text
        else: result=r.json()
        answer=result['answer']; sid=result['session_id']
        assert 'private key' not in answer and '本次不可用' in answer
        assert '2026-09-25' in answer and '北京时间' in answer
        if fabricate: assert '未通过证据引用校验' in answer and '回购记录' not in answer
        elif empty: assert '**K线**' in answer and '资讯检索未取得' in answer
        else: assert '证据来源' in answer and 'AN1' in answer
        trace=client.get(f'/api/agent/traces/{sid}').json()['data']
        assert len(trace)==3 and trace[0]['status']=='error' and trace[1]['status']=='success'
        assert trace[2]['tool_input']['query']=='回购' and all(t['latency_ms']>=0 for t in trace)
        if not empty: assert 'E' in trace[2]['tool_output_summary']
        assert client.get(f'/api/agent/sessions/{sid}').json()['messages'][-1]['content']==answer
        evidence=client.get(f'/api/agent/sessions/{sid}/evidence').json()['data']
        assert bool(evidence)==(not empty and not fabricate)


@pytest.mark.parametrize("stream",[False,True])
def test_budget_exhaustion_visible_no_partial_saved(db,stream):
    url,_,_=db
    def looping(_messages,_info): return ModelResponse(parts=[ToolCallPart('get_market_indices',{})])
    async def streaming(_messages,_info): yield {0:DeltaToolCall(name='get_market_indices',json_args='{}')}
    model=FunctionModel(stream_function=streaming) if stream else FunctionModel(looping)
    with TestClient(create_app(provider=FakeProvider(),database_url=url,prefetch_enabled=False,agent_model=model)) as client:
        r=client.post('/api/agent/chat'+('/stream' if stream else ''),json={'message':'市场指数'})
        if stream:
            emitted=events(r); assert [name for name,_ in emitted if name != 'progress']==['session','error']
            assert emitted[-1][1]['status']==429
            sid=emitted[0][1]['session_id']
        else:
            assert r.status_code==429 and '预算' in r.text
            sid=r.headers['X-Agent-Session-ID']
        assert client.get(f'/api/agent/sessions/{sid}').json()['messages']==[]
        trace=client.get(f'/api/agent/traces/{sid}').json()['data']
        assert 1<=len(trace)<=12 and all(t['status']=='success' for t in trace)


def test_time_notes_source_dates_missing_fields_and_breadth():
    notes=data_notes([{'tool':'get_stock_money_flow','input':{'symbol':'600519'},'result':{
        'source':'eastmoney','cached_at':'2026-09-29T01:00:00Z','stale':True,
        'items':[{'date':'2026-09-28','main_net_inflow':None}]}},
        {'tool':'get_market_breadth','input':{},'result':{'exchanges':[{'exchange':'SH','as_of':'2026-09-28T08:12:00Z'}],
            'limit_up':{'date':'2026-09-28'},'limit_down':{'date':None}}}])
    assert '2026-09-29 09:00:00' in notes and '旧缓存' in notes
    assert '2026-09-28 16:12:00' in notes and '缺失不是零' in notes and '不能证明涨跌因果' in notes
    assert 'evil' not in data_notes([{'tool':'get_stock_quote','input':{'symbol':'[evil](https://evil.example)'},
        'result':{'available':False}}])


@pytest.mark.parametrize('name',['get_stock_news','get_stock_announcements','get_watchlist_insights'])
def test_information_tools_attach_actual_citations_without_extra_search(db,name):
    url,_,_=db
    def function(messages,_info):
        returned=[p.content for p in messages[-1].parts if isinstance(p,ToolReturnPart)]
        if returned:
            evidence=returned[0]['evidence']
            assert evidence
            if 'items' in returned[0]:
                assert {e['document_id'] for e in evidence} <= {i['id'] for i in returned[0]['items']}
            return ModelResponse(parts=[TextPart('已有资料片段 ['+evidence[0]['evidence_id']+']。')])
        return ModelResponse(parts=[ToolCallPart(name,{} if name=='get_watchlist_insights' else {'symbol':'600519'})])
    provider=AnnouncementProvider() if name=='get_stock_announcements' else NewsProvider()
    with TestClient(create_app(provider=provider,database_url=url,prefetch_enabled=False,
        agent_model=FunctionModel(function))) as client:
        if name=='get_watchlist_insights':
            client.post('/api/watchlist',json={'symbol':'600519'})
            client.get('/api/stocks/600519/news')
        r=client.post('/api/agent/chat',json={'message':'资讯摘要'})
        assert r.status_code==200,r.text
        result=r.json(); sid=result['session_id']
        assert '证据来源' in result['answer']
        trace=client.get(f'/api/agent/traces/{sid}').json()['data']
        assert len(trace)==1 and trace[0]['tool_name']==name and '证据 E' in trace[0]['tool_output_summary']
        assert client.get(f'/api/agent/sessions/{sid}/evidence').json()['data']


def test_multi_tool_success_preserves_each_real_date(db):
    url,_,sessions=db; seed(sessions)
    with TestClient(create_app(provider=FakeProvider(),database_url=url,prefetch_enabled=False,
        agent_model=combined_model())) as client:
        r=client.post('/api/agent/chat',json={'message':'600519 联合行情分析'})
        assert r.status_code==200 and '证据来源' in r.json()['answer']
        trace=client.get('/api/agent/traces/'+r.json()['session_id']).json()['data']
        assert len(trace)==3 and all(t['status']=='success' for t in trace)


def test_tool_call_budget_blocks_excessive_parallel_requests(db):
    url,_,_=db
    def excessive(_messages,_info):
        return ModelResponse(parts=[ToolCallPart('get_market_indices',{},tool_call_id=str(i)) for i in range(13)])
    provider=FakeProvider()
    with TestClient(create_app(provider=provider,database_url=url,prefetch_enabled=False,
        agent_model=FunctionModel(excessive))) as client:
        r=client.post('/api/agent/chat',json={'message':'市场指数'})
        assert r.status_code==429
        sid=r.headers['X-Agent-Session-ID']
        assert client.get(f'/api/agent/sessions/{sid}').json()['messages']==[]
        assert client.get(f'/api/agent/traces/{sid}').json()['data']==[]


def test_complete_bare_evidence_ids_normalize_but_unknown_ids_still_rejected(db):
    _,_,sessions=db; seed(sessions)
    data=asyncio.run(DocumentService(sessions,clock=lambda:NOW).recent('600519',kind='news'))
    identifier=data['evidence'][0]['evidence_id']
    answer=finalize_documents('片段记录 '+identifier+'。',[data],'新闻摘要')
    assert '['+identifier+']' in answer and '证据来源' in answer
    assert '[[' not in finalize_documents('已引用 ['+identifier+']。',[data],'新闻摘要')
    assert finalize_documents('记录 '+identifier+'，未知 '+'E'+'0'*24,[data],'新闻摘要')==INVALID_CITATIONS
    for bad in ('[E13dc…不适用]', '['+identifier+' 不适用]', '[Eshort]'):
        assert finalize_documents('事实 ['+identifier+']；'+bad,[data],'新闻摘要') == INVALID_CITATIONS
    assert '['+identifier+']' in finalize_documents('片段记录 [S1]。',[data],'新闻摘要')
    assert finalize_documents('片段记录 [S1] [S999]。',[data],'新闻摘要') == INVALID_CITATIONS

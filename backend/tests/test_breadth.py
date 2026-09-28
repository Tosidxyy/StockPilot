import asyncio
from datetime import date
from types import SimpleNamespace
import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel
from app.agent.tools import get_market_breadth
from app.database.session import create_database_engine, create_session_factory, init_db
from app.main import create_app
from app.models.breadth import MarketBreadth
from app.providers.breadth import parse_breadth, parse_pool
from app.providers.eastmoney import EastMoneyProvider
from app.providers.exceptions import DataSourceError, ProviderTimeoutError
from app.services.market import MarketService
from test_api import FakeProvider
from test_services import Clock

DAY = date(2026, 9, 28)


def row(code, market, up, down, flat, stamp=1790583120):
    return {"f12": code, "f13": market, "f104": up, "f105": down, "f106": flat, "f124": stamp}


def payload(rows=None):
    return {"rc": 0, "data": {"diff": rows if rows is not None else [
        row("000002", 1, 425, 1842, 47), row("399002", 0, 437, 2403, 56), row("899050", 0, 36, 309, 2, 1790580959)]}}


def pool(total=33, rows=None, day=20260928):
    return {"rc": 0, "data": {"tc": total, "qdate": day, "pool": [{"c": "001330"}] if rows is None else rows}}


def complete():
    result = parse_breadth(payload())
    result.limit_up = parse_pool(pool(), DAY)
    result.limit_down = parse_pool(pool(56), DAY)
    return result


class BreadthProvider(FakeProvider):
    def __init__(self):
        super().__init__(); self.breadth_calls = 0; self.result = complete()
    async def get_market_breadth(self):
        self.breadth_calls += 1
        await asyncio.sleep(0)
        self._check()
        return self.result.model_copy(deep=True)


def test_actual_mapping_scope_time_aggregate_and_snapshot_serialization():
    result = complete()
    assert result.advancing == 898 and result.declining == 4554 and result.unchanged == 105
    assert result.date == DAY and result.counts_complete and not result.partial
    assert result.exchanges[0].as_of.isoformat() == "2026-09-28T16:12:00+08:00"
    assert result.exchanges[2].as_of.isoformat() == "2026-09-28T15:35:59+08:00"
    assert result.limit_up.count == 33 and result.limit_down.count == 56
    assert "股池" in result.limit_up.scope and "沪深京" in result.scope
    assert MarketBreadth.model_validate_json(result.model_dump_json()).advancing == 898
    assert parse_breadth(payload({str(i):r for i,r in enumerate(payload()["data"]["diff"])})).date == DAY


def test_missing_market_field_old_and_different_dates_do_not_make_full_total():
    missing = parse_breadth(payload([row("000002",1,425,1842,47)]))
    assert missing.partial and missing.advancing is None and missing.exchanges[1].advancing is None
    fields = parse_breadth(payload([row("000002",1,425,"-",47)]))
    assert fields.exchanges[0].advancing == 425 and fields.exchanges[0].declining is None
    cross_day = complete(); cross_day.exchanges[2].as_of = cross_day.exchanges[2].as_of.replace(day=25)
    assert cross_day.date is None and cross_day.declining is None and cross_day.partial
    old = complete(); old.exchanges[0].stale = True
    assert old.advancing is None
    zero = parse_pool(pool(0, []), DAY)
    assert zero.count == 0 and zero.date == DAY


@pytest.mark.parametrize("raw", [None, {"rc":1}, {"rc":0,"data":None}, payload([]), payload([{}]),
    payload([row("000001",1,5,6,7)]), payload([row("000002",1,0,0,0)]),
    payload([row("000002",1,-1,2,3)]), payload([row("000002",1,True,2,3)]),
    payload([row("000002",1,"NaN",2,3)]), payload([row("000002",1,1.5,2,3)]),
    payload([row("000002",1,5,6,7,0)]), payload([row("000002",1,5,6,7),row("000002",1,5,6,7)])])
def test_invalid_or_wrong_index_counts_rejected(raw):
    with pytest.raises(ValueError): parse_breadth(raw)


@pytest.mark.parametrize("raw", [None,{"rc":1},pool(day=20260929),pool(rows=[]),pool(0,[{"c":"001330"}]),
    pool(-1),pool("-"),pool(rows=[{"c":"bad"}]),pool(1.5)])
def test_pool_date_count_and_silent_empty_validation(raw):
    with pytest.raises((ValueError, ArithmeticError)): parse_pool(raw,DAY)


def test_http_fallback_pool_failure_is_independent_and_timeout_mapping():
    async def run():
        requests=[]
        def respond(request):
            requests.append(request)
            if request.url.host == "push2.eastmoney.com": return httpx.Response(200,json={"rc":0,"data":None})
            if request.url.host == "push2delay.eastmoney.com": return httpx.Response(200,json=payload())
            assert request.url.params["date"] == "20260928" and request.url.params["pagesize"] == "1"
            if request.url.path.endswith("getTopicDTPool"):
                assert request.url.params["sort"] == "fund:asc"
                return httpx.Response(200,json=pool(56,[]))
            return httpx.Response(200,json=pool())
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            result=await EastMoneyProvider(client).get_market_breadth()
            assert len(requests)==4 and result.advancing==898 and result.partial
            assert result.limit_up.count==33 and result.limit_down.count is None
            assert requests[0].url.params["secids"] == "1.000002,0.399002,0.899050"
        def timeout(request): raise httpx.ReadTimeout('private',request=request)
        async with httpx.AsyncClient(transport=httpx.MockTransport(timeout)) as client:
            with pytest.raises(ProviderTimeoutError): await EastMoneyProvider(client).get_market_breadth()
    asyncio.run(run())


def test_cache_tool_partial_merge_original_source_times_restart_outage_and_recovery(tmp_path):
    async def run():
        engine=create_database_engine(f"sqlite:///{(tmp_path/'breadth.db').as_posix()}"); init_db(engine)
        sessions=create_session_factory(engine); provider=BreadthProvider(); timer=Clock()
        market=MarketService(provider,timer=timer,snapshot_sessions=sessions)
        try:
            a,b=await asyncio.gather(market.get_breadth(),market.get_breadth())
            assert provider.breadth_calls==1 and a.cached_at==b.cached_at
            timer.now=31
            tool=await get_market_breadth(SimpleNamespace(market=market))
            assert tool['stale'] and provider.breadth_calls==1
            provider.result=parse_breadth(payload([row('000002',1,430,1840,44)]))
            partial=await market.get_breadth()
            assert partial.data.partial and partial.data.advancing is None
            assert partial.data.exchanges[2].stale and partial.data.exchanges[2].as_of==a.data.exchanges[2].as_of
            assert partial.data.limit_down.stale and partial.data.limit_down.count==56 and partial.data.limit_down.date==DAY
            timer.now=62; provider.error=DataSourceError('private')
            old=await market.get_breadth()
            assert old.stale and old.cached_at==partial.cached_at
            await market.get_breadth(); assert provider.breadth_calls==3
            await market.aclose()
            restarted=MarketService(provider,snapshot_sessions=sessions)
            recovered_old=await restarted.get_breadth()
            assert recovered_old.stale and recovered_old.cached_at==partial.cached_at
            assert recovered_old.data.exchanges[2].stale
            await restarted.aclose()
            timer.now=65; provider.error=None; provider.result=complete()
            good=await market.get_breadth()
            assert not good.stale and not good.data.partial and good.data.advancing==898
        finally: await market.aclose(); engine.dispose()
    asyncio.run(run())


def test_api_and_agent_trace_use_same_cached_breadth_and_source_errors(tmp_path):
    provider=BreadthProvider()
    def model(messages,_):
        if any(isinstance(p,ToolReturnPart) for p in messages[-1].parts):
            return ModelResponse(parts=[TextPart('统计日期2026-09-28，来源沪深京；仅供参考。')])
        return ModelResponse(parts=[ToolCallPart('get_market_breadth',{})])
    with TestClient(create_app(provider=provider,database_url=f"sqlite:///{(tmp_path/'api.db').as_posix()}",agent_model=FunctionModel(model))) as client:
        assert client.get('/health').status_code==200
        page=client.get('/api/market/breadth'); assert page.status_code==200 and page.json()['data']['advancing']==898
        chat=client.post('/api/agent/chat',json={'message':'今天全市场涨跌家数'})
        assert chat.status_code==200,chat.text
        assert provider.breadth_calls==1
        trace=str(client.get('/api/agent/traces/recent').json()); assert 'get_market_breadth' in trace and 'success' in trace
    for error,status in [(DataSourceError('private'),503),(ProviderTimeoutError('private'),504)]:
        provider=BreadthProvider(); provider.error=error
        with TestClient(create_app(provider=provider,database_url=f"sqlite:///{(tmp_path/f'{status}.db').as_posix()}")) as client:
            bad=client.get('/api/market/breadth'); assert bad.status_code==status and 'private' not in bad.text

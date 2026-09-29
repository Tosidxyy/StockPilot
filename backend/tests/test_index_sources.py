import asyncio
from datetime import datetime, timezone, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from app.agent.execution import data_notes
from app.main import create_app
from app.models.market import MarketIndex
from app.providers.exceptions import DataSourceError
from app.providers.quotes import INDEX_CODES, PublicQuotes
from app.providers.resilient import ResilientMarketProvider
from test_api import FakeProvider


NAMES = ['上证指数', '深证成指', '创业板指']


def index_body(source, code, symbol, name):
    if source == 'sina':
        fields = ['0'] * 33
        fields[:6] = [name, '10', '10', '11', '12', '9']
        fields[8:10] = ['12345' if code == 'sh000001' else '1234500', '135795']
        fields[30:32] = ['2026-09-29', '15:00:03']
        return f'var hq_str_{code}="{",".join(fields)}";'
    fields = [''] * 58
    for position, value in {1:name, 2:symbol, 3:'11', 4:'10', 5:'10', 6:'12345',
        30:'20260929150003', 31:'1', 32:'10', 33:'12', 34:'9', 37:'13', 38:'0', 39:'0', 57:'13.5795'}.items():
        fields[position] = value
    return f'v_{code}="{"~".join(fields)}";'


def batch_body(source):
    return ''.join(index_body(source,code,symbol,name) for (code,symbol),name in zip(INDEX_CODES.items(),NAMES))


@pytest.mark.parametrize('source',['tencent','sina'])
def test_index_batch_mapping_units_and_stock_000001_isolation(source):
    requests = []
    def handler(request):
        value = request.url.params['q' if source == 'tencent' else 'list']
        requests.append(value)
        assert b'%2C' not in request.url.query
        content = batch_body(source) if value.startswith('sh000001') else index_body(source,'sz000001','000001','平安银行')
        return httpx.Response(200, content=content.encode('gb18030'))
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = PublicQuotes(source, client)
            rows = await provider.get_indices()
            stock = (await provider.get_quotes(['000001']))[0]
        assert requests == ['sh000001,sz399001,sz399006','sz000001']
        assert [row.symbol for row in rows] == list(INDEX_CODES.values())
        assert [row.name for row in rows] == NAMES
        for row in rows:
            assert row.value == 11 and row.change_amount == 1 and row.change_percent == 10
            assert row.high == 12 and row.low == 9
            assert row.volume == 12345 and row.turnover == pytest.approx(135795)
            assert row.source == source and row.as_of.isoformat() == '2026-09-29T15:00:03+08:00'
        assert stock.name == '平安银行' and stock.symbol == '000001'
    asyncio.run(run())


@pytest.mark.parametrize('source',['tencent','sina'])
@pytest.mark.parametrize('failure',['html','missing','wrong_market','zero','invalid_time','nan'])
def test_invalid_or_incomplete_index_batch_is_never_success(source,failure):
    content = batch_body(source)
    if failure == 'html': content = '<html>upstream unavailable</html>'
    elif failure == 'missing': content = index_body(source,'sh000001','000001',NAMES[0])
    elif failure == 'wrong_market': content = content.replace('sh000001','sz000001')
    elif failure == 'zero': content = content.replace('~11~','~0~') if source=='tencent' else content.replace(',11,',',0,')
    elif failure == 'nan': content = content.replace('~11~','~NaN~') if source=='tencent' else content.replace(',11,',',NaN,')
    else: content = content.replace('20260929150003','invalid').replace('15:00:03','invalid')
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _:httpx.Response(200,content=content.encode('gb18030')))) as client:
            with pytest.raises(DataSourceError): await PublicQuotes(source,client).get_indices()
    asyncio.run(run())


def indices(source='tencent',value=11):
    return [MarketIndex(symbol=symbol,name=name,value=value,change_amount=1,change_percent=10,
        volume=12345,turnover=135795,source=source,
        as_of=datetime(2026,9,29,15,0,3,tzinfo=timezone(timedelta(hours=8))))
        for symbol,name in zip(INDEX_CODES.values(),NAMES)]


def test_index_failure_cooldown_recovery_and_stock_quotes_remain_independent():
    clock = [0.0]; failures = [True,False]; calls = []; closed = []
    class Source(FakeProvider):
        def __init__(self,position): super().__init__(); self.position=position
        async def get_indices(self):
            calls.append(self.position)
            if failures[self.position]: raise DataSourceError('offline')
            return indices('tencent' if self.position==0 else 'sina')
        async def aclose(self): closed.append(self.position)
    async def run():
        sources=[Source(0),Source(1)]
        provider=ResilientMarketProvider(sources[0],quote_sources=sources,index_sources=sources,timer=lambda:clock[0])
        assert (await provider.get_indices())[0].source == 'sina'
        assert calls == [0,1]
        assert (await provider.get_quotes(['000001']))[0].name == '测试股票'
        assert sources[0].quote_batches == [('000001',)]
        await provider.get_indices(); assert calls == [0,1,1]
        clock[0]=31; failures[0]=False
        assert (await provider.get_indices())[0].source == 'tencent'
        assert calls[-1]==0
        clock[0]=62; failures[:]=[True,True]
        with pytest.raises(DataSourceError): await provider.get_indices()
        count=len(calls)
        with pytest.raises(DataSourceError): await provider.get_indices()
        assert len(calls)==count
        await provider.aclose()
        assert sorted(closed)==[0,1]
    asyncio.run(run())


def test_index_failover_does_not_merge_partial_batches_or_accept_duplicates():
    calls=[]
    class Source(FakeProvider):
        def __init__(self,position): super().__init__(); self.position=position
        async def get_indices(self):
            calls.append(self.position)
            rows=indices('eastmoney',self.position+10)
            return rows[:2] if self.position==0 else [rows[0]]*3 if self.position==1 else rows
        async def aclose(self): pass
    async def run():
        sources=[Source(i) for i in range(3)]
        provider=ResilientMarketProvider(sources[0],index_sources=sources)
        assert [row.value for row in await provider.get_indices()]==[12]*3
        assert calls==[0,1,2]
        await provider.aclose()
    asyncio.run(run())


def test_index_source_times_survive_api_restart_and_agent_notes(tmp_path):
    class Online(FakeProvider):
        async def get_indices(self): return indices()
    url=f"sqlite:///{(tmp_path/'indices.db').as_posix()}"
    with TestClient(create_app(provider=Online(),database_url=url,prefetch_enabled=False)) as client:
        saved=client.get('/api/market/indices').json()
        assert not saved['stale'] and len(saved['data'])==3
    offline=FakeProvider();offline.error=DataSourceError('offline')
    with TestClient(create_app(provider=offline,database_url=url,prefetch_enabled=False)) as client:
        response=client.get('/api/market/indices')
        assert response.status_code==200
        result=response.json()
        assert result['stale'] and result['data']==saved['data'] and result['cached_at']==saved['cached_at']
        note=data_notes([{'tool':'get_market_indices','input':{},'result':{'indices':result['data'],
            'stale':True,'cached_at':result['cached_at']}}])
        assert '腾讯财经' in note and '旧缓存' in note and '2026-09-29 15:00:03' in note
        assert '东方财富' not in note
    # Legacy persisted JSON has no source timestamp: never fabricate one.
    legacy=indices()[0].model_dump(exclude={'source','as_of'})
    parsed=MarketIndex.model_validate(legacy)
    assert parsed.source=='eastmoney' and parsed.as_of is None


def test_default_indices_use_public_quote_sources_before_eastmoney():
    async def run():
        provider=ResilientMarketProvider()
        assert provider._index_sources is provider._quote_sources
        assert [s.source for s in provider._index_sources[:2]]==['tencent','sina']
        assert provider._index_sources[-1] is provider._primary
        await provider.aclose()
    asyncio.run(run())

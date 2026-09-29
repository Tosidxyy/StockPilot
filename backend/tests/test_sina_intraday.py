import asyncio
import json
from datetime import datetime

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.models.market import IntradayPoint
from app.providers.exceptions import DataSourceError, InvalidSymbolError
from app.providers.sina_intraday import SinaIntraday, parse_sina_minutes
from app.providers.resilient import ResilientMarketProvider
from test_api import FakeProvider
from test_services import Clock


def candle(day, shares="150", amount="780"):
    return {"day":day,"open":"5.1","high":"5.3","low":"5.0","close":"5.2","volume":shares,"amount":amount}


def wrapper(rows):
    return "/*<script>untrusted()</script>*/\nvar _data=(" + json.dumps(rows) + ");"


def test_latest_day_sort_duplicate_fractional_hands_and_missing_amount():
    points = parse_sina_minutes(wrapper([
        candle("2026-09-28 15:00:00"),candle("2026-09-29 09:32:00"),
        candle("2026-09-29 09:31:00", "123", None),candle("2026-09-29 09:32:00", "200", "1040"),
        candle("2026-09-29 12:00:00"),candle("2026-09-29 15:30:00")]))
    assert [p.time for p in points] == [datetime(2026,9,29,9,31),datetime(2026,9,29,9,32)]
    assert points[0].volume == 1.23 and points[0].turnover is None
    assert points[1].volume == 2 and points[1].turnover == 1040
    assert all(p.source == "sina" for p in points)


@pytest.mark.parametrize("text", ["<html>blocked</html>","var _data=(null);",wrapper([]),
    wrapper([candle("2026-09-29 09:31:00", "-1")]),
    wrapper([candle("2026-09-29 09:31:00", "1.5")]),
    wrapper([dict(candle("2026-09-29 09:31:00"), close="NaN")]),
    wrapper([candle("2026-09-28 09:31:00"),candle("2026-09-29 12:00:00")]),
    wrapper([candle("2026-09-29 09:31:00")])+"evil();"])
def test_bad_or_empty_series_never_overwrites_cache(text):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _:httpx.Response(200,text=text))) as client:
            with pytest.raises(DataSourceError): await SinaIntraday(client).get_stock_intraday("600519")
    asyncio.run(run())


@pytest.mark.parametrize("kind,code,expected",[("stock","600519","sh600519"),("stock","300750","sz300750"),
    ("stock","832566","bj832566"),("index","000001","sh000001"),("index","399006","sz399006")])
def test_symbol_request_and_volume_unit(kind,code,expected):
    def handler(request):
        assert request.url.params["symbol"] == expected
        assert request.url.params["scale"] == "1" and request.url.params["datalen"] == "300"
        return httpx.Response(200,text=wrapper([candle("2026-09-29 09:31:00")]))
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            source=SinaIntraday(client)
            points=await (source.get_stock_intraday(code) if kind=="stock" else source.get_index_intraday(code))
            assert points[0].price==5.2 and points[0].volume==1.5
            with pytest.raises(InvalidSymbolError): await source.get_index_intraday("123456")
    asyncio.run(run())


def test_three_source_order_cooldown_recovery_and_symbol_isolation():
    calls=[]; failing=set(); clock=Clock()
    class Source(FakeProvider):
        def __init__(self,name):super().__init__();self.name=name
        async def get_stock_intraday(self,symbol):return await self.read("stock",symbol)
        async def get_index_intraday(self,symbol):return await self.read("index",symbol)
        async def read(self,kind,symbol):
            calls.append((self.name,kind,symbol))
            if (self.name,kind,symbol) in failing: raise DataSourceError("offline")
            return [IntradayPoint(time=datetime(2026,9,29,9,31),price=5,volume=1,turnover=500,source=self.name)]
        async def aclose(self):pass
    async def run():
        sources=[Source(name) for name in ("sina","tencent","eastmoney")]
        provider=ResilientMarketProvider(sources[2],intraday=sources[1],minute_sources=sources,timer=clock)
        try:
            assert (await provider.get_stock_intraday("600519"))[0].source=="sina"
            assert len(calls)==1
            failing.add(("sina","stock","600519"))
            assert (await provider.get_stock_intraday("600519"))[0].source=="tencent"
            assert calls[-2:]==[("sina","stock","600519"),("tencent","stock","600519")]
            failing.add(("tencent","stock","600519"));clock.now=2
            assert (await provider.get_stock_intraday("600519"))[0].source=="eastmoney"
            assert calls[-2:]==[("tencent","stock","600519"),("eastmoney","stock","600519")]
            assert (await provider.get_stock_intraday("300750"))[0].source=="sina"
            assert (await provider.get_index_intraday("000001"))[0].source=="sina"
            failing.clear();clock.now=31
            assert (await provider.get_stock_intraday("600519"))[0].source=="sina"
        finally:await provider.aclose()
    asyncio.run(run())


def test_sina_persistent_cache_retains_actual_source_after_restart_outage(tmp_path):
    failed=[False]
    def handler(_request):
        if failed[0]: raise httpx.RemoteProtocolError("offline")
        return httpx.Response(200,text=wrapper([candle("2026-09-29 09:31:00", "123")]))
    def provider():
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        sina=SinaIntraday(client);sina._owns_client=True
        class Offline(FakeProvider):
            async def aclose(self):pass
            async def get_stock_intraday(self,symbol):raise DataSourceError("offline")
        offline=Offline()
        return ResilientMarketProvider(offline,intraday=offline,minute_sources=[sina,offline])
    url=f"sqlite:///{(tmp_path/'sina.db').as_posix()}"
    first=provider()
    with TestClient(create_app(provider=first,database_url=url)) as client:
        result=client.get('/api/stocks/600519/intraday')
        assert result.status_code==200
        saved=result.json();assert saved['data'][0]['source']=='sina' and saved['data'][0]['volume']==1.23
    asyncio.run(first.aclose());failed[0]=True;second=provider()
    with TestClient(create_app(provider=second,database_url=url)) as client:
        result=client.get('/api/stocks/600519/intraday').json()
        assert result['stale'] and result['data']==saved['data'] and result['cached_at']==saved['cached_at']
    asyncio.run(second.aclose())

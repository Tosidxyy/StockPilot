import asyncio

import httpx
import pytest

from app.providers.quotes import PublicQuotes
from app.providers.resilient import ResilientMarketProvider
from app.providers.exceptions import DataSourceError
from app.agent.service import deepseek_settings
from app.core.config import Settings
from test_api import FakeProvider


def body(source, symbol="600519"):
    if source == "sina":
        row = ["0"] * 33
        row[0:6] = ["贵州茅台", "10", "10", "11", "12", "9"]
        row[8:10] = ["12345", "135795"]
        row[30:32] = ["2026-09-29", "10:20:30"]
        return f'var hq_str_sh{symbol}="{",".join(row)}";'
    row = [""] * 58
    for index, value in {1:"贵州茅台",2:symbol,3:"11",4:"10",5:"10",6:"123",30:"20260929102030",31:"1",32:"10",33:"12",34:"9",37:"13",38:".5",39:"20",57:"13.5795"}.items():
        row[index] = value
    return f'v_sh{symbol}="{"~".join(row)}";'


@pytest.mark.parametrize("source", ["tencent", "sina"])
def test_quote_mapping_units_source_time_and_requested_identity(source):
    def handler(request):
        assert request.url.params["q" if source == "tencent" else "list"] == "sh600519,sz300750"
        assert b"%2C" not in request.url.query
        return httpx.Response(200, content=(body(source) + body(source,"600000")).encode("gb18030"))
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            quotes = await PublicQuotes(source, client).get_quotes(["600519","300750","600519"])
        assert len(quotes) == 1
        q = quotes[0]
        assert q.name == "贵州茅台" and q.price == 11 and q.change_percent == 10
        assert q.volume == 123 and q.turnover == pytest.approx(135795)
        assert q.source == source and q.as_of.isoformat() == "2026-09-29T10:20:30+08:00"
        if source == "sina": assert q.turnover_rate is None and q.pe_ratio is None
    asyncio.run(run())


@pytest.mark.parametrize("content", ["<html>blocked</html>", 'v_sh600519="garbled";', body("tencent").replace("~11~", "~NaN~")])
def test_bad_quote_response_never_becomes_price(content):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _:httpx.Response(200,content=content.encode("gb18030")))) as client:
            with pytest.raises(DataSourceError):
                await PublicQuotes("tencent",client).get_quotes(["600519"])
    asyncio.run(run())


def test_quote_failover_cooldown_recovery_and_all_failed():
    calls = []
    clock = [0.0]
    failing = [True, False]
    class Source(FakeProvider):
        def __init__(self, index): super().__init__(); self.index = index
        async def get_quotes(self, symbols):
            calls.append(self.index)
            if failing[self.index]: raise DataSourceError("private upstream error")
            return await super().get_quotes(symbols)
        async def aclose(self): pass
    async def run():
        sources = [Source(0),Source(1)]
        provider = ResilientMarketProvider(sources[0],quote_sources=sources,timer=lambda:clock[0])
        assert (await provider.get_quotes(["600519"]))[0].price == 12.3
        assert calls == [0,1]
        await provider.get_quotes(["600519"])
        assert calls == [0,1,1]
        clock[0] = 31; failing[0] = False
        await provider.get_quotes(["600519"])
        assert calls[-1] == 0
        failing[:] = [True,True]; clock[0] = 62
        with pytest.raises(DataSourceError): await provider.get_quotes(["600519"])
        count = len(calls)
        with pytest.raises(DataSourceError): await provider.get_quotes(["600519"])
        assert len(calls) == count
        await provider.aclose()
    asyncio.run(run())


def test_thinking_is_explicit_for_deepseek_and_not_sent_to_other_endpoints():
    settings = Settings(_env_file=None,model_name="deepseek-flash",model_base_url="https://api.deepseek.com")
    assert deepseek_settings(settings)["extra_body"] == {"thinking":{"type":"enabled"}}
    assert deepseek_settings(settings.model_copy(update={"model_thinking_enabled":False}))["openai_reasoning_effort"] == "none"
    assert deepseek_settings(settings.model_copy(update={"model_base_url":"https://example.com/v1"})) is None


def test_private_reasoning_is_not_saved_or_returned(tmp_path):
    from fastapi.testclient import TestClient
    from pydantic_ai.models.function import FunctionModel
    from pydantic_ai.messages import ModelResponse, ThinkingPart, TextPart
    from app.main import create_app
    def respond(_messages, _info):
        return ModelResponse(parts=[ThinkingPart("private-reasoning-marker"),TextPart("你好，我可以帮你分析股票。")])
    with TestClient(create_app(provider=FakeProvider(), database_url=f"sqlite:///{(tmp_path/'private.db').as_posix()}",agent_model=FunctionModel(respond))) as client:
        response=client.post('/api/agent/chat',json={'message':'你好'})
        assert response.status_code == 200
        session=response.json()['session_id']
        assert "private-reasoning-marker" not in response.text
        assert "private-reasoning-marker" not in client.get('/api/agent/sessions/'+session).text
        assert client.get('/api/agent/traces/'+session).json()['data'] == []

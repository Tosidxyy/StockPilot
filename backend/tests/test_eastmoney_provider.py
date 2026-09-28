import asyncio

import httpx
import pytest

from app.providers.eastmoney import EastMoneyProvider, to_secid
from app.providers.exceptions import DataSourceError, InvalidSymbolError, ProviderTimeoutError


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_to_secid_accepts_a_share_codes_and_rejects_invalid_symbols() -> None:
    assert to_secid("600519") == "1.600519"
    assert to_secid("000001") == "0.000001"
    assert to_secid("300750") == "0.300750"
    assert to_secid("832566") == "0.832566"
    for symbol in ("60051", "ABCDEF", "１２３４５６", "900901"):
        with pytest.raises(InvalidSymbolError):
            to_secid(symbol)


def test_search_filters_and_deduplicates_a_shares() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["input"] == "茅台"
        assert request.url.params["type"] == "14"
        return httpx.Response(200, json={"QuotationCodeTable": {"Status": 0, "Data": [
            {"Code": "600519", "Name": "贵州茅台", "Classify": "AStock"},
            {"Code": "600519", "Name": "贵州茅台", "Classify": "AStock"},
            {"Code": "00700", "Name": "Tencent", "Classify": "HKStock"},
        ]}})

    async def run() -> None:
        async with _client(handler) as client:
            results = await EastMoneyProvider(client).search_stocks(" 茅台 ")
        assert len(results) == 1
        assert results[0].model_dump() == {"symbol": "600519", "name": "贵州茅台", "market": "SH"}

    asyncio.run(run())


def test_search_returns_empty_list_for_no_matches() -> None:
    async def run() -> None:
        async with _client(lambda request: httpx.Response(200, json={
            "QuotationCodeTable": {"Status": 0, "TotalCount": 0, "Data": None}
        })) as client:
            assert await EastMoneyProvider(client).search_stocks("no-match") == []

    asyncio.run(run())


def test_quotes_are_batched_scaled_and_ordered() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.url.params["secids"] == "1.600519,0.000001"
        return httpx.Response(200, json={"rc": 0, "data": {"diff": [
            {"f12": "000001", "f14": "平安银行", "f2": 1130, "f3": -44, "f4": -5,
             "f5": 1043819, "f6": 1186736895.7, "f8": 54, "f9": 427,
             "f15": 1147, "f16": 1129, "f17": 1135, "f18": 1135},
            {"f12": "600519", "f14": "贵州茅台", "f2": 123700, "f3": -114,
             "f4": -1424, "f5": 31239, "f6": 3867310920.0, "f8": 25,
             "f9": 1737, "f15": 125613, "f16": 123105, "f17": 125001, "f18": 125124},
        ]}})

    async def run() -> None:
        async with _client(handler) as client:
            quotes = await EastMoneyProvider(client).get_quotes(["600519", "000001", "600519"])
        assert [quote.symbol for quote in quotes] == ["600519", "000001"]
        assert quotes[0].price == 1237.0
        assert quotes[0].change_percent == -1.14
        assert quotes[0].previous_close == 1251.24
        assert quotes[1].price == 11.3
        assert quotes[1].turnover_rate == 0.54
        assert len(calls) == 1

    asyncio.run(run())


def test_indices_use_three_explicit_market_identifiers() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["secids"] == "1.000001,0.399001,0.399006"
        return httpx.Response(200, json={"rc": 0, "data": {"diff": [
            {"f12": code, "f14": name, "f2": value, "f3": 125, "f4": 30,
             "f5": 1000, "f6": 2000.0, "f15": value + 100, "f16": value - 100}
            for code, name, value in (
                ("000001", "上证指数", 388837),
                ("399001", "深证成指", 1331697),
                ("399006", "创业板指", 328895),
            )
        ]}})

    async def run() -> None:
        async with _client(handler) as client:
            indices = await EastMoneyProvider(client).get_indices()
        assert [item.symbol for item in indices] == ["000001", "399001", "399006"]
        assert indices[0].value == 3888.37
        assert indices[0].high == 3889.37
        assert indices[0].low == 3887.37
        assert indices[1].change_percent == 1.25

    asyncio.run(run())


def test_index_intraday_uses_backup_and_keeps_latest_trading_day() -> None:
    hosts = []

    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        assert request.url.params["secid"] == "1.000001"
        if request.url.host == "push2.eastmoney.com":
            return httpx.Response(503)
        return httpx.Response(200, json={"rc": 0, "data": {"trends": [
            "2026-09-24 15:00,3000,3001,3002,2999,100,2000.0,3001",
            "2026-09-25 09:30,3010,3011,3012,3009,110,2100.0,3011",
            "2026-09-25 09:31,3011,3013,3014,3010,120,2200.0,3012",
        ]}})

    async def run() -> None:
        async with _client(handler) as client:
            provider = EastMoneyProvider(client)
            first = await provider.get_index_intraday()
            await provider.get_index_intraday()
            assert [point.price for point in first] == [3011.0, 3013.0]
            assert first[0].volume == 110
            with pytest.raises(InvalidSymbolError):
                await provider.get_index_intraday("600519")
        assert hosts == ["push2.eastmoney.com", "push2delay.eastmoney.com", "push2delay.eastmoney.com"]

    asyncio.run(run())


@pytest.mark.parametrize("period,klt", [("daily", "101"), ("weekly", "102")])
def test_kline_periods_and_standard_model(period: str, klt: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["secid"] == "1.600519"
        assert request.url.params["klt"] == klt
        assert request.url.params["fqt"] == "0"
        assert request.url.params["lmt"] == "2"
        assert request.url.params["beg"] == "0"
        assert request.url.params["end"] == "20500101"
        assert request.url.params["ut"]
        return httpx.Response(200, json={"rc": 0, "data": {
            "klines": ["2026-09-24,1250.01,1237.00,1256.13,1231.05,31239,3867310920.0,0,0,0,0"]
        }})

    async def run() -> None:
        async with _client(handler) as client:
            candles = await EastMoneyProvider(client).get_kline("600519", period, 2)
        assert len(candles) == 1
        assert candles[0].close == 1237.0
        assert candles[0].volume == 31239
        assert candles[0].date.isoformat() == "2026-09-24"

    asyncio.run(run())


def test_quote_fallback_then_prefers_last_successful_endpoint() -> None:
    hosts = []

    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        if request.url.host == "push2.eastmoney.com":
            return httpx.Response(503)
        return httpx.Response(200, json={"rc": 0, "data": {"diff": [
            {"f12": "600519", "f14": "贵州茅台", "f2": 123700}
        ]}})

    async def run() -> None:
        async with _client(handler) as client:
            provider = EastMoneyProvider(client)
            await provider.get_quotes(["600519"])
            await provider.get_quotes(["600519"])
        assert hosts == ["push2.eastmoney.com", "push2delay.eastmoney.com", "push2delay.eastmoney.com"]

    asyncio.run(run())


def test_quote_fallback_on_malformed_primary_payload() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "push2.eastmoney.com":
            return httpx.Response(200, json={"rc": 0, "data": None})
        return httpx.Response(200, json={"rc": 0, "data": {"diff": [
            {"f12": "600519", "f14": "贵州茅台", "f2": 123700}
        ]}})

    async def run() -> None:
        async with _client(handler) as client:
            quotes = await EastMoneyProvider(client).get_quotes(["600519"])
        assert quotes[0].price == 1237.0

    asyncio.run(run())


def test_timeout_and_invalid_responses_become_provider_errors() -> None:
    async def run() -> None:
        async with _client(lambda request: (_ for _ in ()).throw(httpx.ReadTimeout("late"))) as client:
            with pytest.raises(ProviderTimeoutError):
                await EastMoneyProvider(client).get_quotes(["600519"])
        async with _client(lambda request: httpx.Response(200, text="not json")) as client:
            with pytest.raises(DataSourceError):
                await EastMoneyProvider(client).get_quotes(["600519"])
        async with _client(lambda request: httpx.Response(200, json={"rc": 1})) as client:
            with pytest.raises(DataSourceError):
                await EastMoneyProvider(client).get_quotes(["600519"])

    asyncio.run(run())


def test_empty_kline_is_reported_as_data_source_failure() -> None:
    async def run() -> None:
        async with _client(lambda request: httpx.Response(200, json={"rc": 0, "data": {"klines": []}})) as client:
            with pytest.raises(DataSourceError):
                await EastMoneyProvider(client).get_kline("600519")

    asyncio.run(run())


@pytest.mark.parametrize("failure", ["disconnect", "timeout", "empty", "malformed"])
def test_kline_fallback_prefers_valid_node_and_limits_latest_history(failure: str) -> None:
    hosts = []

    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        if request.url.host == "push2his.eastmoney.com":
            if failure == "disconnect":
                raise httpx.RemoteProtocolError("disconnected", request=request)
            if failure == "timeout":
                raise httpx.ReadTimeout("late", request=request)
            rows = [] if failure == "empty" else ["invalid"]
        else:
            # Endpoint may ignore lmt; keep latest dates in ascending order.
            rows = [f"2026-09-{day},10,11,12,9,100,1100" for day in (28, 24, 25)]
        return httpx.Response(200, json={"rc": 0, "data": {"klines": rows}})

    async def run() -> None:
        async with _client(handler) as client:
            provider = EastMoneyProvider(client)
            for _ in range(2):
                candles = await provider.get_kline("300750", limit=2)
                assert [item.date.isoformat() for item in candles] == ["2026-09-25", "2026-09-28"]
                assert candles[-1].close == 11
        primary_calls = 2 if failure == "disconnect" else 1
        assert hosts == ["push2his.eastmoney.com"] * primary_calls + [
            "1.push2his.eastmoney.com", "1.push2his.eastmoney.com"
        ]

    asyncio.run(run())


def test_kline_can_return_to_primary_when_preferred_backup_fails() -> None:
    hosts = []

    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        if len(hosts) in (1, 3):
            return httpx.Response(503)
        return httpx.Response(200, json={"rc": 0, "data": {
            "klines": ["2026-09-28,10,11,12,9,100,1100"]
        }})

    async def run() -> None:
        async with _client(handler) as client:
            provider = EastMoneyProvider(client)
            for _ in range(3):
                assert len(await provider.get_kline("300750")) == 1
        assert hosts == [
            "push2his.eastmoney.com", "1.push2his.eastmoney.com",
            "1.push2his.eastmoney.com", "push2his.eastmoney.com", "push2his.eastmoney.com",
        ]

    asyncio.run(run())


def test_owned_client_uses_complete_web_headers() -> None:
    async def run() -> None:
        async with EastMoneyProvider() as provider:
            assert "AppleWebKit" in provider._client.headers["User-Agent"]
            assert provider._client.headers["Referer"] == "https://quote.eastmoney.com/"
            assert provider._client.headers["Accept"] == "*/*"

    asyncio.run(run())


def test_kline_retries_disconnected_socket_once_before_switching_nodes() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            raise httpx.RemoteProtocolError("disconnected", request=request)
        return httpx.Response(200, json={"rc": 0, "data": {
            "klines": ["2026-09-28,10,11,12,9,100,1100"]
        }})

    async def run() -> None:
        async with _client(handler) as client:
            assert len(await EastMoneyProvider(client).get_kline("300750")) == 1
        assert [request.url.host for request in calls] == ["push2his.eastmoney.com"] * 2
        assert all(request.url.params["_"].isdigit() for request in calls)

    asyncio.run(run())


def test_stock_intraday_converts_symbol_and_keeps_latest_day() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["secid"] == "0.300750"
        assert request.url.params["ndays"] == "1"
        return httpx.Response(200, json={"rc": 0, "data": {"trends": [
            "2026-09-25 15:00,10,11,12,9,100,1100,11",
            "2026-09-28 09:30,12,13,14,11,200,2600,13",
        ]}})

    async def run() -> None:
        async with _client(handler) as client:
            provider = EastMoneyProvider(client)
            points = await provider.get_stock_intraday("300750")
            assert len(points) == 1
            assert points[0].price == 13
            assert points[0].volume == 200
            assert points[0].time.isoformat() == "2026-09-28T09:30:00"
            with pytest.raises(InvalidSymbolError):
                await provider.get_stock_intraday("bad")

    asyncio.run(run())

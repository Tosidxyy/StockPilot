"""EastMoney public-Web adapter for P0 A-share market data."""

from datetime import date, datetime
from time import time_ns
from typing import Any, Literal, Sequence

import httpx

from app.models.market import IntradayPoint, KlineItem, MarketIndex, StockQuote, SymbolSearchResult
from app.providers.base import MarketDataProvider
from app.providers.exceptions import DataSourceError, InvalidSymbolError, ProviderTimeoutError

_QUOTE_ENDPOINTS = (
    "https://push2.eastmoney.com/api/qt/ulist.np/get",
    "https://push2delay.eastmoney.com/api/qt/ulist.np/get",
)
_KLINE_ENDPOINTS = (
    "https://push2his.eastmoney.com/api/qt/stock/kline/get",
    "https://1.push2his.eastmoney.com/api/qt/stock/kline/get",
)
# Public token used by EastMoney's quote chart, not an account API key.
_KLINE_WEB_TOKEN = "fa5fd1943c7b386f172d6893dbfba10b"
_SEARCH_ENDPOINT = "https://searchapi.eastmoney.com/api/suggest/get"
_TREND_ENDPOINTS = (
    "https://push2.eastmoney.com/api/qt/stock/trends2/get",
    "https://push2delay.eastmoney.com/api/qt/stock/trends2/get",
)
_QUOTE_FIELDS = "f2,f3,f4,f5,f6,f8,f9,f12,f13,f14,f15,f16,f17,f18"
_INDEX_SECIDS = ("1.000001", "0.399001", "0.399006")
_INDEX_NAMES = {"000001": "上证指数", "399001": "深证成指", "399006": "创业板指"}
_INDEX_IDS = dict(zip(_INDEX_NAMES, _INDEX_SECIDS))


def to_secid(symbol: str) -> str:
    """Convert a six-digit A-share code to an EastMoney security identifier."""
    if not isinstance(symbol, str) or len(symbol) != 6 or not symbol.isascii() or not symbol.isdigit():
        raise InvalidSymbolError(f"Invalid A-share symbol: {symbol!r}")
    if symbol[0] == "6":
        return f"1.{symbol}"
    if symbol[0] in "0348":
        return f"0.{symbol}"
    raise InvalidSymbolError(f"Unsupported A-share symbol: {symbol!r}")


def _number(value: Any, *, scale: int = 1, required: bool = False) -> float | None:
    if value is None or value == "-" or value == "":
        if required:
            raise DataSourceError("EastMoney response is missing a required number")
        return None
    try:
        return float(value) / scale
    except (TypeError, ValueError, OverflowError) as exc:
        raise DataSourceError("EastMoney response contains an invalid number") from exc


def _integer(value: Any, *, required: bool = False) -> int | None:
    result = _number(value, required=required)
    return None if result is None else int(result)


class EastMoneyProvider(MarketDataProvider):
    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(connect=1.5, read=3.0, write=3.0, pool=3.0),
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
                ),
                "Referer": "https://quote.eastmoney.com/",
                "Accept": "*/*",
            },
        )
        self._preferred_quote_endpoint = _QUOTE_ENDPOINTS[0]
        self._preferred_trend_endpoint = _TREND_ENDPOINTS[0]
        self._preferred_kline_endpoint = _KLINE_ENDPOINTS[0]

    async def __aenter__(self) -> "EastMoneyProvider":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _request_json(
        self, endpoints: Sequence[str], params: dict[str, str | int]
    ) -> dict[str, Any]:
        last_error: DataSourceError | None = None
        for endpoint in endpoints:
            try:
                response = await self._client.get(endpoint, params=params)
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise DataSourceError("EastMoney returned an invalid response")
                if "rc" in payload and payload["rc"] != 0:
                    raise DataSourceError(f"EastMoney returned rc={payload['rc']}")
                if endpoint in _QUOTE_ENDPOINTS:
                    self._preferred_quote_endpoint = endpoint
                if endpoint in _TREND_ENDPOINTS:
                    self._preferred_trend_endpoint = endpoint
                return payload
            except httpx.TimeoutException as exc:
                last_error = ProviderTimeoutError("EastMoney request timed out")
                last_error.__cause__ = exc
            except (httpx.HTTPError, ValueError, DataSourceError) as exc:
                last_error = DataSourceError("EastMoney request failed")
                last_error.__cause__ = exc
        assert last_error is not None
        raise last_error

    def _quote_endpoints(self) -> tuple[str, str]:
        preferred = self._preferred_quote_endpoint
        other = next(endpoint for endpoint in _QUOTE_ENDPOINTS if endpoint != preferred)
        return preferred, other

    async def search_stocks(self, query: str, limit: int = 20) -> list[SymbolSearchResult]:
        query = query.strip()
        if not query:
            return []
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        payload = await self._request_json(
            (_SEARCH_ENDPOINT,), {"input": query, "type": 14, "count": limit}
        )
        table = payload.get("QuotationCodeTable")
        if not isinstance(table, dict) or table.get("Status") != 0:
            raise DataSourceError("EastMoney search response is invalid")
        if table.get("Data") is None and table.get("TotalCount") == 0:
            return []
        if not isinstance(table.get("Data"), list):
            raise DataSourceError("EastMoney search response is invalid")
        results: list[SymbolSearchResult] = []
        seen: set[str] = set()
        for item in table["Data"]:
            if not isinstance(item, dict) or item.get("Classify") != "AStock":
                continue
            symbol, name = item.get("Code"), item.get("Name")
            if not isinstance(symbol, str) or not isinstance(name, str) or symbol in seen:
                continue
            try:
                to_secid(symbol)
            except InvalidSymbolError:
                continue
            market = "SH" if symbol.startswith("6") else "BJ" if symbol.startswith(("4", "8")) else "SZ"
            results.append(SymbolSearchResult(symbol=symbol, name=name, market=market))
            seen.add(symbol)
        return results

    async def _quote_rows(self, secids: Sequence[str]) -> list[dict[str, Any]]:
        params = {"secids": ",".join(secids), "fields": _QUOTE_FIELDS}
        last_error: DataSourceError | None = None
        for endpoint in self._quote_endpoints():
            try:
                payload = await self._request_json((endpoint,), params)
                data = payload.get("data")
                if not isinstance(data, dict) or not isinstance(data.get("diff"), list):
                    raise DataSourceError("EastMoney quote response is invalid")
                rows = data["diff"]
                if not all(isinstance(row, dict) for row in rows):
                    raise DataSourceError("EastMoney quote response contains an invalid row")
                return rows
            except DataSourceError as exc:
                last_error = exc
        assert last_error is not None
        raise last_error

    async def get_quotes(self, symbols: Sequence[str]) -> list[StockQuote]:
        unique_symbols = list(dict.fromkeys(symbols))
        if not unique_symbols:
            return []
        secids = [to_secid(symbol) for symbol in unique_symbols]
        rows = await self._quote_rows(secids)
        quotes: dict[str, StockQuote] = {}
        for row in rows:
            symbol, name = row.get("f12"), row.get("f14")
            if not isinstance(symbol, str) or not isinstance(name, str):
                raise DataSourceError("EastMoney quote identity is invalid")
            quotes[symbol] = StockQuote(
                symbol=symbol, name=name,
                price=_number(row.get("f2"), scale=100),
                change_percent=_number(row.get("f3"), scale=100),
                change_amount=_number(row.get("f4"), scale=100),
                volume=_integer(row.get("f5")),
                turnover=_number(row.get("f6")),
                turnover_rate=_number(row.get("f8"), scale=100),
                pe_ratio=_number(row.get("f9"), scale=100),
                high=_number(row.get("f15"), scale=100),
                low=_number(row.get("f16"), scale=100),
                open=_number(row.get("f17"), scale=100),
                previous_close=_number(row.get("f18"), scale=100),
            )
        return [quotes[symbol] for symbol in unique_symbols if symbol in quotes]

    async def get_indices(self) -> list[MarketIndex]:
        rows = await self._quote_rows(_INDEX_SECIDS)
        indices: dict[str, MarketIndex] = {}
        for row in rows:
            symbol = row.get("f12")
            if symbol not in _INDEX_NAMES:
                continue
            indices[symbol] = MarketIndex(
                symbol=symbol, name=str(row.get("f14") or _INDEX_NAMES[symbol]),
                value=_number(row.get("f2"), scale=100),
                change_percent=_number(row.get("f3"), scale=100),
                change_amount=_number(row.get("f4"), scale=100),
                volume=_integer(row.get("f5")),
                turnover=_number(row.get("f6")),
                high=_number(row.get("f15"), scale=100),
                low=_number(row.get("f16"), scale=100),
            )
        if len(indices) != len(_INDEX_SECIDS):
            raise DataSourceError("EastMoney returned incomplete market indices")
        return [indices[symbol] for symbol in _INDEX_NAMES]

    async def get_index_intraday(self, index_code: str = "000001") -> list[IntradayPoint]:
        if index_code not in _INDEX_IDS:
            raise InvalidSymbolError(f"Unsupported market index: {index_code!r}")
        preferred = self._preferred_trend_endpoint
        other = next(endpoint for endpoint in _TREND_ENDPOINTS if endpoint != preferred)
        params = {
            "secid": _INDEX_IDS[index_code], "ndays": 1, "iscr": 0,
            "fields1": "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
        }
        last_error: DataSourceError | None = None
        for endpoint in (preferred, other):
            try:
                payload = await self._request_json((endpoint,), params)
                data = payload.get("data")
                if not isinstance(data, dict) or not isinstance(data.get("trends"), list) or not data["trends"]:
                    raise DataSourceError("EastMoney intraday response is invalid")
                points: list[IntradayPoint] = []
                for row in data["trends"]:
                    if not isinstance(row, str):
                        raise DataSourceError("EastMoney intraday row is invalid")
                    values = row.split(",")
                    if len(values) < 7:
                        raise DataSourceError("EastMoney intraday row has too few fields")
                    try:
                        points.append(IntradayPoint(
                            time=datetime.fromisoformat(values[0]),
                            price=_number(values[2], required=True),
                            volume=_integer(values[5], required=True),
                            turnover=_number(values[6], required=True),
                        ))
                    except (TypeError, ValueError) as exc:
                        raise DataSourceError("EastMoney intraday row is invalid") from exc
                latest_date = max(point.time.date() for point in points)
                return [point for point in points if point.time.date() == latest_date]
            except DataSourceError as exc:
                last_error = exc
        assert last_error is not None
        raise last_error

    async def get_kline(
        self, symbol: str, period: Literal["daily", "weekly"] = "daily", limit: int = 120
    ) -> list[KlineItem]:
        secid = to_secid(symbol)
        if period not in ("daily", "weekly"):
            raise ValueError("period must be daily or weekly")
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        params = {
            "secid": secid, "klt": 101 if period == "daily" else 102,
            "fqt": 0, "lmt": limit,
            "beg": 0, "end": 20500101, "ut": _KLINE_WEB_TOKEN,
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        }
        preferred = self._preferred_kline_endpoint
        other = next(endpoint for endpoint in _KLINE_ENDPOINTS if endpoint != preferred)
        last_error: DataSourceError | None = None
        for endpoint in (preferred, other):
            try:
                payload = await self._request_kline(endpoint, params)
                items = self._parse_kline(payload)
                self._preferred_kline_endpoint = endpoint
                # The public endpoint can ignore lmt and return the full history.
                return sorted(items, key=lambda item: item.date)[-limit:]
            except DataSourceError as exc:
                last_error = exc
        assert last_error is not None
        raise last_error

    async def _request_kline(self, endpoint: str, params: dict[str, str | int]) -> dict[str, Any]:
        for attempt in range(2):
            try:
                # Match the chart's cache-busting requests; Service owns our TTL cache.
                return await self._request_json(
                    (endpoint,), {**params, "_": time_ns() // 1_000_000}
                )
            except DataSourceError as exc:
                # Retry a disconnected socket once, then let get_kline try the other node.
                if attempt or not isinstance(exc.__cause__, httpx.RemoteProtocolError):
                    raise
        raise AssertionError("unreachable")

    @staticmethod
    def _parse_kline(payload: dict[str, Any]) -> list[KlineItem]:
        data = payload.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("klines"), list):
            raise DataSourceError("EastMoney kline response is invalid")
        if not data["klines"]:
            raise DataSourceError("EastMoney returned no kline data")
        items: list[KlineItem] = []
        for row in data["klines"]:
            if not isinstance(row, str):
                raise DataSourceError("EastMoney kline row is invalid")
            values = row.split(",")
            if len(values) < 7:
                raise DataSourceError("EastMoney kline row has too few fields")
            try:
                items.append(KlineItem(
                    date=date.fromisoformat(values[0]),
                    open=_number(values[1], required=True),
                    close=_number(values[2], required=True),
                    high=_number(values[3], required=True),
                    low=_number(values[4], required=True),
                    volume=_integer(values[5], required=True),
                    turnover=_number(values[6], required=True),
                ))
            except (TypeError, ValueError) as exc:
                raise DataSourceError("EastMoney kline row is invalid") from exc
        return items

"""EastMoney daily flow endpoint used by its own stock money-flow page."""

from datetime import date
from math import isfinite
import re
import httpx
from app.models.money_flow import MoneyFlow, MoneyFlowSeries
from app.providers.exceptions import DataSourceError, ProviderTimeoutError

ENDPOINTS = ("https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get",
             "https://1.push2his.eastmoney.com/api/qt/stock/fflow/daykline/get")
# f51=date; f52..56=main/small/medium/large/super-large net yuan;
# f57..61=the corresponding net ratios, already percentage points (not /100).
FIELDS = ("main_net", "small_net", "medium_net", "large_net", "super_large_net",
          "main_ratio", "small_ratio", "medium_ratio", "large_ratio", "super_large_ratio")


def parse_flows(payload, symbol: str) -> MoneyFlowSeries:
    if not isinstance(payload, dict) or payload.get("rc") != 0:
        raise ValueError("Invalid flow response")
    data = payload.get("data")
    # Null data is a source failure, not a confirmed successful empty result.
    if not isinstance(data, dict) or data.get("code") != symbol:
        raise ValueError("Missing or mismatched flow security")
    rows = data.get("klines")
    if not isinstance(rows, list) or len(rows) > 5000:
        raise ValueError("Invalid flow rows")
    parsed = {}
    for row in rows:
        if not isinstance(row, str) or len(row) > 1024:
            raise ValueError("Invalid flow row")
        cells = row.split(",")
        if len(cells) != 15 or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", cells[0]) is None:
            raise ValueError("Invalid flow fields")
        trading_date = date.fromisoformat(cells[0])
        values = {}
        for key, raw in zip(FIELDS, cells[1:11]):
            raw = raw.strip()
            value = None if raw in ("", "-", "null") else float(raw)
            if value is not None and (not isfinite(value) or (key.endswith("ratio") and abs(value) > 100)):
                raise ValueError("Invalid flow number")
            values[key] = value
        parsed[trading_date] = MoneyFlow(date=trading_date, **values)
    return MoneyFlowSeries(symbol=symbol, items=[parsed[d] for d in sorted(parsed)[-30:]])


class EastMoneyFlows:
    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    async def collect(self, symbol: str, secid: str) -> MoneyFlowSeries:
        error = None
        for endpoint in ENDPOINTS:
            try:
                response = await self.client.get(endpoint, params={"secid": secid, "lmt": 30, "klt": 101,
                    "fields1": "f1,f2,f3,f7", "fields2": ",".join(f"f{i}" for i in range(51, 66))})
                response.raise_for_status()
                return parse_flows(response.json(), symbol)
            except httpx.TimeoutException as exc:
                error = ProviderTimeoutError("EastMoney flow request timed out")
                error.__cause__ = exc
            except (httpx.HTTPError, ValueError, TypeError, KeyError, OverflowError) as exc:
                error = DataSourceError("EastMoney flow request failed")
                error.__cause__ = exc
        raise error

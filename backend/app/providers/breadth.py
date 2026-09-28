"""Direct counters from EastMoney's hotmap, not index constituents."""
import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import httpx
from app.models.breadth import ExchangeBreadth, LimitPoolCount, MarketBreadth
from app.providers.exceptions import DataSourceError, ProviderTimeoutError

BEIJING = timezone(timedelta(hours=8))
IDS = {(1, "000002"): "SH", (0, "399002"): "SZ", (0, "899050"): "BJ"}
ENDPOINTS = ("https://push2.eastmoney.com/api/qt/ulist/get", "https://push2delay.eastmoney.com/api/qt/ulist/get")


def count(value):
    if value in (None, "-", ""):
        return None
    if isinstance(value, bool):
        raise ValueError("Invalid count")
    result = Decimal(str(value))
    if not result.is_finite() or result != result.to_integral_value() or not 0 <= result <= 100000:
        raise ValueError("Invalid count")
    return int(result)


def parse_breadth(payload) -> MarketBreadth:
    if not isinstance(payload, dict) or payload.get("rc") != 0:
        raise ValueError("Invalid breadth response")
    data = payload.get("data")
    rows = data.get("diff") if isinstance(data, dict) else None
    if isinstance(rows, dict):
        rows = list(rows.values())
    if not isinstance(rows, list) or not rows or len(rows) > 100:
        raise ValueError("Missing breadth rows")
    parsed = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Invalid breadth row")
        exchange = IDS.get((row.get("f13"), row.get("f12")))
        if exchange is None:
            continue
        if exchange in parsed:
            raise ValueError("Duplicate market counters")
        try:
            values = [count(row.get(field)) for field in ("f104", "f105", "f106")]
            # The official hotmap also rejects all-zero market counters.
            if not any(v for v in values if v is not None):
                continue
            stamp = row.get("f124")
            if isinstance(stamp, bool) or not isinstance(stamp, (int, float)) or not 0 < stamp < 4102444800:
                raise ValueError("Invalid source timestamp")
            parsed[exchange] = ExchangeBreadth(exchange=exchange, advancing=values[0], declining=values[1],
                unchanged=values[2], as_of=datetime.fromtimestamp(stamp, BEIJING))
        except (ValueError, ArithmeticError):
            continue
    if not parsed:
        raise ValueError("No usable exchange breadth")
    return MarketBreadth(exchanges=[parsed.get(e, ExchangeBreadth(exchange=e)) for e in ("SH", "SZ", "BJ")])


def parse_pool(payload, trading_date) -> LimitPoolCount:
    if not isinstance(payload, dict) or payload.get("rc") != 0:
        raise ValueError("Invalid stock pool")
    data = payload.get("data")
    if not isinstance(data, dict) or str(data.get("qdate")) != trading_date.strftime("%Y%m%d"):
        raise ValueError("Mismatched pool date")
    total, rows = count(data.get("tc")), data.get("pool")
    if total is None or not isinstance(rows, list) or (total > 0 and len(rows) != 1) or (total == 0 and rows):
        raise ValueError("Missing pool witness")
    if rows and (not isinstance(rows[0], dict) or not isinstance(rows[0].get("c"), str) or len(rows[0]["c"]) != 6 or not rows[0]["c"].isascii() or not rows[0]["c"].isdigit()):
        raise ValueError("Invalid pool witness")
    return LimitPoolCount(count=total, date=trading_date)


class EastMoneyBreadth:
    def __init__(self, client):
        self.client = client

    async def collect(self):
        error = DataSourceError("Market breadth unavailable")
        for endpoint in ENDPOINTS:
            try:
                response = await self.client.get(endpoint, timeout=httpx.Timeout(6, connect=3), params={"secids": "1.000002,0.399002,0.899050",
                    "fields": "f12,f13,f104,f105,f106,f124", "fltt": 1, "invt": 2, "np": 1, "pn": 1,
                    "pz": 20, "dect": 1, "ut": "8dec03ba335b81bf4ebdf7b29ec27d15"})
                response.raise_for_status()
                result = parse_breadth(response.json())
                break
            except httpx.TimeoutException:
                error = ProviderTimeoutError("Market breadth request timed out")
            except (httpx.HTTPError, ValueError, TypeError, ArithmeticError):
                error = DataSourceError("Market breadth request failed")
        else:
            raise error
        if result.date:
            result.limit_up, result.limit_down = await asyncio.gather(
                self.pool("getTopicZTPool", "fbt:asc", result.date), self.pool("getTopicDTPool", "fund:asc", result.date))
        return result

    async def pool(self, endpoint, sort, trading_date):
        try:
            response = await self.client.get("https://push2ex.eastmoney.com/" + endpoint, timeout=httpx.Timeout(6, connect=3), params={
                "ut": "7eea3edcaed734bea9cbfc24409ed989", "dpt": "wz.ztzt", "Pageindex": 0,
                "pagesize": 1, "sort": sort, "date": trading_date.strftime("%Y%m%d")})
            response.raise_for_status()
            return parse_pool(response.json(), trading_date)
        except (httpx.HTTPError, ValueError, TypeError, ArithmeticError):
            return LimitPoolCount()

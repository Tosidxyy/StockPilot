"""Sina one-minute candles as an intraday series; never execute JSONP."""
import json
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from math import isfinite

import httpx

from app.models.market import IntradayPoint
from app.providers.exceptions import DataSourceError, InvalidSymbolError, ProviderTimeoutError
from app.providers.intraday import INDEX_CODES
from app.providers.quotes import quote_code


def parse_sina_minutes(text: str) -> list[IntradayPoint]:
    if len(text) > 2_000_000:
        raise ValueError("Oversized minute response")
    # The comment contains script-like text on the public endpoint. Treat the
    # entire wrapper as data and decode only its JSON array, without eval/JS.
    match = re.fullmatch(r"\s*(?:/\*.*?\*/\s*)?var _data=\((\[.*\]|null)\);\s*", text, re.S)
    if not match:
        raise ValueError("Invalid minute wrapper")
    rows = json.loads(match[1])
    if not isinstance(rows, list) or not rows or len(rows) > 1024:
        raise ValueError("Missing minute candles")
    parsed = {}
    dates = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Invalid minute candle")
        timestamp = datetime.strptime(row["day"], "%Y-%m-%d %H:%M:%S")
        dates.append(timestamp.date())
        minute = timestamp.strftime("%H%M")
        if timestamp.second or not ("0930" <= minute <= "1130" or "1300" <= minute <= "1500"):
            continue
        opening, high, low, closing = (float(row[key]) for key in ("open", "high", "low", "close"))
        shares = Decimal(str(row["volume"]))
        amount = Decimal(str(row["amount"])) if row.get("amount") not in (None, "", "-") else None
        if not all(isfinite(value) and value > 0 for value in (opening, high, low, closing)) or low > min(opening, closing) or high < max(opening, closing):
            raise ValueError("Invalid minute prices")
        if not shares.is_finite() or shares < 0 or shares != shares.to_integral_value() or not isfinite(float(shares)):
            raise ValueError("Invalid minute volume")
        if amount is not None and (not amount.is_finite() or amount < 0 or not isfinite(float(amount))):
            raise ValueError("Invalid minute amount")
        parsed[timestamp] = IntradayPoint(time=timestamp, price=closing, volume=float(shares / 100),
            turnover=float(amount) if amount is not None else None, source="sina")
    latest = max(dates)
    points = [point for timestamp, point in sorted(parsed.items()) if timestamp.date() == latest]
    if not points:
        raise ValueError("No regular-session minutes on latest date")
    return points


class SinaIntraday:
    def __init__(self, client: httpx.AsyncClient | None = None):
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(1.2, connect=.6),
            headers={"User-Agent":"Mozilla/5.0", "Referer":"https://finance.sina.com.cn/"})

    async def aclose(self):
        if self._owns_client:
            await self._client.aclose()

    async def get_stock_intraday(self, symbol: str):
        return await self._get(quote_code(symbol))

    async def get_index_intraday(self, index_code: str = "000001"):
        if index_code not in INDEX_CODES:
            raise InvalidSymbolError("Unsupported market index")
        return await self._get(INDEX_CODES[index_code])

    async def _get(self, code: str):
        try:
            response = await self._client.get(
                "https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_data=/CN_MarketDataService.getKLineData",
                params={"symbol":code,"scale":1,"ma":"no","datalen":300})
            response.raise_for_status()
            return parse_sina_minutes(response.text)
        except httpx.TimeoutException as error:
            raise ProviderTimeoutError("Sina minute request timed out") from error
        except (httpx.HTTPError, ValueError, TypeError, KeyError, OverflowError, InvalidOperation) as error:
            raise DataSourceError("Sina minute request failed") from error

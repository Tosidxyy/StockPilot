"""Tencent's public minute series, normalized to regular A-share sessions."""

from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from math import isfinite

import httpx

from app.models.market import IntradayPoint
from app.providers.eastmoney import to_secid
from app.providers.exceptions import DataSourceError, InvalidSymbolError, ProviderTimeoutError

INDEX_CODES = {"000001": "sh000001", "399001": "sz399001", "399006": "sz399006"}


def parse_minutes(payload: dict, code: str) -> list[IntradayPoint]:
    if not isinstance(payload, dict) or payload.get("code") != 0:
        raise ValueError("Invalid minute response")
    series = payload["data"][code]["data"]
    raw_date = series["date"]
    if not isinstance(raw_date, str) or len(raw_date) != 8 or not raw_date.isascii() or not raw_date.isdigit():
        raise ValueError("Invalid minute date")
    trading_date = datetime.strptime(raw_date, "%Y%m%d").date()
    rows = series["data"]
    if not isinstance(rows, list) or not rows or len(rows) > 5000:
        raise ValueError("Missing/oversized minute series")
    parsed = {}
    for row in rows:
        if not isinstance(row, str) or len(row) > 256:
            raise ValueError("Invalid minute row")
        fields = row.split()
        if len(fields) != 4 or len(fields[0]) != 4 or not fields[0].isascii() or not fields[0].isdigit():
            raise ValueError("Invalid minute fields")
        timestamp = datetime.strptime(series["date"] + fields[0], "%Y%m%d%H%M")
        price = float(fields[1])
        volume, amount = Decimal(fields[2]), Decimal(fields[3])
        if not isfinite(price) or price <= 0 or not volume.is_finite() or not amount.is_finite() or not isfinite(float(amount)) or not isfinite(float(volume)):
            raise ValueError("Invalid minute numbers")
        if volume < 0 or amount < 0 or volume != volume.to_integral_value():
            raise ValueError("Invalid cumulative counters")
        # The source includes post-close trades for stocks, extending to 15:30.
        # Keep the same regular-session range as the existing EastMoney chart.
        minute = timestamp.strftime("%H%M")
        if not ("0930" <= minute <= "1130" or "1300" <= minute <= "1500"):
            continue
        # A repeated minute can be a source correction; use its last actual row.
        parsed[timestamp] = (price, volume, amount)
    points, previous_volume, previous_amount = [], Decimal(0), Decimal(0)
    previous_time = None
    for timestamp, (price, volume, amount) in sorted(parsed.items()):
        if volume < previous_volume or amount < previous_amount:
            raise ValueError("Cumulative minute counters decreased")
        adjacent = (timestamp.strftime("%H%M") == "0930" if previous_time is None else
            timestamp - previous_time == timedelta(minutes=1) or
            previous_time.strftime("%H%M") == "1130" and timestamp.strftime("%H%M") == "1300")
        points.append(IntradayPoint(time=timestamp, price=price, volume=int(volume - previous_volume) if adjacent else None,
            turnover=float(amount - previous_amount) if adjacent else None, source="tencent"))
        previous_volume, previous_amount = volume, amount
        previous_time = timestamp
    if not points or points[0].time.date() != trading_date:
        raise ValueError("No regular-session minute points")
    return points


class TencentIntraday:
    def __init__(self, client: httpx.AsyncClient | None = None):
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(3, connect=1.5),
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"})

    async def aclose(self):
        if self._owns_client:
            await self._client.aclose()

    async def get_stock_intraday(self, symbol: str):
        to_secid(symbol)
        market = "sh" if symbol.startswith("6") else "bj" if symbol.startswith(("4", "8")) else "sz"
        return await self._get(market + symbol)

    async def get_index_intraday(self, index_code: str = "000001"):
        if index_code not in INDEX_CODES:
            raise InvalidSymbolError("Unsupported market index")
        return await self._get(INDEX_CODES[index_code])

    async def _get(self, code):
        try:
            response = await self._client.get("https://web.ifzq.gtimg.cn/appstock/app/minute/query", params={"code": code})
            response.raise_for_status()
            return parse_minutes(response.json(), code)
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError("Tencent minute request timed out") from exc
        except (httpx.HTTPError, ValueError, KeyError, TypeError, OverflowError, InvalidOperation) as exc:
            raise DataSourceError("Tencent minute request failed") from exc

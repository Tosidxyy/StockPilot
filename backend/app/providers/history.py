"""Unadjusted historical candles from Tencent's public chart endpoint."""

from datetime import date
from math import isfinite
from typing import Literal

import httpx

from app.models.market import KlineItem
from app.providers.eastmoney import to_secid
from app.providers.exceptions import DataSourceError, ProviderTimeoutError


class TencentHistory:
    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(5, connect=1.5),
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"},
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def get_kline(
        self, symbol: str, period: Literal["daily", "weekly"] = "daily", limit: int = 120,
    ) -> list[KlineItem]:
        to_secid(symbol)
        if period not in ("daily", "weekly") or not 1 <= limit <= 1000:
            raise ValueError("Invalid historical candle period or limit")
        market = "sh" if symbol.startswith("6") else "bj" if symbol.startswith(("4", "8")) else "sz"
        code = market + symbol
        interval = "day" if period == "daily" else "week"
        # This endpoint is unadjusted; do not mix qfq/hfq series into fqt=0 data.
        try:
            response = await self._client.get(
                "https://web.ifzq.gtimg.cn/appstock/app/kline/kline",
                params={"param": f"{code},{interval},,,{limit}"},
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or payload.get("code") != 0:
                raise ValueError("Invalid chart response")
            rows = payload["data"][code][interval]
            if not isinstance(rows, list) or not rows:
                raise ValueError("No historical candles")
            candles = []
            for row in rows:
                if not isinstance(row, list) or len(row) < 6:
                    raise ValueError("Invalid historical candle")
                opening, closing, high, low, volume = (float(value) for value in row[1:6])
                if not all(isfinite(value) for value in (opening, closing, high, low, volume)):
                    raise ValueError("Non-finite historical candle")
                if low <= 0 or low > min(opening, closing) or high < max(opening, closing) or volume < 0 or not volume.is_integer():
                    raise ValueError("Invalid historical candle values")
                candles.append(KlineItem(
                    date=date.fromisoformat(row[0]), open=opening, close=closing,
                    high=high, low=low, volume=int(volume), turnover=None, source="tencent",
                ))
            return sorted(candles, key=lambda item: item.date)[-limit:]
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError("Tencent history request timed out") from exc
        except (httpx.HTTPError, ValueError, TypeError, KeyError, OverflowError) as exc:
            raise DataSourceError("Tencent history request failed") from exc

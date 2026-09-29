"""Batched public quotes; normalize hand/share units and preserve source time."""
import re
from datetime import datetime, timedelta, timezone
from math import isfinite
from typing import Literal, Sequence

import httpx

from app.models.market import StockQuote
from app.providers.eastmoney import to_secid
from app.providers.exceptions import DataSourceError, ProviderTimeoutError


def quote_code(symbol: str) -> str:
    to_secid(symbol)
    return ("sh" if symbol.startswith("6") else "bj" if symbol.startswith(("4", "8")) else "sz") + symbol


def number(value: str) -> float | None:
    if value in ("", "-", "--"):
        return None
    result = float(value)
    if not isfinite(result):
        raise ValueError("Non-finite quote")
    return result


class PublicQuotes:
    def __init__(self, source: Literal["tencent", "sina"], client: httpx.AsyncClient | None = None):
        self.source = source
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(1.2, connect=.6),
            headers={"User-Agent":"Mozilla/5.0", "Referer": "https://finance.sina.com.cn/" if source == "sina" else "https://gu.qq.com/"})

    async def aclose(self):
        if self._owns_client:
            await self._client.aclose()

    async def get_quotes(self, symbols: Sequence[str]) -> list[StockQuote]:
        codes = {quote_code(symbol): symbol for symbol in dict.fromkeys(symbols)}
        if not codes:
            return []
        if len(codes) > 50:
            raise ValueError("At most 50 symbols per batch")
        url = "https://qt.gtimg.cn/" if self.source == "tencent" else "https://hq.sinajs.cn/"
        try:
            # Sina treats percent-encoded commas as part of one symbol. Codes have
            # been validated above, so preserve the literal batch delimiter.
            response = await self._client.get(url + ("?q=" if self.source == "tencent" else "?list=") + ",".join(codes))
            response.raise_for_status()
            text = response.content.decode("gb18030")
            rows = {}
            for code, body in re.findall(r'(?:v_|hq_str_)((?:sh|sz|bj)\d{6})="([^"\r\n]*)";', text):
                if code not in codes or not body:
                    continue
                try:
                    quote = self._parse(codes[code], body)
                    rows[quote.symbol] = quote
                except (ValueError, IndexError):
                    # One malformed/missing symbol must not discard the successful batch.
                    continue
            if not rows:
                raise ValueError("No usable public quotes")
            return [rows[symbol] for symbol in codes.values() if symbol in rows]
        except httpx.TimeoutException as error:
            raise ProviderTimeoutError("Public quote source timed out") from error
        except (httpx.HTTPError, UnicodeError, ValueError) as error:
            raise DataSourceError("Public quote source unavailable") from error

    def _parse(self, symbol: str, body: str) -> StockQuote:
        fields = body.split("~" if self.source == "tencent" else ",")
        if self.source == "tencent":
            if len(fields) < 40 or fields[2] != symbol:
                raise ValueError("Invalid quote identity")
            name = fields[1]
            price, previous, opening, high, low = (number(fields[i]) for i in (3,4,5,33,34))
            volume = number(fields[6])
            # Tencent's detailed cumulative amount is in ten-thousand yuan.
            turnover = number(fields[57] if len(fields) > 57 and fields[57] else fields[37])
            turnover = turnover * 10000 if turnover is not None else None
            change, percent, rate, pe = (number(fields[i]) for i in (31,32,38,39))
            stamp = datetime.strptime(fields[30], "%Y%m%d%H%M%S")
        else:
            if len(fields) < 32:
                raise ValueError("Invalid quote fields")
            name = fields[0]
            opening, previous, price, high, low = (number(fields[i]) for i in (1,2,3,4,5))
            shares = number(fields[8])
            volume = shares / 100 if shares is not None else None
            turnover = number(fields[9])
            change = round(price - previous, 4) if price is not None and previous else None
            percent = round(change / previous * 100, 4) if change is not None else None
            rate = pe = None  # This source does not provide these fields.
            stamp = datetime.strptime(fields[30] + " " + fields[31], "%Y-%m-%d %H:%M:%S")
        if not name or any(value is not None and value < 0 for value in (price,previous,opening,high,low,volume,turnover)):
            raise ValueError("Invalid quote values")
        # A zero current price is commonly a suspended/unavailable quote, not a tradable price.
        price = price or None
        if price is None:
            change = percent = None
        return StockQuote(symbol=symbol,name=name,price=price,previous_close=previous,
            open=opening,high=high,low=low,volume=int(volume) if volume is not None else None,
            turnover=turnover,change_amount=change,change_percent=percent,turnover_rate=rate,
            pe_ratio=pe,source=self.source,as_of=stamp.replace(tzinfo=timezone(timedelta(hours=8))))

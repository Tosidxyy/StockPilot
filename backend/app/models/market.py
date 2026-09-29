"""Provider-independent P0 market data models."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel


class StockQuote(BaseModel):
    symbol: str
    name: str
    price: float | None
    change_percent: float | None
    change_amount: float | None
    volume: int | None
    turnover: float | None
    high: float | None
    low: float | None
    open: float | None
    previous_close: float | None
    turnover_rate: float | None
    pe_ratio: float | None
    source: Literal["eastmoney", "tencent", "sina"] = "eastmoney"
    as_of: datetime | None = None


class MarketIndex(BaseModel):
    symbol: str
    name: str
    value: float | None
    change_percent: float | None
    change_amount: float | None
    volume: int | None
    turnover: float | None
    high: float | None = None
    low: float | None = None


class IntradayPoint(BaseModel):
    time: datetime
    price: float
    volume: float | None
    turnover: float | None
    source: Literal["eastmoney", "tencent", "sina"] = "eastmoney"


class KlineItem(BaseModel):
    date: date
    open: float
    close: float
    high: float
    low: float
    volume: int
    turnover: float | None
    source: Literal["eastmoney", "tencent"] = "eastmoney"


class SymbolSearchResult(BaseModel):
    symbol: str
    name: str
    market: str

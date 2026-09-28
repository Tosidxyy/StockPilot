"""The provider contract consumed by future services."""

from abc import ABC, abstractmethod
from typing import Literal, Sequence

from app.models.market import IntradayPoint, KlineItem, MarketIndex, StockQuote, SymbolSearchResult
from app.providers.exceptions import DataSourceError


class MarketDataProvider(ABC):
    @abstractmethod
    async def search_stocks(self, query: str, limit: int = 20) -> list[SymbolSearchResult]: ...

    @abstractmethod
    async def get_quotes(self, symbols: Sequence[str]) -> list[StockQuote]: ...

    @abstractmethod
    async def get_indices(self) -> list[MarketIndex]: ...

    @abstractmethod
    async def get_index_intraday(self, index_code: str = "000001") -> list[IntradayPoint]: ...

    async def get_stock_intraday(self, symbol: str) -> list[IntradayPoint]:
        raise DataSourceError("Stock intraday data is not supported by this provider")

    @abstractmethod
    async def get_kline(
        self, symbol: str, period: Literal["daily", "weekly"] = "daily", limit: int = 120
    ) -> list[KlineItem]: ...

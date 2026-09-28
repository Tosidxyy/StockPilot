"""The provider contract consumed by future services."""

from abc import ABC, abstractmethod
from typing import Literal, Sequence

from app.models.market import IntradayPoint, KlineItem, MarketIndex, StockQuote, SymbolSearchResult
from app.providers.exceptions import DataSourceError
from app.models.news import NewsBatch
from app.models.announcements import AnnouncementBatch, AnnouncementItem
from app.models.money_flow import MoneyFlowSeries
from app.models.breadth import MarketBreadth


class MarketDataProvider(ABC):
    async def get_market_breadth(self) -> MarketBreadth:
        raise DataSourceError("Market breadth is not supported by this provider")

    async def get_stock_money_flow(self, symbol: str) -> MoneyFlowSeries:
        raise DataSourceError("Money flow is not supported by this provider")

    async def get_stock_announcements(self, symbol: str) -> AnnouncementBatch:
        raise DataSourceError("Announcements are not supported by this provider")

    async def get_announcement_content(self, item: AnnouncementItem, previous: AnnouncementItem | None = None) -> AnnouncementItem:
        raise DataSourceError("Announcement text is not supported by this provider")

    async def get_stock_news(self, symbol: str) -> NewsBatch:
        raise DataSourceError("Stock news is not supported by this provider")

    async def get_market_news(self) -> NewsBatch:
        raise DataSourceError("Market news is not supported by this provider")

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

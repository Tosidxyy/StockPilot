"""Exchange breadth and stock-pool counts with separate coverage."""
from datetime import date as TradingDate, datetime
from typing import Annotated, Literal
from pydantic import BaseModel, Field, computed_field

Count = Annotated[int, Field(strict=True, ge=0, le=100000)]


class ExchangeBreadth(BaseModel):
    exchange: Literal["SH", "SZ", "BJ"]
    advancing: Count | None = None
    declining: Count | None = None
    unchanged: Count | None = None
    as_of: datetime | None = None
    stale: bool = False

    @computed_field
    @property
    def complete(self) -> bool:
        return self.as_of is not None and not self.stale and all(v is not None for v in (self.advancing, self.declining, self.unchanged))


class LimitPoolCount(BaseModel):
    count: Count | None = None
    date: TradingDate | None = None
    stale: bool = False
    scope: str = "东方财富对应股池口径，未保证覆盖与沪深京涨跌统计一致；不是固定涨跌幅阈值计数。"


class MarketBreadth(BaseModel):
    source: Literal["eastmoney"] = "eastmoney"
    scope: str = "沪深京 A 股：东方财富大盘星图使用的三市场计数；不含 B 股、基金和新三板。"
    definition: str = "按来源上涨/下跌/平盘分类；停牌分类沿用来源。三市场快照可能不同步，来源时间不是缓存时间。"
    exchanges: list[ExchangeBreadth] = Field(min_length=3, max_length=3)
    limit_up: LimitPoolCount = Field(default_factory=LimitPoolCount)
    limit_down: LimitPoolCount = Field(default_factory=LimitPoolCount)

    @computed_field
    @property
    def counts_complete(self) -> bool:
        return {e.exchange for e in self.exchanges} == {"SH", "SZ", "BJ"} and all(e.complete for e in self.exchanges) and len({e.as_of.date() for e in self.exchanges}) == 1

    @computed_field
    @property
    def date(self) -> TradingDate | None:
        return self.exchanges[0].as_of.date() if self.counts_complete else None

    @computed_field
    @property
    def advancing(self) -> int | None:
        return sum(e.advancing for e in self.exchanges) if self.counts_complete else None

    @computed_field
    @property
    def declining(self) -> int | None:
        return sum(e.declining for e in self.exchanges) if self.counts_complete else None

    @computed_field
    @property
    def unchanged(self) -> int | None:
        return sum(e.unchanged for e in self.exchanges) if self.counts_complete else None

    @computed_field
    @property
    def partial(self) -> bool:
        return not self.counts_complete or any(p.count is None or p.stale or p.date != self.date for p in (self.limit_up, self.limit_down))

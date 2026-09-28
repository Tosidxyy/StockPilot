"""Source-reported daily net flows; amounts are yuan and ratios percentage points."""

from datetime import date
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class MoneyFlow(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    date: date
    main_net: float | None = None
    super_large_net: float | None = None
    large_net: float | None = None
    medium_net: float | None = None
    small_net: float | None = None
    main_ratio: float | None = None
    super_large_ratio: float | None = None
    large_ratio: float | None = None
    medium_ratio: float | None = None
    small_ratio: float | None = None


class MoneyFlowSeries(BaseModel):
    symbol: str
    source: Literal["eastmoney"] = "eastmoney"
    amount_unit: Literal["CNY"] = "CNY"
    ratio_unit: Literal["percent"] = "percent"
    definition: str = "东方财富按订单大小分类的净流入统计；主力为超大单与大单，净占比为净流入占成交额的百分比。并非机构账户真实持仓或新增资金。"
    coverage: str = "源头最近最多30个交易日，最新统计日可能仍在更新；不保证包含今天。缺失字段为空，不代表零。"
    items: list[MoneyFlow] = Field(default_factory=list, max_length=30)

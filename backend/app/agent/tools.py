"""Agent tools delegate only to existing services."""

import re
from dataclasses import dataclass, field
from typing import Literal

from starlette.concurrency import run_in_threadpool

from app.services.market import MarketService
from app.services.stock import StockService
from app.services.watchlist import WatchlistService
from app.agent.trace import ToolStep


@dataclass(frozen=True)
class AgentDependencies:
    stocks: StockService
    market: MarketService
    watchlist: WatchlistService
    trace_steps: list[ToolStep] = field(default_factory=list)


def _symbol(value: str) -> str:
    if re.fullmatch(r"[03468][0-9]{5}", value) is None:
        raise ValueError("股票代码必须是六位 A 股代码")
    return value


async def get_stock_quote(deps: AgentDependencies, symbol: str) -> dict:
    """Get the current quote for a six-digit A-share code."""
    symbol = _symbol(symbol)
    result = await deps.stocks.get_quote(symbol)
    return {
        "symbol": symbol,
        "found": result.data is not None,
        "stale": result.stale,
        "cached_at": result.cached_at.isoformat() if result.cached_at else None,
        "quote": result.data.model_dump(mode="json") if result.data else None,
    }


async def get_stock_kline(
    deps: AgentDependencies,
    symbol: str,
    period: Literal["daily", "weekly"] = "daily",
    limit: int = 5,
) -> dict:
    """Get recent daily or weekly K-lines and volume for an A-share code."""
    symbol = _symbol(symbol)
    if not 1 <= limit <= 120:
        raise ValueError("limit 必须在 1 到 120 之间")
    result = await deps.stocks.get_kline(symbol, period, limit)
    return {
        "symbol": symbol,
        "period": period,
        "stale": result.stale,
        "cached_at": result.cached_at.isoformat() if result.cached_at else None,
        "klines": [item.model_dump(mode="json") for item in result.data],
    }


async def get_market_indices(deps: AgentDependencies) -> dict:
    """Get the latest available Shanghai, Shenzhen, and ChiNext indices."""
    result = await deps.market.get_indices()
    return {
        "stale": result.stale,
        "cached_at": result.cached_at.isoformat() if result.cached_at else None,
        "indices": [item.model_dump(mode="json") for item in result.data],
    }


async def get_watchlist(deps: AgentDependencies) -> dict:
    """Get the user's watchlist and its quotes in one batched data request."""
    entries = await run_in_threadpool(deps.watchlist.list_entries)
    symbols = [entry.symbol for entry in entries]
    if not symbols:
        return {"entries": [], "quotes": [], "stale": False}
    result = await deps.stocks.get_quotes(symbols)
    return {
        "entries": [entry.model_dump(mode="json") for entry in entries],
        "quotes": [item.model_dump(mode="json") for item in result.data],
        "stale": result.stale,
        "cached_at": result.cached_at.isoformat() if result.cached_at else None,
    }

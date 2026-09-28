"""Agent tools delegate only to existing services."""

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

from starlette.concurrency import run_in_threadpool

from app.services.market import MarketService
from app.services.stock import StockService
from app.services.watchlist import WatchlistService
from app.services.cache import CachedResult
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


def _cache_metadata(result: CachedResult) -> dict:
    return {
        "stale": result.stale,
        "cached_at": result.cached_at.isoformat() if result.cached_at else None,
        "cache_age_seconds": max(0, round((datetime.now(timezone.utc) - result.cached_at).total_seconds(), 1))
        if result.cached_at else None,
    }


async def get_stock_quote(deps: AgentDependencies, symbol: str) -> dict:
    """Read the latest cached quote; fetch only if no usable cache exists."""
    symbol = _symbol(symbol)
    result = await deps.stocks.get_quote(symbol, prefer_cached=True)
    return {
        "symbol": symbol,
        "found": result.data is not None,
        **_cache_metadata(result),
        "quote": result.data.model_dump(mode="json") if result.data else None,
    }


async def get_stock_kline(
    deps: AgentDependencies,
    symbol: str,
    period: Literal["daily", "weekly"] = "daily",
    limit: int = 5,
) -> dict:
    """Read cached daily/weekly K-lines; fetch only if no covering cache exists."""
    symbol = _symbol(symbol)
    if not 1 <= limit <= 120:
        raise ValueError("limit 必须在 1 到 120 之间")
    result = await deps.stocks.get_kline(symbol, period, limit, prefer_cached=True)
    return {
        "symbol": symbol,
        "period": period,
        **_cache_metadata(result),
        "klines": [item.model_dump(mode="json") for item in result.data],
    }


async def get_market_indices(deps: AgentDependencies) -> dict:
    """Read the latest cached major indices; fetch only on cache miss."""
    result = await deps.market.get_indices(prefer_cached=True)
    return {
        **_cache_metadata(result),
        "indices": [item.model_dump(mode="json") for item in result.data],
    }


async def get_watchlist(deps: AgentDependencies) -> dict:
    """Read saved symbols with cached quotes; fetch a batch only on cache miss."""
    entries = await run_in_threadpool(deps.watchlist.list_entries)
    symbols = [entry.symbol for entry in entries]
    if not symbols:
        return {"entries": [], "quotes": [], "stale": False, "cached_at": None, "cache_age_seconds": None}
    result = await deps.stocks.get_quotes(symbols, prefer_cached=True)
    return {
        "entries": [entry.model_dump(mode="json") for entry in entries],
        "quotes": [item.model_dump(mode="json") for item in result.data],
        **_cache_metadata(result),
    }

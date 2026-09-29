"""Agent tools delegate only to existing services."""

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Literal
from collections.abc import Awaitable, Callable

from starlette.concurrency import run_in_threadpool

from app.services.market import MarketService
from app.services.stock import StockService
from app.services.watchlist import WatchlistService
from app.services.cache import CachedResult
from app.services.reads import StockReadService
from app.services.news import NewsService
from app.services.announcements import AnnouncementService
from app.services.money_flow import MoneyFlowService
from app.services.documents import DocumentService
from app.providers.exceptions import DataSourceError
from app.agent.trace import ToolStep


@dataclass(frozen=True)
class AgentDependencies:
    stocks: StockService
    market: MarketService
    watchlist: WatchlistService
    trace_steps: list[ToolStep] = field(default_factory=list)
    reader: StockReadService | None = None
    news: NewsService | None = None
    announcements: AnnouncementService | None = None
    money_flow: MoneyFlowService | None = None
    documents: DocumentService | None = None
    document_searches: list[dict] = field(default_factory=list)
    tool_results: list[dict] = field(default_factory=list)
    progress: Callable[[str], Awaitable[None]] | None = None


def _symbol(value: str) -> str:
    if re.fullmatch(r"[03468][0-9]{5}", value) is None:
        raise ValueError("股票代码必须是六位 A 股代码")
    return value


def _cache_metadata(result: CachedResult) -> dict:
    return {
        **({"collection_state": result.state} if getattr(result, "state", None) else {}),
        "stale": result.stale,
        "cached_at": result.cached_at.isoformat() if result.cached_at else None,
        "cache_age_seconds": max(0, round((datetime.now(timezone.utc) - result.cached_at).total_seconds(), 1))
        if result.cached_at else None,
    }


async def get_stock_quote(deps: AgentDependencies, symbol: str) -> dict:
    """Read the latest cached quote; fetch only if no usable cache exists."""
    symbol = _symbol(symbol)
    result = await deps.reader.quote(symbol, prefer_cached=True) if deps.reader else await deps.stocks.get_quote(symbol, prefer_cached=True)
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
    result = await deps.reader.kline(symbol, period, limit, prefer_cached=True) if deps.reader else await deps.stocks.get_kline(symbol, period, limit, prefer_cached=True)
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


async def get_market_breadth(deps: AgentDependencies) -> dict:
    result = await deps.market.get_breadth(prefer_cached=True)
    return {**_cache_metadata(result), **result.data.model_dump(mode="json")}


async def search_stock_documents(deps: AgentDependencies, symbol: str, query: str,
                                 kind: Literal["all", "news", "announcement"] = "all",
                                 start: str | None = None, end: str | None = None, limit: int = 6) -> dict:
    """Search collected evidence only; never fetch the source or extend its cache age."""
    from datetime import date
    symbol = _symbol(symbol)
    if deps.documents is None:
        raise DataSourceError("Document service unavailable")
    result = await deps.documents.search(symbol, query, kind=kind,
        start=date.fromisoformat(start) if start else None, end=date.fromisoformat(end) if end else None, limit=limit)
    deps.document_searches.append(result)
    return result


async def get_watchlist(deps: AgentDependencies) -> dict:
    """Read saved symbols with cached quotes; fetch a batch only on cache miss."""
    entries = await run_in_threadpool(deps.watchlist.list_entries)
    symbols = [entry.symbol for entry in entries]
    if not symbols:
        return {"entries": [], "quotes": [], "stale": False, "cached_at": None, "cache_age_seconds": None}
    result = await deps.reader.quotes(symbols, prefer_cached=True) if deps.reader else await deps.stocks.get_quotes(symbols, prefer_cached=True)
    return {
        "entries": [entry.model_dump(mode="json") for entry in entries],
        "quotes": [item.model_dump(mode="json") for item in result.data],
        **_cache_metadata(result),
    }


async def get_stock_news(deps: AgentDependencies, symbol: str, days: int = 7, limit: int = 10) -> dict:
    symbol = _symbol(symbol)
    if not 1 <= days <= 30 or not 1 <= limit <= 50:
        raise ValueError("days 需为 1–30，limit 需为 1–50")
    if deps.news is None:
        raise DataSourceError("News service unavailable")
    start = (datetime.now(timezone(timedelta(hours=8))) - timedelta(days=days - 1)).date()
    result = await deps.news.get(symbol, start=start, page_size=limit, prefer_cached=True)
    output = {"symbol": symbol, "days": days, **_cache_metadata(result), **result.data.model_dump(mode="json")}
    return await _attach_recent(deps,output,symbol,"news",start,min(limit,10))


async def get_stock_money_flow(deps: AgentDependencies, symbol: str, limit: int = 5) -> dict:
    """Read cached daily flows; limit is trading-day rows, not calendar days."""
    symbol = _symbol(symbol)
    if not 1 <= limit <= 30:
        raise ValueError("limit 必须在1到30个交易日之间")
    if deps.money_flow is None:
        raise DataSourceError("Money flow service unavailable")
    result = await deps.money_flow.get(symbol, limit, prefer_cached=True)
    return {**_cache_metadata(result), **result.data.model_dump(mode="json")}


async def get_market_news(deps: AgentDependencies, days: int = 7, limit: int = 10) -> dict:
    if not 1 <= days <= 30 or not 1 <= limit <= 50:
        raise ValueError("days 需为 1–30，limit 需为 1–50")
    if deps.news is None:
        raise DataSourceError("News service unavailable")
    start = (datetime.now(timezone(timedelta(hours=8))) - timedelta(days=days - 1)).date()
    result = await deps.news.get(start=start, page_size=limit, prefer_cached=True)
    output = {"days": days, **_cache_metadata(result), **result.data.model_dump(mode="json")}
    return await _attach_recent(deps,output,"market","news",start,min(limit,10))


async def get_stock_announcements(deps: AgentDependencies, symbol: str, days: int = 30,
                                  limit: int = 10, document_id: str | None = None) -> dict:
    symbol = _symbol(symbol)
    if not 1 <= days <= 90 or not 1 <= limit <= 50:
        raise ValueError("days 需为1–90，limit需为1–50")
    if deps.announcements is None:
        raise DataSourceError("Announcement service unavailable")
    start = (datetime.now(timezone(timedelta(hours=8))) - timedelta(days=days - 1)).date()
    result = await deps.announcements.get(symbol, start=start, page_size=limit, prefer_cached=True)
    output = {"symbol": symbol, "days": days, **_cache_metadata(result), **result.data.model_dump(mode="json")}
    if document_id:
        if re.fullmatch(r"AN[0-9]{18}", document_id) is None:
            raise ValueError("公告ID无效")
        item = await deps.announcements.document(symbol, document_id)
        if item and item.notice_date < start:
            item = None
        document = item.model_dump(mode="json") if item else None
        if document:
            text = document.get("text") or ""
            document["text"] = text[:8000] or None
            document["tool_text_truncated"] = len(text) > 8000
        output["document"] = document
    return await _attach_recent(deps,output,symbol,"announcement",start,10 if document_id else min(limit,10),document_id)


async def _attach_recent(deps, output, symbol, kind, start, limit, document_id=None):
    documents = getattr(deps,"documents",None)
    if documents is not None:
        result = await documents.recent(symbol,kind=kind,start=start,limit=limit,document_id=document_id,
            document_ids=None if document_id else [i["id"] for i in output.get("items",[])])
        deps.document_searches.append(result)
        output["evidence"] = result["evidence"]
        output["source_states"] = result["source_states"]
        output["evidence_coverage"] = result["coverage"]
        output["evidence_truncated"] = result["index_truncated"] or result["candidates_truncated"] or (
            not document_id and len({e["document_id"] for e in result["evidence"]}) < len(output.get("items",[])))
    return output


async def get_watchlist_insights(deps: AgentDependencies, days: int = 3, stock_limit: int = 5) -> dict:
    """Bounded cache-only news/announcement overview; no quote/source fan-out."""
    if not 1 <= days <= 30 or not 1 <= stock_limit <= 10:
        raise ValueError("days需1–30，stock_limit需1–10")
    if deps.documents is None:
        raise DataSourceError("Document service unavailable")
    entries = await run_in_threadpool(deps.watchlist.list_entries)
    start = (datetime.now(timezone(timedelta(hours=8))) - timedelta(days=days-1)).date()
    stocks, evidence = [], []
    for entry in entries[:stock_limit]:
        categories = {}
        for kind in ("news","announcement"):
            result = await deps.documents.recent(entry.symbol,kind=kind,start=start,limit=1)
            deps.document_searches.append(result)
            evidence.extend(result["evidence"])
            categories[kind] = result
        stocks.append({"symbol":entry.symbol,**categories})
    return {"days":days,"stock_limit":stock_limit,"watchlist_total":len(entries),"stocks":stocks,"evidence":evidence,
        "remaining_count":max(0,len(entries)-stock_limit),"partial":len(entries)>stock_limit or any(
            not c["evidence"] or any(s["state"] != "ready" for s in c["source_states"].values()) for row in stocks
            for c in (row["news"],row["announcement"])),
        "coverage":"仅自选名单前N只，每股每类最多一个已有片段；不请求资讯源、不读取报价，不保证该时段消息完整。"}

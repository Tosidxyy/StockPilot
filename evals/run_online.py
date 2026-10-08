"""Bounded real-source acceptance; isolated storage, no model or user DB writes."""

import argparse
import asyncio
import hashlib
import json
import logging
import sqlite3
import sys
import tempfile
from collections import defaultdict
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import httpx
from pydantic_core import to_jsonable_python
from sqlalchemy import select

from app.database.models import MarketSnapshot, NewsFetchState, AnnouncementFetchState
from app.agent.tools import AgentDependencies, get_market_indices, get_stock_kline, get_stock_quote
from app.main import create_app
from app.providers.exceptions import DataSourceError
from app.providers.resilient import ResilientMarketProvider
from app.services.collector import BEIJING, is_market_session


def stamp():
    return datetime.now(timezone.utc).isoformat()


def tracked_provider(events, started):
    provider = ResilientMarketProvider()
    for name in (
        "get_quotes", "get_stock_intraday", "get_index_intraday", "get_indices", "get_kline",
        "get_stock_news", "get_stock_announcements", "get_stock_money_flow", "get_stock_comments",
        "get_market_breadth",
    ):
        original = getattr(provider, name)

        async def tracked(*args, _method=original, _name=name, **kwargs):
            begin = monotonic()
            event = {"method": _name, "args": list(args), "started_seconds": round(begin - started, 3)}
            try:
                rows = await _method(*args, **kwargs)
                event["success"] = True
                items = rows if isinstance(rows, list) else getattr(rows, "items", None)
                if isinstance(items, list):
                    event["count"] = len(items)
                    if items:
                        last = items[-1]
                        event["source"] = getattr(last, "source", None)
                        for field in ("as_of", "time", "date"):
                            value = getattr(last, field, None)
                            if value is not None:
                                event["source_time"] = value.isoformat()
                                break
                    if _name in ("get_quotes", "get_indices"):
                        event["quotes"] = [{"symbol": item.symbol, "source": item.source,
                                            "as_of": item.as_of.isoformat() if item.as_of else None,
                                            "price": getattr(item, "price", getattr(item, "value", None))}
                                           for item in items]
                return rows
            except asyncio.CancelledError:
                event["cancelled"] = True
                raise
            except Exception as error:
                event.update(success=False, error_type=type(error).__name__)
                raise
            finally:
                event["finished_seconds"] = round(monotonic() - started, 3)
                event["duration_seconds"] = round(monotonic() - begin, 3)
                events.append(event)

        setattr(provider, name, tracked)
    return provider


def summarize(events):
    groups = defaultdict(list)
    for event in events:
        key = event["method"] + ":" + json.dumps(event["args"], ensure_ascii=False)
        groups[key].append(event)
    result = {}
    for key, rows in groups.items():
        times = sorted(row["finished_seconds"] for row in rows if row.get("success") and row.get("count", 1))
        gaps = [b - a for a, b in zip(times, times[1:])]
        ordered = sorted(gaps)
        result[key] = {"attempts": len(rows), "successes": len(times),
                       "failures": sum(row.get("success") is False for row in rows),
                       "median_success_gap_seconds": round(ordered[len(ordered) // 2], 3) if ordered else None,
                       "max_success_gap_seconds": round(max(gaps), 3) if gaps else None,
                       "last_source_time": next((row.get("source_time") for row in reversed(rows)
                                                 if row.get("source_time")), None)}
    return result


def snapshots(sessions):
    with sessions() as session:
        return {
            "market": [{"namespace": row.namespace, "key": json.loads(row.cache_key),
                        "saved_at": row.saved_at.isoformat()} for row in session.scalars(select(MarketSnapshot))],
            "news": [{"scope": row.scope, "failed": row.failed,
                      "saved_at": row.fetched_at.isoformat() if row.fetched_at else None}
                     for row in session.scalars(select(NewsFetchState))],
            "announcements": [{"scope": row.scope, "failed": row.failed,
                               "saved_at": row.fetched_at.isoformat() if row.fetched_at else None}
                              for row in session.scalars(select(AnnouncementFetchState))],
        }


def copy_shutdown_database(source_path, target_path):
    # sqlite3.Connection's context manager commits/rolls back but does not close.
    # Explicit closing is needed so Windows can delete the temporary files.
    with closing(sqlite3.connect(source_path)) as source, closing(sqlite3.connect(target_path)) as target:
        source.backup(target)


async def expected_snapshot(app, path):
    """Read only from the immutable shutdown backup, never from a moving API response."""
    if path == "/api/market/indices":
        return await app.state.market_service._indices.peek("indices")
    if path == "/api/market/breadth":
        return await app.state.market_service._breadth.peek("breadth")
    if path.startswith("/api/market/indices/"):
        return await app.state.market_service._intraday.peek(path.split("/")[4])
    parts = path.split("/")
    if len(parts) != 5 or parts[2] != "stocks":
        return None
    symbol, resource = parts[3:]
    stocks = app.state.stock_service
    if resource == "quote":
        return (await stocks.cached_quotes([symbol])).get(symbol)
    if resource == "intraday":
        return await stocks.cached_intraday(symbol)
    if resource.startswith("kline?"):
        return await stocks.cached_kline(symbol, "daily", 120)
    if resource == "money-flow":
        return await app.state.money_flow_service._store.peek(symbol)
    if resource == "sentiment":
        return await app.state.sentiment_service.store.peek(symbol)
    return None


async def run(duration):
    started = monotonic()
    now = datetime.now(timezone.utc)
    events = []
    report = {"started_at": stamp(), "beijing_date": now.astimezone(BEIJING).date().isoformat(),
              "market_session_at_start": is_market_session(now), "duration_requested_seconds": duration,
              "mode": "Real HTTP sources; isolated temporary SQLite; no detail reads during collection; no model calls.",
              "limitations": ["Bounded three-stock sample, not all user watchlist stocks or long-term stability.",
                              "Source timestamps and repeated prices do not prove a new trade every two seconds.",
                              "Offline restart below is controlled source failure, not a naturally observed outage."],
              "source_sha256": {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                                for path in [Path(__file__), ROOT / "backend/app/providers/resilient.py",
                                             ROOT / "backend/app/services/collector.py",
                                             ROOT / "backend/app/services/cache.py",
                                             ROOT / "backend/app/services/stock.py",
                                             ROOT / "backend/app/services/snapshots.py"]},
              "initial_watchlist": ["600519", "300750", "600578"], "checkpoints": []}
    with tempfile.TemporaryDirectory(prefix="stockpilot-online-") as directory:
        url = f"sqlite:///{(Path(directory) / 'acceptance.db').as_posix()}"
        provider = tracked_provider(events, started)
        app = create_app(provider=provider, database_url=url, prefetch_enabled=True)
        try:
            async with app.router.lifespan_context(app):
                watch = app.state.watchlist_service
                for symbol in report["initial_watchlist"]:
                    watch.add(symbol)
                sessions = watch._session_factory
                changed = False
                next_checkpoint = 30
                while monotonic() - started < duration:
                    elapsed = monotonic() - started
                    if elapsed >= duration / 2 and not changed:
                        watch.remove("600578")
                        watch.add("002414")
                        report["membership_change"] = {"elapsed_seconds": round(elapsed, 3),
                                                       "remove": "600578", "add": "002414"}
                        changed = True
                    if elapsed >= next_checkpoint:
                        report["checkpoints"].append({"elapsed_seconds": round(elapsed, 3), **snapshots(sessions)})
                        next_checkpoint += 30
                    await asyncio.sleep(0.25)
                report["collection_finished_at"] = stamp()
                report["background_events"] = list(events)
                report["background_summary"] = summarize(events)
                report["final_snapshots"] = snapshots(sessions)
                paths = ["/health", "/api/watchlist/status", "/api/watchlist/quotes",
                         "/api/market/indices", "/api/market/breadth"]
                paths.extend(f"/api/market/indices/{code}/intraday" for code in ("000001", "399001", "399006"))
                for symbol in ("600519", "300750", "002414"):
                    paths.extend([f"/api/stocks/{symbol}/quote", f"/api/stocks/{symbol}/intraday",
                                  f"/api/stocks/{symbol}/kline?period=daily&limit=120",
                                  f"/api/stocks/{symbol}/money-flow", f"/api/stocks/{symbol}/sentiment"])
                baseline = {}
                report["api_checks"] = []
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://acceptance") as client:
                    for path in paths:
                        response = await client.get(path, timeout=30)
                        body = response.json()
                        data = body.get("data")
                        count = len(data) if isinstance(data, list) else (
                            len(data["items"]) if isinstance(data, dict) and isinstance(data.get("items"), list)
                            else None)
                        report["api_checks"].append({"path": path, "http_status": response.status_code,
                            "stale": body.get("stale"), "collection_state": body.get("collection_state"),
                            "cached_at": body.get("cached_at"), "has_data": count > 0 if count is not None else bool(data),
                            "item_count": count})
                        if response.status_code == 200 and data and body.get("cached_at"):
                            baseline[path] = body
        finally:
            await provider.aclose()

        frozen_path = Path(directory) / "shutdown-backup.db"
        copy_shutdown_database(Path(directory) / "acceptance.db", frozen_path)
        frozen_url = f"sqlite:///{frozen_path.as_posix()}"
        report["offline_baseline"] = "Immutable SQLite backup after full application shutdown. Earlier live API responses may precede the final background write."
        offline = ResilientMarketProvider()
        blocked_calls = []

        for name in ("get_quotes", "get_stock_intraday", "get_index_intraday", "get_kline", "get_indices", "get_market_breadth",
                     "get_stock_money_flow", "get_stock_comments"):
            async def unavailable(*args, _name=name, **kwargs):
                blocked_calls.append(_name)
                raise DataSourceError("Controlled offline acceptance")
            setattr(offline, name, unavailable)
        restarted = create_app(provider=offline, database_url=frozen_url, prefetch_enabled=False)
        report["controlled_offline_restart"] = []
        try:
            async with restarted.router.lifespan_context(restarted):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=restarted), base_url="http://acceptance") as client:
                    for path, previous in baseline.items():
                        if path in ("/api/watchlist/status", "/api/watchlist/quotes"):
                            continue
                        expected = await expected_snapshot(restarted, path)
                        if expected is None:
                            report["controlled_offline_restart"].append({"path": path, "error": "No valid snapshot in shutdown backup"})
                            continue
                        response = await client.get(path)
                        body = response.json()
                        report["controlled_offline_restart"].append({"path": path,
                            "http_status": response.status_code, "stale": body.get("stale"),
                            "same_data": body.get("data") == to_jsonable_python(expected.data),
                            "same_cached_at": bool(body.get("cached_at")) and datetime.fromisoformat(body["cached_at"]) == expected.cached_at,
                            "expected_cached_at": expected.cached_at.isoformat(), "actual_cached_at": body.get("cached_at")})
                deps = AgentDependencies(restarted.state.stock_service, restarted.state.market_service,
                                         restarted.state.watchlist_service)
                report["cache_only_tools"] = []
                for name, call in (
                    ("get_stock_quote", lambda: get_stock_quote(deps, "300750")),
                    ("get_stock_kline", lambda: get_stock_kline(deps, "300750", "daily", 5)),
                    ("get_market_indices", lambda: get_market_indices(deps)),
                ):
                    before = len(blocked_calls)
                    try:
                        result = await call()
                        report["cache_only_tools"].append({"tool": name, "success": True,
                            "new_provider_calls": len(blocked_calls) - before, "stale": result.get("stale"),
                            "cached_at": result.get("cached_at")})
                    except DataSourceError:
                        report["cache_only_tools"].append({"tool": name, "success": False,
                                                          "new_provider_calls": len(blocked_calls) - before})
        finally:
            await offline.aclose()
    report["finished_at"] = stamp()
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration", type=int, default=180)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.duration < 60:
        parser.error("duration must be at least 60 seconds")
    logging.basicConfig(level=logging.ERROR)
    result = asyncio.run(run(args.duration))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(args.output), "background_summary": result["background_summary"],
                      "api_checks": result["api_checks"], "offline_restart": result["controlled_offline_restart"]},
                     ensure_ascii=False, indent=2))

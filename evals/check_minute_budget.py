"""Real minute reads plus explicitly controlled slow-backup/cache recovery checks."""

import argparse
import asyncio
import hashlib
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import httpx

from app.main import create_app
from app.providers.exceptions import DataSourceError
from app.providers.resilient import ResilientMarketProvider
from app.services.collector import BEIJING, is_market_session


class ControlledSlowBackup:
    def __init__(self):
        self.calls = 0
        self.cancelled = 0

    async def get_stock_intraday(self, symbol):
        self.calls += 1
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled += 1
            raise

    async def aclose(self):
        pass


async def run():
    now = datetime.now(timezone.utc)
    report = {"started_at": now.isoformat(), "market_session": is_market_session(now),
              "beijing_time": now.astimezone(BEIJING).isoformat(),
              "minute_chain_budget_seconds": 2,
              "mode": "Real HTTP normal reads; controlled one preferred failure and indefinitely slow backup; real preferred recovery.",
              "limits": ["A short check, not long-term two-second source stability.",
                         "Off-session source timestamps/prices may remain unchanged; a new cache time is not a new trade.",
                         "Source failures and simulated failures are reported separately; no model calls or user database writes."],
              "source_sha256": {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                                for path in (Path(__file__), ROOT / "backend/app/providers/resilient.py",
                                             ROOT / "backend/app/services/cache.py")}}
    provider = ResilientMarketProvider()
    with tempfile.TemporaryDirectory(prefix="stockpilot-minute-budget-") as directory:
        app = create_app(provider=provider,
                         database_url=f"sqlite:///{(Path(directory) / 'acceptance.db').as_posix()}",
                         prefetch_enabled=False)
        try:
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://acceptance") as client:
                    report["health"] = (await client.get("/health")).status_code
                    paths = [f"/api/stocks/{code}/intraday" for code in ("600519", "300750", "000001")]
                    paths.extend(f"/api/market/indices/{code}/intraday" for code in ("000001", "399001", "399006"))

                    async def read(path):
                        begin = monotonic()
                        response = await client.get(path)
                        body = response.json()
                        points = body.get("data", [])
                        return {"path": path, "http_status": response.status_code,
                                "elapsed_seconds": round(monotonic() - begin, 4),
                                "point_count": len(points), "stale": body.get("stale"),
                                "cached_at": body.get("cached_at"),
                                "source": points[-1].get("source") if points else None,
                                "source_time": points[-1].get("time") if points else None}

                    report["normal_reads"] = await asyncio.gather(*(read(path) for path in paths))
                    stocks = app.state.stock_service
                    baseline = await stocks.cached_intraday("600519")
                    if baseline is None:
                        report["controlled_recovery"] = {"skipped": "No real successful baseline for 600519"}
                    else:
                        preferred = provider._minute_sources[0]
                        original_preferred = preferred.get_stock_intraday
                        original_sources = provider._minute_sources
                        original_primary = provider._primary.get_stock_intraday
                        slow = ControlledSlowBackup()
                        faults = {"preferred_failures": 0, "last_source_calls": 0}

                        async def one_failure(symbol):
                            if symbol == "600519" and faults["preferred_failures"] == 0:
                                faults["preferred_failures"] += 1
                                raise DataSourceError("Controlled preferred-source failure")
                            return await original_preferred(symbol)

                        async def last_source(symbol):
                            faults["last_source_calls"] += 1
                            return await original_primary(symbol)

                        preferred.get_stock_intraday = one_failure
                        provider._primary.get_stock_intraday = last_source
                        provider._minute_sources = [preferred, slow, provider._primary]
                        # Only the isolated test target is reset to exercise the injected backup.
                        provider._sina_retry.pop(("stock", "600519", 0), None)
                        provider._intraday_retry.pop(("stock", "600519", 1), None)
                        try:
                            begin = monotonic()
                            stale = await stocks.get_intraday("600519", revalidate=True)
                            elapsed = monotonic() - begin
                            cached_api = await client.get("/api/stocks/600519/intraday")
                            cached_body = cached_api.json()
                            report["controlled_recovery"] = {
                                "elapsed_seconds": round(elapsed, 4), **faults,
                                "slow_backup_calls": slow.calls, "slow_backup_cancelled": slow.cancelled,
                                "stale": stale.stale, "same_data": stale.data == baseline.data,
                                "same_cached_at": stale.cached_at == baseline.cached_at,
                                "cache_http_status": cached_api.status_code,
                                "cache_api_stale": cached_body.get("stale"),
                                "cache_api_keeps_time": bool(cached_body.get("cached_at")) and
                                    datetime.fromisoformat(cached_body["cached_at"]) == baseline.cached_at,
                            }
                            preferred.get_stock_intraday = original_preferred
                            await asyncio.sleep(2.1)  # Service failure backoff is still respected.
                            recovered = await stocks.get_intraday("600519", revalidate=True)
                            report["controlled_recovery"].update(
                                recovered_fresh=not recovered.stale,
                                recovered_source=recovered.data[-1].source,
                                recovered_source_time=recovered.data[-1].time.isoformat(),
                                original_cached_at=baseline.cached_at.isoformat(),
                                recovered_cached_at=recovered.cached_at.isoformat(),
                                cache_time_advanced=recovered.cached_at > baseline.cached_at,
                            )
                        finally:
                            preferred.get_stock_intraday = original_preferred
                            provider._primary.get_stock_intraday = original_primary
                            provider._minute_sources = original_sources

                    async def probe(name, call):
                        try:
                            value = await call()
                            items = getattr(value, "items", None)
                            return {"resource": name, "success": True,
                                    "item_count": len(items) if isinstance(items, list) else None}
                        except DataSourceError as error:
                            return {"resource": name, "success": False, "error_type": type(error).__name__,
                                    "cause_type": type(error.__cause__).__name__ if error.__cause__ else None}
                    report["auxiliary_sources"] = await asyncio.gather(
                        probe("market_breadth", provider.get_market_breadth),
                        probe("300750_money_flow", lambda: provider.get_stock_money_flow("300750")),
                        probe("300750_comments", lambda: provider.get_stock_comments("300750")),
                    )
        finally:
            await provider.aclose()
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(run())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))

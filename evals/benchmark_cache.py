"""Controlled cached-API latency benchmark; no external sources or model calls."""

import argparse
import asyncio
import hashlib
import json
import statistics
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "backend/tests")]
import httpx
from sqlalchemy import event
from app.api.dependencies import get_stock_reader
from app.main import create_app
from app.models.market import IntradayPoint, KlineItem
from app.services.collector import WatchlistCollector
from app.services.reads import StockReadService
from test_api import FakeProvider


class Fixture(FakeProvider):
    def __init__(self):
        super().__init__()
        self.source_calls = 0
        now = datetime.now(timezone.utc)
        self.points = [IntradayPoint(time=now - timedelta(minutes=300-i), price=10+i/1000,
                                    volume=100, turnover=1000, source="sina") for i in range(300)]
        self.klines = [KlineItem(date=(now-timedelta(days=120-i)).date(), open=10, close=10,
                                high=11, low=9, volume=100, turnover=1000) for i in range(120)]

    async def get_quotes(self, symbols):
        self.source_calls += 1
        return await super().get_quotes(symbols)

    async def get_stock_intraday(self, symbol):
        self.source_calls += 1
        return self.points

    async def get_kline(self, symbol, period="daily", limit=120):
        self.source_calls += 1
        return self.klines[-limit:]


async def run(iterations):
    provider = Fixture()
    with tempfile.TemporaryDirectory(prefix="stockpilot-cache-bench-") as directory:
        app = create_app(provider=provider, database_url=f"sqlite:///{(Path(directory)/'bench.db').as_posix()}",
                         prefetch_enabled=False)
        async with app.router.lifespan_context(app):
            stocks, watch = app.state.stock_service, app.state.watchlist_service
            symbols = [f"600{i:03d}" for i in range(1, 41)]
            for symbol in symbols:
                watch.add(symbol)
            await stocks.get_quotes(symbols)
            for symbol in symbols:
                await stocks.get_intraday(symbol)
                await stocks.get_kline(symbol, "daily", 120)
                await stocks.get_kline(symbol, "weekly", 120)
            reader = StockReadService(stocks, watch, WatchlistCollector(stocks, watch))
            app.dependency_overrides[get_stock_reader] = lambda: reader
            counter = [0]
            engine = watch._session_factory.kw["bind"]
            def count(*args):
                counter[0] += 1
            event.listen(engine, "before_cursor_execute", count)
            initial_calls = provider.source_calls
            results = {}
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://bench") as client:
                for path in ("/api/watchlist/status", "/api/watchlist/quotes", "/api/stocks/600001/intraday"):
                    samples = []
                    before = counter[0]
                    for _ in range(iterations):
                        started = perf_counter()
                        response = await client.get(path)
                        assert response.status_code == 200
                        body = response.json()
                        assert body["data"]
                        samples.append((perf_counter()-started)*1000)
                    ordered = sorted(samples)
                    results[path] = {"iterations": iterations, "median_ms": round(statistics.median(samples),3),
                        "p95_ms": round(ordered[max(0, int(len(ordered)*.95)-1)],3),
                        "sql_statements": counter[0]-before}
            assert provider.source_calls == initial_calls
            event.remove(engine, "before_cursor_execute", count)
        return {"at": datetime.now(timezone.utc).isoformat(),
            "mode": "Controlled synthetic cached data, single-process ASGI requests; no source/model calls during measurement.",
            "watchlist_count": 40, "intraday_points": 300, "kline_points_per_period": 120,
            "source_calls_during_measurement": provider.source_calls-initial_calls,
            "source_sha256": {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                              for path in (Path(__file__), ROOT/'backend/app/services/cache.py', ROOT/'backend/app/services/stock.py')},
            "results": results}


if __name__ == "__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--iterations",type=int,default=20)
    args=parser.parse_args()
    result=asyncio.run(run(args.iterations))
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(result["results"],ensure_ascii=False,indent=2))

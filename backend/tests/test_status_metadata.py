import asyncio

from sqlalchemy import event

from app.services.stock import StockService
from app.services.reads import StockReadService
from test_collector import setup, settle


def test_warm_status_reads_only_metadata_and_partial_restart_backfills_missing_series(tmp_path):
    async def run():
        engine, sessions, provider, stocks, watch, collector = setup(tmp_path)
        for symbol in ("600519", "300750"):
            watch.add(symbol)
        try:
            await settle(collector)
            reader = StockReadService(stocks, watch, collector)
            statements = []
            def record(*args):
                statements.append(args[2])
            event.listen(engine, "before_cursor_execute", record)
            first = await reader.overview()
            assert len(first.items) == 2
            assert len(statements) == 2  # Watchlist + quotes; no series SQL scans.
            metadata = await stocks.cached_resources(["600519"], metadata_only=True)
            assert len(metadata[("600519", "intraday")].data) == 1
            original = await stocks.cached_intraday("600519")
            assert original.data[0].price > 0  # Metadata projection did not mutate the real series.
            restarted = StockService(provider, snapshot_sessions=sessions)
            # Only one series is in memory; the other stock must restore from SQLite.
            await restarted.get_intraday("600519")
            restored_reader = StockReadService(restarted, watch, collector)
            restored = await restored_reader.overview()
            assert len(restored.items) == 2
            assert all(item.resources["intraday"].cached_at for item in restored.items)
            statements.clear()
            again = await restored_reader.overview()
            assert len(statements) == 2
            assert restored.model_dump() == again.model_dump()
            await restarted.aclose()
            event.remove(engine, "before_cursor_execute", record)
        finally:
            await collector.stop()
            await stocks.aclose()
            engine.dispose()
    asyncio.run(run())

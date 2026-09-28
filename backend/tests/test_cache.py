import asyncio

import pytest

from app.providers.exceptions import DataSourceError, ProviderTimeoutError
from app.services.cache import AsyncTTLStore


def test_concurrent_callers_share_fetch_and_receive_independent_data():
    async def run():
        cache = AsyncTTLStore(ttl=1)
        started, release = asyncio.Event(), asyncio.Event()
        calls = 0

        async def load():
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            return [1]

        first = asyncio.create_task(cache.get("stock", load))
        await started.wait()
        second = asyncio.create_task(cache.get("stock", load))
        await asyncio.sleep(0)
        release.set()
        a, b = await asyncio.gather(first, second)
        a.data.append(2)
        assert b.data == [1]
        assert (await cache.get("stock", load)).data == [1]
        assert calls == 1
        assert not cache._pending

    asyncio.run(run())


def test_failure_backoff_is_bounded_preserves_timeout_and_resets_after_recovery():
    async def run():
        now = 0
        cache = AsyncTTLStore(ttl=1, timer=lambda: now)
        calls = 0
        offline = True

        async def load():
            nonlocal calls
            calls += 1
            if offline:
                raise ProviderTimeoutError("offline")
            return [1]

        for delay in (2, 4, 8, 16, 30, 30):
            before = calls
            with pytest.raises(ProviderTimeoutError):
                await asyncio.gather(*(cache.get("stock", load) for _ in range(8)))
            assert calls == before + 1
            now += delay - 0.1
            with pytest.raises(ProviderTimeoutError):
                await cache.get("stock", load)
            assert calls == before + 1
            now += 0.1

        offline = False
        assert not (await cache.get("stock", load)).stale
        assert not cache._failures
        offline = True
        now += 1
        assert (await cache.get("stock", load)).stale
        before = calls
        now += 2
        assert (await cache.get("stock", load)).stale
        assert calls == before + 1  # Recovery reset the retry delay to two seconds.

    asyncio.run(run())


def test_stale_data_expires_during_cooldown_and_failure_metadata_is_bounded():
    async def run():
        now = 0
        cache = AsyncTTLStore(ttl=1, stale_ttl=3, maxsize=2, timer=lambda: now)
        offline = False
        calls = 0

        async def load():
            nonlocal calls
            calls += 1
            if offline:
                raise DataSourceError("offline")
            return [1]

        await cache.get("a", load)
        offline = True
        now = 2
        result = await cache.get("a", load)
        assert result.stale
        result.data.append(2)
        assert (await cache.get("a", load)).data == [1]
        now = 3
        with pytest.raises(DataSourceError):
            await cache.get("a", load)
        assert calls == 2  # Expired stale data cannot be shown during retry cooldown.
        for key in ("b", "c", "d"):
            with pytest.raises(DataSourceError):
                await cache.get(key, load)
        assert len(cache._failures) == 2

    asyncio.run(run())


def test_cancelled_caller_does_not_cancel_shared_fetch_or_block_other_keys():
    async def run():
        cache = AsyncTTLStore(ttl=1)
        started, release = asyncio.Event(), asyncio.Event()
        calls = 0

        async def slow():
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            return [1]

        async def fast():
            return [2]

        first = asyncio.create_task(cache.get("a", slow))
        await started.wait()
        second = asyncio.create_task(cache.get("a", slow))
        await asyncio.sleep(0)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert (await cache.get("b", fast)).data == [2]
        release.set()
        assert (await second).data == [1]
        assert calls == 1
        assert not cache._pending

    asyncio.run(run())


def test_loader_cancellation_cleans_pending_without_failure_backoff():
    async def run():
        cache = AsyncTTLStore(ttl=1)

        async def cancelled():
            raise asyncio.CancelledError

        with pytest.raises(asyncio.CancelledError):
            await cache.get("a", cancelled)
        assert not cache._pending
        assert not cache._failures

        async def recovered():
            return [1]

        assert (await cache.get("a", recovered)).data == [1]

    asyncio.run(run())

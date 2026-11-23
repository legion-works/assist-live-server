import asyncio
import time

import pytest

from tests import loop_clock
from tests.loop_clock import LoopClock


def test_stopped_clock_keeps_thread_work_out_of_loop_deadline(monkeypatch):
    async def run():
        clock = LoopClock(monkeypatch)
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(0.1):
                await asyncio.to_thread(time.sleep, 0.3)
        async with asyncio.timeout(0.1):
            with clock.stopped():
                await asyncio.to_thread(time.sleep, 0.3)

    asyncio.run(run())


def test_stopped_clock_resumes_without_counting_stopped_time(monkeypatch):
    async def run():
        loop = asyncio.get_running_loop()
        clock = LoopClock(monkeypatch)
        # Real time brackets loop time: read it first here and last below.
        real_before = time.monotonic()
        loop_before = loop.time()
        with clock.stopped():
            with clock.stopped():
                start = time.monotonic()
                time.sleep(0.2)
                stopped = time.monotonic() - start
        loop_after, real_after = loop.time(), time.monotonic()
        assert loop_after >= loop_before
        assert loop_after - loop_before <= real_after - real_before - stopped + 1e-6

    asyncio.run(run())


def test_stopped_clock_continues_after_stop_limit(monkeypatch):
    monkeypatch.setattr(loop_clock, "STOP_LIMIT", 0.05)

    async def run():
        clock = LoopClock(monkeypatch)
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(0.1):
                with clock.stopped():
                    await asyncio.to_thread(time.sleep, 1.0)

    asyncio.run(run())

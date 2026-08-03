"""定时调度器测试。"""
import asyncio
from unittest import mock

from backend.services.scheduler import Scheduler


class FakeClock:
    def __init__(self):
        self.t = 1_000_000.0

    def now(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


def test_scheduler_fires_keyword_and_deepscan():
    calls: list[str] = []
    clock = FakeClock()
    intervals = [(15, None), (15, 60), (15, 60), (15, 60), (15, 60)]

    def get_intervals():
        return intervals.pop(0) if intervals else (15, 60)

    scheduler = Scheduler(lambda mode: None, get_intervals)

    async def fake_sleep(delay):
        clock.advance(delay)

    class StopLoop(asyncio.CancelledError):
        pass

    async def record_limited(mode):
        calls.append(mode)
        if len(calls) >= 6:
            raise StopLoop()

    scheduler.request_scan = record_limited

    async def run():
        with (
            mock.patch("asyncio.sleep", side_effect=fake_sleep),
            mock.patch("time.time", side_effect=clock.now),
        ):
            try:
                await scheduler._loop()
            except StopLoop:
                pass

    asyncio.run(run())
    assert len(calls) == 6
    assert calls[0] == "keyword"
    assert "deepscan" in calls


def test_scheduler_intervals():
    scheduler = Scheduler(lambda mode: None, lambda: (15, None))
    keyword_period, deep_period = scheduler._current_intervals()
    assert keyword_period == 15 * 60
    assert deep_period is None

    scheduler = Scheduler(lambda mode: None, lambda: (15, 240))
    _, deep_period = scheduler._current_intervals()
    assert deep_period == 240 * 60


def test_scheduler_recovers_without_replacing_task():
    async def scenario():
        scheduler = Scheduler(lambda mode: None, lambda: (15, None))

        with mock.patch("asyncio.sleep", side_effect=[RuntimeError("boom"), asyncio.CancelledError()]):
            try:
                await scheduler._loop()
            except asyncio.CancelledError:
                pass

        assert scheduler._task is None

    asyncio.run(scenario())

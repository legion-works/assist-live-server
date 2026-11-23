import asyncio
from contextlib import contextmanager

# A stopped clock also stops the hang guards of a test. After this real time,
# the clock continues, so a store call that does not return still fails.
STOP_LIMIT = 30.0


class LoopClock:
    """Event-loop clock that a test can stop while setup work runs.

    SQLite I/O and connection setup are not the timed behavior of a test.
    Under host load they can use a full protocol window. While the clock is
    stopped, loop deadlines do not count that time, and the protocol limits
    keep their exact values.
    """

    def __init__(self, monkeypatch):
        loop = asyncio.get_running_loop()
        self._real = loop.time
        self._offset = 0.0
        self._depth = 0
        self._start = 0.0
        monkeypatch.setattr(loop, "time", self.time)

    def time(self):
        now = self._real()
        if self._depth:
            late = max(0.0, now - self._start - STOP_LIMIT)
            return self._start + self._offset + late
        return now + self._offset

    @contextmanager
    def stopped(self):
        if not self._depth:
            self._start = self._real()
        self._depth += 1
        try:
            yield
        finally:
            self._depth -= 1
            if not self._depth:
                self._offset -= min(self._real() - self._start, STOP_LIMIT)

    def exclude_store(self, monkeypatch, store):
        """Stop the clock while a call waits for the SQLite writer thread."""
        call = store._call

        async def stopped_call(function, *args):
            with self.stopped():
                return await call(function, *args)

        monkeypatch.setattr(store, "_call", stopped_call)

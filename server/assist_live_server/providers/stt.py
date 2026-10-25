"""Bound CPU work and expose the selected local provider."""

import asyncio
import heapq
import itertools
import threading
from collections.abc import Awaitable, Callable
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Protocol


class STTClosedError(RuntimeError):
    """The session is finished or cancelled."""


class AudioLimitError(ValueError):
    """The complete push would exceed the audio limit."""


class STTBusyError(RuntimeError):
    """The bounded executor cannot accept another job."""


class STTSession(Protocol):
    """Accept ordered mono 16 kHz little-endian int16 PCM."""

    async def push(self, pcm: bytes) -> None: ...
    async def partial(self) -> str: ...
    async def finish(self) -> str: ...
    def speech_started(self) -> None: ...
    def cancel(self) -> None: ...


class STTProvider(Protocol):
    def session(self) -> STTSession: ...


# One worker prevents concurrent use of the shared decoder. Four slots bound
# running and queued work together, including work whose caller cancelled.
_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="local-stt")
_SLOTS = threading.BoundedSemaphore(4)
_QUEUE: list[tuple[int, int, Callable[[], None]]] = []
_SEQUENCE = itertools.count()
# A fast worker can finish before callback registration. The callback can then
# dispatch the next job in the caller thread while this lock is held.
_LOCK = threading.RLock()
_active = False


def _dispatch() -> None:
    global _active
    if not _QUEUE:
        _active = False
        return
    _active = True
    _, _, work = heapq.heappop(_QUEUE)
    future = _EXECUTOR.submit(work)
    future.add_done_callback(_completed)


def _completed(future: Future[None]) -> None:
    with _LOCK:
        _dispatch()


async def run_decode[T](operation: Callable[[], T], *, final: bool = False) -> T:
    if not _SLOTS.acquire(blocking=False):
        raise STTBusyError("local STT executor busy: four jobs admitted")
    result: Future[T] = Future()

    def work() -> None:
        if not result.set_running_or_notify_cancel():
            _SLOTS.release()
            return
        try:
            value = operation()
        except BaseException as error:
            _SLOTS.release()
            result.set_exception(error)
        else:
            _SLOTS.release()
            result.set_result(value)

    with _LOCK:
        heapq.heappush(_QUEUE, (0 if final else 1, next(_SEQUENCE), work))
        if not _active:
            _dispatch()
    return await asyncio.wrap_future(result)


type ProviderFactory = Callable[..., Awaitable[STTProvider]]


def provider_names(*, mode: str = "local_compatible") -> tuple[str, ...]:
    if mode == "local_streaming":
        from .streaming_local import STREAMING_PROVIDERS

        return STREAMING_PROVIDERS
    if mode != "local_compatible":
        raise ValueError(f"unknown STT mode: {mode}")
    return ("parakeet",)


def provider_factory(
    name: str | None = None, *, mode: str = "local_compatible"
) -> ProviderFactory:
    if mode == "local_streaming":
        from .streaming_local import no_streaming_default

        no_streaming_default(name)
    names = provider_names(mode=mode)
    if (name or "parakeet") not in names:
        raise ValueError(f"unknown STT provider: {name}")
    from .parakeet import ParakeetProvider

    return ParakeetProvider.load

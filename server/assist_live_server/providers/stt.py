"""Bound CPU work and expose the selected local provider."""

import asyncio
import heapq
import itertools
import threading
from collections.abc import Awaitable, Callable
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Protocol

from ..destinations import Destination


class STTClosedError(RuntimeError):
    """The session is finished or cancelled."""


class AudioLimitError(ValueError):
    """The complete push would exceed the audio limit."""


class STTBusyError(RuntimeError):
    """The bounded executor cannot accept another job."""


class STTCheckpointDeadlineError(RuntimeError):
    """The checkpoint did not finish before its deadline."""


class STTSession(Protocol):
    """Accept ordered mono 16 kHz little-endian int16 PCM.

    Sessions can set final_redecodes=True when finish decodes all audio again.
    Without this optional attribute, the gateway keeps the final unchanged.
    """

    async def push(self, pcm: bytes) -> None: ...
    async def partial(self) -> str: ...
    async def finish(self) -> str: ...
    def speech_started(self) -> None: ...
    def cancel(self) -> None: ...


class STTProvider(Protocol):
    def session(self) -> STTSession: ...


# One worker prevents concurrent use of the shared decoder. Four slots bound
# running and queued decodes together, including work whose caller cancelled.
_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="local-stt")
_SLOTS = threading.BoundedSemaphore(4)
# Checkpoints must keep one decode slot for a `final` request.
_CHECKPOINT_SLOTS = threading.BoundedSemaphore(3)
_QUEUE: list[tuple[int, int, Callable[[], None], Future[Any], Callable[[], None]]] = []
_SEQUENCE = itertools.count()
# A fast worker can finish before callback registration. The callback can then
# dispatch the next job in the caller thread while this lock is held.
_LOCK = threading.RLock()
_active = False
_hint_build_waits_total = 0
_final_admission_refusals_total = 0


def _dispatch() -> None:
    global _active
    while _QUEUE:
        _, _, work, result, release = heapq.heappop(_QUEUE)
        if result.cancelled():
            # Canceled queue entries must not start an executor task at shutdown.
            work()
            continue
        try:
            future = _EXECUTOR.submit(work)
        except RuntimeError:
            release()
            if result.set_running_or_notify_cancel():
                result.set_exception(STTClosedError("local STT executor closed"))
            continue
        _active = True
        future.add_done_callback(_completed)
        return
    _active = False


def _completed(future: Future[None]) -> None:
    with _LOCK:
        _dispatch()


async def run_decode[T](operation: Callable[[], T], *, final: bool = False) -> T:
    global _final_admission_refusals_total
    if not _SLOTS.acquire(blocking=False):
        if final:
            with _LOCK:
                _final_admission_refusals_total += 1
        raise STTBusyError("local STT executor busy: four jobs admitted")
    return await _run_job(operation, priority=0 if final else 1, admitted=True)


async def run_checkpoint_decode[T](operation: Callable[[], T]) -> T:
    if not _CHECKPOINT_SLOTS.acquire(blocking=False):
        raise STTBusyError("local STT checkpoint admission full")
    if not _SLOTS.acquire(blocking=False):
        _CHECKPOINT_SLOTS.release()
        raise STTBusyError("local STT executor busy: four jobs admitted")
    return await _run_job(operation, priority=1, admitted=True, checkpoint=True)


def final_admission_refusals_total() -> int:
    with _LOCK:
        return _final_admission_refusals_total


def hint_build_waits_total() -> int:
    with _LOCK:
        return _hint_build_waits_total


async def run_hint_build[T](operation: Callable[[], T]) -> T:
    # Decode slots are for decodes. Queued builds must run after queued decodes.
    return await _run_job(operation, priority=2, admitted=False)


async def _run_job[T](
    operation: Callable[[], T],
    *,
    priority: int,
    admitted: bool,
    checkpoint: bool = False,
) -> T:
    global _hint_build_waits_total
    result: Future[T] = Future()

    def release() -> None:
        if admitted:
            _SLOTS.release()
        if checkpoint:
            _CHECKPOINT_SLOTS.release()

    def work() -> None:
        if not result.set_running_or_notify_cancel():
            release()
            return
        try:
            value = operation()
        except BaseException as error:
            release()
            result.set_exception(error)
        else:
            release()
            result.set_result(value)

    with _LOCK:
        entry = (priority, next(_SEQUENCE), work, result, release)
        if not admitted and _active:
            _hint_build_waits_total += 1
        heapq.heappush(_QUEUE, entry)
        if not _active:
            _dispatch()
    try:
        return await asyncio.wrap_future(result)
    except asyncio.CancelledError:
        if not admitted or checkpoint:
            with _LOCK:
                result.cancel()
                # A blocked worker must not keep superseded lists in the queue.
                # Queued checkpoints must release slots before a `final` request.
                if entry in _QUEUE:
                    _QUEUE.remove(entry)
                    heapq.heapify(_QUEUE)
                    release()
        raise


type ProviderFactory = Callable[..., Awaitable[STTProvider]]

PROVIDER_DESTINATIONS: dict[str, Destination] = {
    "parakeet": "local",
    "deepgram": "cloud",
    "openai": "cloud",
}


def provider_names(*, mode: str = "all") -> tuple[str, ...]:
    if mode == "local_streaming":
        from .streaming_local import STREAMING_PROVIDERS

        return STREAMING_PROVIDERS
    if mode == "local_compatible":
        return tuple(
            name
            for name, destination in PROVIDER_DESTINATIONS.items()
            if destination == "local"
        )
    if mode != "all":
        raise ValueError(f"unknown STT mode: {mode}")
    return tuple(PROVIDER_DESTINATIONS)


def provider_destination(name: str) -> Destination:
    try:
        return PROVIDER_DESTINATIONS[name]
    except KeyError:
        raise ValueError(f"unknown STT provider: {name}") from None


async def _load_deepgram(api_key: str) -> STTProvider:
    from .deepgram import DeepgramProvider

    return DeepgramProvider(api_key)


async def _load_openai(api_key: str) -> STTProvider:
    import os

    from .openai_stt import OpenAIProvider

    return OpenAIProvider(
        api_key, delay=os.environ.get("OPENAI_TRANSCRIPTION_DELAY", "low")
    )


def provider_factory(name: str | None = None, *, mode: str = "all") -> ProviderFactory:
    if mode == "local_streaming":
        from .streaming_local import no_streaming_default

        no_streaming_default(name)
    names = provider_names(mode=mode)
    if (name or "parakeet") not in names:
        raise ValueError(f"unknown STT provider: {name}")
    if name == "deepgram":
        return _load_deepgram
    if name == "openai":
        return _load_openai
    from .parakeet import ParakeetProvider

    return ParakeetProvider.load

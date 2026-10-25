"""Sentence-level TTS with a separate, bounded CPU executor."""

import asyncio
import re
import threading
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Protocol

from assist_live_client.models import TTSItem


class TTSBusyError(RuntimeError):
    """The TTS executor has four admitted sentences."""


class TTSProvider(Protocol):
    def synthesize(
        self, text: AsyncIterator[str], voice: str
    ) -> AsyncIterator[bytes]: ...
    def stream(
        self, text: AsyncIterator[str], voice: str
    ) -> AsyncIterator[TTSItem]: ...


# Piper shares a process-global phonemizer. One worker serializes it, and a
# separate pool prevents queued STT partials from delaying TTS admission.
_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="local-tts")
_SLOTS = threading.BoundedSemaphore(4)


async def run_synthesis[T](operation: Callable[[], T]) -> T:
    if not _SLOTS.acquire(blocking=False):
        raise TTSBusyError("local TTS executor busy: four sentences admitted")
    try:
        future = _EXECUTOR.submit(operation)
    except BaseException:
        _SLOTS.release()
        raise
    # Cancellation cannot interrupt native inference. Keep its slot until the
    # sentence returns. A queued Future can cancel before it touches the model.
    future.add_done_callback(lambda _: _SLOTS.release())
    return await asyncio.wrap_future(future)


_BOUNDARY = re.compile(r"""[.!?]+["'”’)\]]*(?=\s|$)""")
_ABBREVIATIONS = frozenset(
    "mr. mrs. ms. dr. prof. sr. jr. st. vs. etc. e.g. i.e. a.m. p.m.".split()
)


def _split(buffer: str) -> tuple[list[str], str]:
    sentences = []
    start = 0
    for match in _BOUNDARY.finditer(buffer):
        # A period at a part boundary can still be the decimal point in 3.5.
        # Require right context, including space, before declaring a sentence.
        if match.end() == len(buffer):
            break
        prefix = buffer[start : match.end()]
        token = prefix.split()[-1].lower()
        if match.group() == "." and (
            token in _ABBREVIATIONS or re.fullmatch(r"(?:[a-z]\.)+", token)
        ):
            continue
        sentence = prefix.strip()
        if sentence:
            sentences.append(sentence)
        start = match.end()
    return sentences, buffer[start:]


async def sentences(text: AsyncIterator[str]) -> AsyncGenerator[str]:
    buffer = ""
    async for part in text:
        buffer += part
        ready, buffer = _split(buffer)
        if len(buffer) > 16384:
            raise ValueError("TTS sentence exceeds 16384 characters")
        for sentence in ready:
            yield sentence
    if buffer.strip():
        yield buffer.strip()


type ProviderFactory = Callable[..., Awaitable[TTSProvider]]


def provider_names() -> tuple[str, ...]:
    return ("piper",)


def provider_factory(name: str = "piper") -> ProviderFactory:
    if name not in provider_names():
        raise ValueError(f"unknown TTS provider: {name}")
    from .piper import PiperProvider

    return PiperProvider.load

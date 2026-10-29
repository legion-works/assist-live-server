"""Sentence-level TTS with a separate, bounded CPU executor."""

import asyncio
import os
import threading
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Protocol

from assist_live_client.models import TTSItem

from ..destinations import Destination


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


_ABBREVIATIONS = frozenset(
    "mr. mrs. ms. dr. prof. sr. jr. st. vs. etc. e.g. i.e. a.m. p.m.".split()
)
_MAX_ABBREVIATION = max(map(len, _ABBREVIATIONS))
_OPENING = frozenset("\"'“‘([")
_CLOSING = frozenset("\"'”’)]")


class SentenceAssembler:
    def __init__(self) -> None:
        self.buffer: list[str] = []
        self._boundary = 0
        self._period = False
        self._token: str | None = ""
        self._opening = True
        self._initials = 0
        self.received = 0

    def _read_token(self, character: str) -> None:
        if character.isspace():
            self._token = ""
            self._opening = True
            self._initials = 0
            return
        if self._opening and character in _OPENING:
            return
        self._opening = False
        # Keep only a bounded spelling for named abbreviations. The two-state
        # initials check retains the meaning of arbitrarily long dotted tokens.
        for lower in character.lower():
            if self._token is not None:
                self._token += lower
                if len(self._token) > _MAX_ABBREVIATION:
                    self._token = None
            if self._initials == 0 and "a" <= lower <= "z":
                self._initials = 1
            elif self._initials == 1 and lower == ".":
                self._initials = 0
            else:
                self._initials = -1

    def feed(self, part: str) -> list[str]:
        return [sentence for sentence, _ in self.feed_positions(part)]

    def feed_positions(self, part: str) -> list[tuple[str, int]]:
        ready = []
        for character in part:
            if self._boundary and character.isspace():
                if not self._period or not (
                    self._token in _ABBREVIATIONS or self._initials == 0
                ):
                    sentence = "".join(self.buffer).strip()
                    if sentence:
                        ready.append((sentence, self.received))
                    self.buffer.clear()
                self._boundary = 0
            elif character in ".!?":
                self._period = self._boundary != 1 and character == "."
                self._boundary = 1
            elif self._boundary and character in _CLOSING:
                self._boundary = 2
                self._period = False
            else:
                self._boundary = 0
            # Each character advances the scan once, even when parts contain
            # only one character. No old prefix is scanned or copied again.
            self.buffer.append(character)
            self.received += 1
            self._read_token(character)
        if len(self.buffer) > 16384:
            raise ValueError("TTS sentence exceeds 16384 characters")
        return ready


async def sentences(text: AsyncIterator[str]) -> AsyncGenerator[tuple[str, int]]:
    assembler = SentenceAssembler()
    async for part in text:
        for sentence in assembler.feed_positions(part):
            yield sentence
    if fragment := "".join(assembler.buffer).strip():
        yield fragment, assembler.received


type ProviderFactory = Callable[..., Awaitable[TTSProvider]]

PROVIDER_DESTINATIONS: dict[str, Destination] = {
    "piper": "local",
    "elevenlabs": "cloud",
}


def provider_names() -> tuple[str, ...]:
    return tuple(PROVIDER_DESTINATIONS)


def provider_destination(name: str) -> Destination:
    try:
        return PROVIDER_DESTINATIONS[name]
    except KeyError:
        raise ValueError(f"unknown TTS provider: {name}") from None


async def _load_elevenlabs(api_key: str) -> TTSProvider:
    from .elevenlabs import ElevenLabsProvider

    voice = os.environ.get("ELEVENLABS_VOICE_ID")
    if not voice:
        return ElevenLabsProvider(api_key)
    return ElevenLabsProvider(
        api_key,
        voice=voice,
        voice_name=os.environ.get("ELEVENLABS_VOICE_NAME"),
        voice_language=os.environ.get("ELEVENLABS_VOICE_LANGUAGE"),
    )


def provider_factory(name: str = "piper") -> ProviderFactory:
    if name not in provider_names():
        raise ValueError(f"unknown TTS provider: {name}")
    if name == "elevenlabs":
        return _load_elevenlabs
    from .piper import PiperProvider

    return PiperProvider.load

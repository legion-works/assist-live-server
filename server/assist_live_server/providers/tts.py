"""Sentence-level TTS with a separate, bounded CPU executor."""

import asyncio
import os
import threading
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from typing import Protocol

from assist_live_client.models import TTSItem

from ..destinations import Destination
from ..spoken import SpokenText


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
    def __init__(self, *, spoken: bool = True) -> None:
        self.buffer: list[str] = []
        self._boundary = 0
        self._period = False
        self._token: str | None = ""
        self._opening = True
        self._initials = 0
        self.received = 0
        self._position = 0
        self._spoken = SpokenText() if spoken else None
        self._audible = False
        self._unsent: list[str] = []
        self._trailing = False
        self.spoken_part = ""
        self.line_ends: set[int] = set()

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
        start = self.received
        self.received += len(part)
        characters = (
            self._spoken.feed(part)
            if self._spoken is not None
            else ((char, start + index + 1, False) for index, char in enumerate(part))
        )
        return self._scan(characters)

    def _scan(
        self, characters: Iterator[tuple[str, int, bool]]
    ) -> list[tuple[str, int]]:
        ready = []
        output = []
        self.line_ends.clear()
        for character, end, forced in characters:
            was_audible = self._audible or self._trailing
            if forced:
                fragment = "".join(self.buffer).strip()
                if fragment and any(c.isalnum() for c in fragment):
                    ready.append((fragment, end))
                    self.line_ends.add(end)
                self.buffer.clear()
                self._boundary = 0
                self._read_token(" ")
                self._audible = False
                self._unsent.clear()
                self._trailing = was_audible
            if not character:
                self._position = end
                continue
            if self._boundary and character.isspace():
                if not self._period or not (
                    self._token in _ABBREVIATIONS or self._initials == 0
                ):
                    sentence = "".join(self.buffer).strip()
                    if sentence and (
                        self._spoken is None or any(c.isalnum() for c in sentence)
                    ):
                        ready.append((sentence, self._position))
                    self.buffer.clear()
                    self._audible = False
                    self._unsent.clear()
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
            if self._spoken is not None:
                if self._audible or (character.isspace() and was_audible):
                    output.append(character)
                elif character.isalnum():
                    output.extend(self._unsent)
                    self._unsent.clear()
                    output.append(character)
                    self._audible = True
                else:
                    self._unsent.append(character)
                self._trailing = False
            self._position = end
            self._read_token(character)
        if len(self.buffer) > 16384:
            raise ValueError("TTS sentence exceeds 16384 characters")
        self.spoken_part = "".join(output)
        return ready

    def finish_positions(self) -> list[tuple[str, int]]:
        ready = self._scan(self._spoken.finish()) if self._spoken is not None else []
        fragment = "".join(self.buffer).strip()
        if fragment and (self._spoken is None or any(c.isalnum() for c in fragment)):
            ready.append((fragment, self.received))
        self.buffer.clear()
        return ready


async def sentences(text: AsyncIterator[str]) -> AsyncGenerator[tuple[str, int]]:
    assembler = SentenceAssembler()
    scanned = 0
    async for part in text:
        for offset in range(0, len(part), 1024):
            section = part[offset : offset + 1024]
            for sentence in assembler.feed_positions(section):
                yield sentence
            scanned += len(section)
            if scanned >= 1024:
                # Limit event-loop work when a reply has no sentence boundary.
                await asyncio.sleep(0)
                scanned = 0
    for sentence in assembler.finish_positions():
        yield sentence


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

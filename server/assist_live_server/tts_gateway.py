"""Select a TTS destination before the text iterator can supply content."""

import json
import time
from collections.abc import AsyncIterator, Callable
from typing import Any

from assist_live_client.models import (
    TTSAudio,
    TTSFormat,
    TTSItem,
    TTSSentenceEnd,
    TTSVoice,
)
from assist_live_client.protocol import MAX_PCM, decode_control, encode_control

from .audio_gateway import Recorder
from .destinations import Destination, require_destination
from .protocol import AudioBackend
from .providers.tts import SentenceAssembler, TTSProvider


class TTSGateway:
    def __init__(
        self,
        provider_factory: Callable[[str], TTSProvider] | None = None,
        *,
        provider: str = "piper",
        destination: Destination | None = None,
        local_only: bool = True,
        backend: AudioBackend | None = None,
        voices: tuple[TTSVoice, ...] = (),
        default_voice: str | None = None,
    ) -> None:
        self.provider_factory = provider_factory
        self.provider = provider
        self.destination: Destination = "local" if destination is None else destination
        self.declared_destination = destination
        self.local_only = local_only
        self.backend = backend
        self.voices = voices
        self.default_voice = default_voice
        if (provider_factory is None) == (backend is None):
            raise ValueError("select one TTS source")

    async def _backend_items(
        self, turn_id: str, text: AsyncIterator[str], voice: str
    ) -> AsyncIterator[TTSItem]:
        assert self.backend is not None
        audio_format = getattr(self.backend, "audio_format", None)
        if not isinstance(audio_format, TTSFormat):
            raise ValueError("backend audio format required")
        yield audio_format
        source = self.backend.tts(turn_id, text, voice)
        sentence = 0
        pending = False
        try:
            async for item in source:
                if isinstance(item, bytes):
                    if not item or len(item) % 2 or len(item) > MAX_PCM:
                        raise ValueError("invalid backend PCM")
                    pending = True
                    yield TTSAudio(item)
                else:
                    message = decode_control(json.dumps(item), turn_id)
                    if (
                        message["type"] != "sentence_end"
                        or message["sentence"] != sentence
                        or not pending
                    ):
                        raise ValueError("invalid backend sentence")
                    pending = False
                    yield TTSSentenceEnd(sentence)
                    sentence += 1
            if pending:
                raise ValueError("backend sentence incomplete")
        finally:
            if hasattr(source, "aclose"):
                await source.aclose()

    async def stream(
        self,
        turn_id: str,
        text: AsyncIterator[str],
        voice: str,
        *,
        record: Recorder | None = None,
        text_positions: bool = False,
        request: int = 0,
        sentence_limit: int = 256,
    ) -> AsyncIterator[dict[str, Any] | bytes]:
        metadata: dict[str, object] = {
            "part": "tts",
            "provider": self.provider,
            "request": request,
        }
        if self.provider_factory is not None and self.declared_destination is not None:
            metadata["destination"] = self.declared_destination
        stream: AsyncIterator[TTSItem] | None = None
        first_text = False
        has_text = False
        first_audio = False
        pending_audio = False
        sentence = 0
        received = 0
        failure_code = "tts_failed"
        origin_ns: int | None = None
        text_ready: list[tuple[int, int]] = []
        assembler: SentenceAssembler | None = SentenceAssembler()
        last_text_ms = 0
        first_sentence_ms = 0
        last_audio_ns: int | None = None
        sentence_bytes = 0
        total_bytes = 0
        audio_format: TTSFormat | None = None

        async def sentence_note(index: int, text_end: int | None) -> None:
            nonlocal sentence_bytes
            if record is not None and index < sentence_limit:
                text_ms = last_text_ms
                if index < len(text_ready):
                    text_ms = text_ready[index][1]
                await record(
                    "tts_sentence",
                    {
                        "request": request,
                        "index": index,
                        "text_end": text_end,
                        "text_ms": text_ms,
                        "first_byte_ms": first_sentence_ms,
                        "audio_ms": duration(sentence_bytes),
                        "t_utc_ns": last_audio_ns
                        if last_audio_ns is not None
                        else time.time_ns(),
                    },
                )
            sentence_bytes = 0

        def duration(size: int) -> int:
            if audio_format is None:
                return 0
            return (
                size
                * 1000
                // (audio_format.rate * audio_format.channels * audio_format.width)
            )

        async def input_text() -> AsyncIterator[str]:
            nonlocal first_text, has_text, received, origin_ns, assembler, last_text_ms
            async for chunk in text:
                received += len(chunk)
                has_text = has_text or bool(chunk.strip())
                if chunk and not first_text:
                    first_text = True
                    origin_ns = time.monotonic_ns()
                    if record is not None:
                        await record("tts_text_received", metadata)
                if origin_ns is not None:
                    last_text_ms = (time.monotonic_ns() - origin_ns) // 1_000_000
                if assembler is not None:
                    for _, endpoint in assembler.feed_positions(chunk):
                        if len(text_ready) < sentence_limit:
                            text_ready.append((endpoint, last_text_ms))
                    if len(text_ready) >= sentence_limit:
                        assembler = None
                yield chunk
            if assembler is not None and "".join(assembler.buffer).strip():
                text_ready.append((received, last_text_ms))

        try:
            require_destination(self.destination, self.local_only)
            selected_provider = (
                self.provider_factory(self.provider)
                if self.provider_factory is not None
                else None
            )
            selected = (
                selected_provider if selected_provider is not None else self.backend
            )
            voices = getattr(selected, "voices", self.voices)
            voice = (
                voice or getattr(selected, "default_voice", self.default_voice) or ""
            )
            if voices and voice not in {item.id for item in voices}:
                failure_code = "unknown_tts_voice"
                raise ValueError("unknown TTS voice")
            if self.backend is not None:
                stream = self._backend_items(turn_id, input_text(), voice)
            else:
                assert selected_provider is not None
                stream = selected_provider.stream(input_text(), voice)
            async for item in stream:
                if isinstance(item, TTSFormat):
                    audio_format = item
                    yield encode_control(
                        "format",
                        turn_id,
                        codec=item.codec,
                        rate=item.rate,
                        channels=item.channels,
                        width=item.width,
                    )
                elif isinstance(item, TTSAudio):
                    if not pending_audio and origin_ns is not None:
                        first_sentence_ms = (
                            time.monotonic_ns() - origin_ns
                        ) // 1_000_000
                    pending_audio = True
                    sentence_bytes += len(item.data)
                    total_bytes += len(item.data)
                    if not first_audio:
                        first_audio = True
                        if record is not None:
                            await record("tts_first_byte", metadata)
                    yield item.data
                    # The caller sends the bytes before it requests the next item.
                    last_audio_ns = time.time_ns()
                elif isinstance(item, TTSSentenceEnd):
                    await sentence_note(item.sentence, item.text_end)
                    fields = (
                        {"text_end": item.text_end}
                        if text_positions and item.text_end is not None
                        else {}
                    )
                    yield encode_control(
                        "sentence_end", turn_id, sentence=item.sentence, **fields
                    )
                    pending_audio = False
                    sentence = item.sentence + 1
            if has_text and not first_audio:
                raise ValueError("TTS audio required")
            if pending_audio:
                # Without alignment, only the end of the remaining audio is known.
                fields = {"text_end": received} if text_positions else {}
                yield encode_control(
                    "sentence_end", turn_id, sentence=sentence, **fields
                )
                await sentence_note(sentence, received if text_positions else None)
                sentence += 1
            if record is not None:
                await record(
                    "tts_last_byte",
                    {
                        "request": request,
                        "sentences": sentence,
                        "audio_ms": duration(total_bytes),
                        "t_utc_ns": last_audio_ns
                        if last_audio_ns is not None
                        else time.time_ns(),
                    },
                )
        except ValueError, RuntimeError:
            if record is not None:
                await record("tts_failed", metadata)
            yield encode_control("error", turn_id, code=failure_code)
        finally:
            if stream is not None and hasattr(stream, "aclose"):
                await stream.aclose()

"""Select a TTS destination before the text iterator can supply content."""

import json
from collections.abc import AsyncIterator, Callable
from typing import Any

from assist_live_client.models import TTSAudio, TTSFormat, TTSItem, TTSSentenceEnd
from assist_live_client.protocol import MAX_PCM, decode_control, encode_control

from .audio_gateway import Recorder
from .destinations import Destination, require_destination
from .protocol import AudioBackend
from .providers.tts import TTSProvider


class TTSGateway:
    def __init__(
        self,
        provider_factory: Callable[[str], TTSProvider] | None = None,
        *,
        provider: str = "piper",
        destination: Destination | None = None,
        local_only: bool = True,
        backend: AudioBackend | None = None,
    ) -> None:
        self.provider_factory = provider_factory
        self.provider = provider
        self.destination: Destination = "local" if destination is None else destination
        self.declared_destination = destination
        self.local_only = local_only
        self.backend = backend
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
    ) -> AsyncIterator[dict[str, Any] | bytes]:
        metadata: dict[str, object] = {
            "part": "tts",
            "provider": self.provider,
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

        async def input_text() -> AsyncIterator[str]:
            nonlocal first_text, has_text, received
            async for chunk in text:
                received += len(chunk)
                has_text = has_text or bool(chunk.strip())
                if chunk and not first_text:
                    first_text = True
                    if record is not None:
                        await record("tts_first_text", metadata)
                yield chunk

        try:
            require_destination(self.destination, self.local_only)
            if self.backend is not None:
                stream = self._backend_items(turn_id, input_text(), voice)
            else:
                assert self.provider_factory is not None
                selected = self.provider_factory(self.provider)
                stream = selected.stream(input_text(), voice)
            async for item in stream:
                if isinstance(item, TTSFormat):
                    yield encode_control(
                        "format",
                        turn_id,
                        codec=item.codec,
                        rate=item.rate,
                        channels=item.channels,
                        width=item.width,
                    )
                elif isinstance(item, TTSAudio):
                    pending_audio = True
                    if not first_audio:
                        first_audio = True
                        if record is not None:
                            await record("tts_first_byte", metadata)
                    yield item.data
                elif isinstance(item, TTSSentenceEnd):
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
        except ValueError, RuntimeError:
            if record is not None:
                await record("tts_failed", metadata)
            yield encode_control("error", turn_id, code="tts_failed")
        finally:
            if stream is not None and hasattr(stream, "aclose"):
                await stream.aclose()

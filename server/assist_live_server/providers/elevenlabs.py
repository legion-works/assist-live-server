"""Explicit cloud TTS with request positions and provider alignment."""

import asyncio
import base64
import json
from collections import deque
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import suppress
from time import monotonic

import aiohttp
from assist_live_client.models import TTSAudio, TTSFormat, TTSItem, TTSSentenceEnd
from assist_live_client.protocol import MAX_PCM
from yarl import URL

from .tts import SentenceAssembler

MAX_TEXT_CHARACTERS = 16384
STREAM_MARGIN_SECONDS = 10.0
CHARACTERS_PER_SECOND = 8.0
MAX_ALIGNMENT_CHARACTERS = 4096
MAX_ALIGNMENT_BYTES = 65536


class ElevenLabsError(RuntimeError):
    """The cloud operation failed without provider response details."""


class ElevenLabsProvider:
    provider_id = "elevenlabs"
    destination = "cloud"

    def __init__(
        self,
        api_key: str,
        *,
        endpoint: str = "wss://api.elevenlabs.io",
        timeout: float = 15.0,
        enable_logging: bool = True,
    ) -> None:
        if not api_key or any(c in api_key for c in "\r\n"):
            raise ValueError("invalid ElevenLabs credential")
        url = None
        try:
            url = URL(endpoint)
            _ = url.host
        except ValueError:
            url = None
        if (
            url is None
            or url.scheme not in {"http", "https", "ws", "wss"}
            or (url.scheme in {"http", "ws"} and url.host not in {"127.0.0.1", "::1"})
            or not url.host
            or url.user is not None
            or url.query_string
            or url.fragment
            or api_key in endpoint
            or timeout <= 0
            or not isinstance(enable_logging, bool)
        ):
            raise ValueError("invalid ElevenLabs endpoint or timeout")
        self._api_key = api_key
        self._endpoint = url
        self._timeout = timeout
        self._enable_logging = enable_logging
        self.audio_format = TTSFormat("pcm_s16le", 16000, 1, 2)

    async def stream(
        self, text: AsyncIterator[str], voice: str
    ) -> AsyncGenerator[TTSItem]:
        if (
            not voice
            or self._api_key in voice
            or not all(c.isascii() and (c.isalnum() or c in "-_") for c in voice)
        ):
            raise ValueError("invalid ElevenLabs voice identifier")
        audio_format = self.audio_format
        yield audio_format
        started = monotonic()
        deadline = started + STREAM_MARGIN_SECONDS
        sent_characters = 0
        bytes_per_second = (
            audio_format.rate * audio_format.width * audio_format.channels
        )
        audio_limit = int(STREAM_MARGIN_SECONDS * bytes_per_second)
        failed = False
        trace = aiohttp.TraceConfig()

        async def refuse_redirect(
            session: aiohttp.ClientSession,
            context: object,
            params: aiohttp.TraceRequestRedirectParams,
        ) -> None:
            raise ElevenLabsError("ElevenLabs redirect refused")

        trace.on_request_redirect.append(refuse_redirect)
        client = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=self._timeout),
            trust_env=False,
            trace_configs=[trace],
        )
        ws: aiohttp.ClientWebSocketResponse | None = None
        writer: asyncio.Task[None] | None = None
        complete = False
        input_ended = False
        aligned = False
        assembler = SentenceAssembler()
        request_assembler = SentenceAssembler()
        positions: deque[int] = deque()
        aligned_sentences = 0
        sentence = 0
        audio_bytes = 0
        alignment_bytes = 0
        try:
            ws = await client.ws_connect(
                self._endpoint / "v1" / "text-to-speech" / voice / "stream-input",
                params={
                    "model_id": "eleven_flash_v2_5",
                    "output_format": f"pcm_{audio_format.rate}",
                    "enable_logging": str(self._enable_logging).lower(),
                },
                headers={"xi-api-key": self._api_key},
                timeout=aiohttp.ClientWSTimeout(ws_receive=self._timeout, ws_close=1.0),
                max_msg_size=262144,
            )
            await ws.send_json(
                {
                    "text": " ",
                    "generation_config": {"chunk_length_schedule": [50]},
                }
            )

            async def send() -> None:
                nonlocal failed, input_ended, sent_characters, deadline, audio_limit
                assert ws is not None
                try:
                    async for part in text:
                        if part:
                            if sent_characters + len(part) > MAX_TEXT_CHARACTERS:
                                raise ValueError("ElevenLabs text limit exceeded")
                            positions.extend(
                                end for _, end in request_assembler.feed_positions(part)
                            )
                            await ws.send_json({"text": part})
                            sent_characters += len(part)
                            allowance = (
                                STREAM_MARGIN_SECONDS
                                + sent_characters / CHARACTERS_PER_SECOND
                            )
                            deadline = started + allowance
                            audio_limit = int(allowance * bytes_per_second)
                    if "".join(request_assembler.buffer).strip():
                        positions.append(request_assembler.received)
                    await ws.send_json({"text": " ", "flush": True})
                    await ws.send_json({"text": ""})
                    input_ended = True
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # Close wakes the receiver. Only the static error flag crosses
                    # the task boundary, not an input or transport exception.
                    failed = True
                    with suppress(Exception):
                        await ws.close()
                finally:
                    close = getattr(text, "aclose", None)
                    if close is not None:
                        try:
                            await close()
                        except Exception:
                            failed = True
                            with suppress(Exception):
                                await ws.close()

            writer = asyncio.create_task(send())
            while True:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise ValueError("ElevenLabs stream deadline exceeded")
                try:
                    message = await ws.receive(timeout=min(self._timeout, remaining))
                except TimeoutError:
                    if remaining < self._timeout and monotonic() < deadline:
                        continue
                    raise
                if monotonic() >= deadline:
                    raise ValueError("ElevenLabs stream deadline exceeded")
                if message.type != aiohttp.WSMsgType.TEXT:
                    raise ValueError("invalid ElevenLabs message type")
                value = json.loads(message.data)
                if "error" in value:
                    raise ValueError("ElevenLabs rejected input")
                if value.get("audio"):
                    pcm = base64.b64decode(value["audio"], validate=True)
                    if len(pcm) % 2:
                        raise ValueError("invalid ElevenLabs PCM alignment")
                    audio_bytes += len(pcm)
                    if audio_bytes > audio_limit:
                        raise ValueError("ElevenLabs audio limit exceeded")
                    for offset in range(0, len(pcm), MAX_PCM):
                        if monotonic() >= deadline:
                            raise ValueError("ElevenLabs stream deadline exceeded")
                        yield TTSAudio(pcm[offset : offset + MAX_PCM])
                alignment = value.get("alignment") or value.get("normalizedAlignment")
                if alignment is not None:
                    chars = alignment["chars"]
                    if (
                        not isinstance(chars, list)
                        or len(chars) > MAX_ALIGNMENT_CHARACTERS
                        or not all(isinstance(c, str) and len(c) == 1 for c in chars)
                        or len(alignment["charStartTimesMs"]) != len(chars)
                        or len(alignment["charDurationsMs"]) != len(chars)
                    ):
                        raise ValueError("invalid ElevenLabs alignment")
                    alignment_text = "".join(chars)
                    alignment_bytes += len(alignment_text.encode("utf-8"))
                    if alignment_bytes > MAX_ALIGNMENT_BYTES:
                        raise ValueError("ElevenLabs alignment limit exceeded")
                    aligned = True
                    # Alignment can change spelling. Only request positions index
                    # the original text. Alignment controls when markers leave.
                    aligned_sentences += len(assembler.feed(alignment_text))
                    while positions and sentence < aligned_sentences:
                        if monotonic() >= deadline:
                            raise ValueError("ElevenLabs stream deadline exceeded")
                        yield TTSSentenceEnd(sentence, positions.popleft())
                        sentence += 1
                if value.get("isFinal") is True:
                    complete = True
                    if aligned and "".join(assembler.buffer).strip():
                        aligned_sentences += 1
                    while positions and sentence < aligned_sentences:
                        yield TTSSentenceEnd(sentence, positions.popleft())
                        sentence += 1
                    if sentence != aligned_sentences:
                        raise ValueError(
                            "ElevenLabs alignment exceeds request sentences"
                        )
                    break
            if not complete:
                failed = True
            if complete and not input_ended:
                # A final response before input end must not wait for more text.
                failed = True
            if complete and input_ended:
                async with asyncio.timeout(self._timeout):
                    await asyncio.shield(writer)
        except asyncio.CancelledError:
            raise
        except Exception:
            failed = True
        finally:
            if writer is not None:
                if not writer.done():
                    writer.cancel()
                results = await asyncio.gather(writer, return_exceptions=True)
                if any(isinstance(value, Exception) for value in results):
                    failed = True
            if ws is not None:
                with suppress(Exception):
                    await ws.close()
            with suppress(Exception):
                await client.close()
        if failed:
            raise ElevenLabsError("ElevenLabs stream failed")

    async def synthesize(
        self, text: AsyncIterator[str], voice: str
    ) -> AsyncIterator[bytes]:
        stream = self.stream(text, voice)
        try:
            async for item in stream:
                if isinstance(item, TTSAudio):
                    yield item.data
        finally:
            await stream.aclose()

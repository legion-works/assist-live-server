import asyncio
import io
import json
import math
import os
import wave
from collections.abc import AsyncGenerator, AsyncIterator
from pathlib import Path
from time import monotonic

import aiohttp
import numpy as np
from assist_live_client.models import (
    TTSAudio,
    TTSFormat,
    TTSItem,
    TTSSentenceEnd,
    TTSVoice,
)
from assist_live_client.protocol import MAX_PCM

from ..destinations import (
    PinnedResolver,
    Resolver,
    checked_addresses,
    resolve_addresses,
    speaker_url,
)
from .tts import sentences

VOICE_CACHE_SECONDS = 60.0
METADATA_TIMEOUT = 0.1
MAX_WAV_BYTES = 16 * 1024 * 1024


class OpenAITTSError(RuntimeError):
    pass


class OpenAITTSProvider:
    provider_id = "openai_tts"

    def __init__(self, base_url: str, *, local_only: bool, resolver: Resolver) -> None:
        self._url = speaker_url(base_url)
        self._local_only = local_only
        self._resolver = resolver
        self.default_voice = os.environ.get("OPENAI_TTS_DEFAULT_VOICE", "")
        self.voices: tuple[TTSVoice, ...] = ()
        self._next_refresh = 0.0
        self._api_key = ""
        key_file = os.environ.get("OPENAI_TTS_API_KEY_FILE")
        try:
            if key_file:
                with Path(key_file).open("rb") as source:
                    raw = source.read(4097)
                self._api_key = raw.decode("ascii").strip()
                if not 0 < len(raw) <= 4096 or not self._api_key:
                    raise ValueError
                if any(not 32 <= ord(c) <= 126 for c in self._api_key):
                    raise ValueError
            self._timeout = float(os.environ.get("OPENAI_TTS_TIMEOUT", "15"))
            if not math.isfinite(self._timeout) or self._timeout <= 0:
                raise ValueError
            self._allowlist = tuple(
                v.strip()
                for v in os.environ.get("OPENAI_TTS_VOICES", "").split(",")
                if v.strip()
            )
            if not self._valid_voice(self.default_voice) or (
                self._allowlist and self.default_voice not in self._allowlist
            ):
                raise ValueError("invalid openai_tts default voice")
            if any(not self._valid_voice(v) for v in self._allowlist):
                raise ValueError
            if self._api_key and self._api_key in str(self._url):
                raise ValueError
        except OSError, UnicodeError, ValueError:
            raise ValueError(
                "invalid openai_tts key, timeout, or default voice configuration"
            ) from None
        self.voices = tuple(
            TTSVoice(v, v, "und")
            for v in dict.fromkeys(self._allowlist or (self.default_voice,))
        )

    def _valid_voice(self, value: object) -> bool:
        return (
            isinstance(value, str)
            and 0 < len(value) <= 128
            and bool(value.strip())
            and not any(ord(c) < 32 or ord(c) == 127 for c in value)
            and not (self._api_key and self._api_key in value)
        )

    async def _request(self, path: str, payload: dict[str, str] | None = None) -> bytes:
        try:
            async with asyncio.timeout(self._timeout if payload is not None else 2.0):
                try:
                    addresses, _ = await checked_addresses(
                        self._url, local_only=self._local_only, resolver=self._resolver
                    )
                except ValueError:
                    raise OpenAITTSError("openai_tts_destination_refused") from None
                connector = aiohttp.TCPConnector(
                    resolver=PinnedResolver(self._url.host or "", (addresses[0],)),
                    use_dns_cache=False,
                )
                async with aiohttp.ClientSession(
                    connector=connector, trust_env=False, auto_decompress=False
                ) as client:
                    async with client.request(
                        "GET" if payload is None else "POST",
                        self._url / path,
                        json=payload,
                        headers={"Authorization": "Bearer " + self._api_key}
                        if self._api_key
                        else {},
                        allow_redirects=False,
                    ) as response:
                        if response.status != 200:
                            raise OpenAITTSError("openai_tts_http_error")
                        limit = MAX_WAV_BYTES if payload is not None else 65536
                        raw = bytearray()
                        async for chunk in response.content.iter_chunked(65536):
                            if len(raw) + len(chunk) > limit:
                                raise OpenAITTSError("openai_tts_response_too_large")
                            raw.extend(chunk)
                        return bytes(raw)
        except TimeoutError:
            raise OpenAITTSError("openai_tts_timeout") from None
        except aiohttp.ClientError, OSError:
            raise OpenAITTSError("openai_tts_connection_failed") from None

    @classmethod
    async def load(
        cls,
        cache: Path | str,
        *,
        base_url: str,
        local_only: bool = True,
        resolver: Resolver = resolve_addresses,
    ) -> OpenAITTSProvider:
        provider = cls(base_url, local_only=local_only, resolver=resolver)
        await provider.refresh_voices(initial=True)
        return provider

    async def refresh_voices(self, *, initial: bool = False) -> None:
        if monotonic() < self._next_refresh:
            return
        self._next_refresh = monotonic() + VOICE_CACHE_SECONDS
        try:
            async with asyncio.timeout(2.0 if initial else METADATA_TIMEOUT):
                value = json.loads(await self._request("v1/audio/voices"))
            names = value["voices"]
            if not isinstance(names, list) or not 0 < len(names) <= 256:
                raise ValueError
            if any(not self._valid_voice(v) for v in names) or len(set(names)) != len(
                names
            ):
                raise ValueError
            selected = [v for v in names if not self._allowlist or v in self._allowlist]
            if self.default_voice not in selected:
                raise ValueError("invalid openai_tts default voice")
            self.voices = tuple(TTSVoice(v, v, "und") for v in selected)
        except TimeoutError, OpenAITTSError:
            return
        except ValueError, TypeError, KeyError:
            if initial:
                raise ValueError(
                    "invalid openai_tts voice list or default voice"
                ) from None

    def _pcm(self, raw: bytes) -> tuple[TTSFormat, bytes]:
        try:
            if int.from_bytes(raw[4:8], "little") != len(raw) - 8:
                raise ValueError
            with wave.open(io.BytesIO(raw), "rb") as wav:
                if (
                    wav.getnchannels() != 1
                    or wav.getsampwidth() != 2
                    or wav.getcomptype() != "NONE"
                    or not 8000 <= wav.getframerate() <= 192000
                    or wav.getnframes() == 0
                ):
                    raise ValueError
                audio_format = TTSFormat("pcm_s16le", wav.getframerate(), 1, 2)
                pcm = wav.readframes(wav.getnframes() + 1)
                if len(pcm) != wav.getnframes() * 2:
                    raise ValueError
                return audio_format, pcm
        except wave.Error, EOFError, ValueError:
            raise OpenAITTSError("openai_tts_bad_wav") from None

    def _resample(self, pcm: bytes, rate: int) -> bytes:
        samples = np.frombuffer(pcm, dtype="<i2").astype(np.float64)
        ratio = 22050 / rate
        half = math.ceil(64 / min(1.0, ratio))
        offsets = np.arange(-half, half + 1)
        cutoff = 0.94 * min(1.0, ratio)
        kernel = cutoff * np.sinc(cutoff * offsets) * np.kaiser(len(offsets), 8.6)
        kernel /= kernel.sum()
        # The filter decreases energy above the new Nyquist limit before decimation.
        # Edge padding keeps the sentence length.
        filtered = np.convolve(
            np.pad(samples, (half, half), mode="edge"), kernel, mode="valid"
        )
        size = max(1, round(len(samples) * ratio))
        positions = np.arange(size) / ratio
        result = np.interp(positions, np.arange(len(samples)), filtered)
        return np.clip(np.rint(result), -32768, 32767).astype("<i2").tobytes()

    async def stream(
        self, text: AsyncIterator[str], voice: str
    ) -> AsyncGenerator[TTSItem]:
        await self.refresh_voices()
        if voice not in {v.id for v in self.voices}:
            raise ValueError("unknown TTS voice")
        sentence_stream = sentences(text)
        try:
            index = 0
            reply_rate = None
            async for sentence, text_end in sentence_stream:
                raw = await self._request(
                    "v1/audio/speech", {"input": sentence, "voice": voice}
                )
                audio_format, pcm = self._pcm(raw)
                rate = audio_format.rate
                if reply_rate is not None and reply_rate != rate:
                    raise OpenAITTSError("openai_tts_rate_changed")
                if rate not in {16000, 22050}:
                    pcm = await asyncio.to_thread(self._resample, pcm, rate)
                    audio_format = TTSFormat("pcm_s16le", 22050, 1, 2)
                if reply_rate is None:
                    reply_rate = rate
                    yield audio_format
                for offset in range(0, len(pcm), MAX_PCM):
                    yield TTSAudio(pcm[offset : offset + MAX_PCM])
                yield TTSSentenceEnd(index, text_end)
                index += 1
        finally:
            await sentence_stream.aclose()

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

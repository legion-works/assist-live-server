import asyncio
import io
import json
import logging
import math
import os
import re
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
MAX_SENTENCE_SECONDS = 60
MAX_WAV_BYTES = MAX_SENTENCE_SECONDS * 24000 * 2 + 65536
RESAMPLE_BLOCK = 256
SINC_TAPS = 128
LOGGER = logging.getLogger(__name__)


class OpenAITTSError(RuntimeError):
    pass


# A language tag such as en, de, en-US, or en_US. The HTTP route gives no language.
LANGUAGE = re.compile(r"[a-z]{2,3}(?:[-_][A-Za-z0-9]{2,8}){0,3}")


class OpenAITTSProvider:
    provider_id = "openai_tts"

    def __init__(self, base_url: str, *, local_only: bool, resolver: Resolver) -> None:
        self._url = speaker_url(base_url)
        self._local_only = local_only
        self._resolver = resolver
        self.default_voice = os.environ.get("OPENAI_TTS_DEFAULT_VOICE", "")
        self.language = os.environ.get("OPENAI_TTS_LANGUAGE", "en")
        self.voices: tuple[TTSVoice, ...] = ()
        self._next_refresh = 0.0
        self.invalid_voice_names_total = 0
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
            if not LANGUAGE.fullmatch(self.language):
                raise ValueError
            self._timeout = float(os.environ.get("OPENAI_TTS_TIMEOUT", "15"))
            if not math.isfinite(self._timeout) or self._timeout <= 0:
                raise ValueError
            self._allowlist = tuple(
                v.strip()
                for v in os.environ.get("OPENAI_TTS_VOICES", "").split(",")
                if v.strip()
            )
            invalid = sum(
                not self._valid_voice(v) for v in (self.default_voice, *self._allowlist)
            )
            if invalid:
                self.invalid_voice_names_total += invalid
                LOGGER.warning("openai_tts_invalid_voice_names count=%d", invalid)
                raise ValueError
            if self._allowlist and self.default_voice not in self._allowlist:
                raise ValueError("invalid openai_tts default voice")
            if any(not self._valid_voice(v) for v in self._allowlist):
                raise ValueError
            if self._api_key and self._api_key in str(self._url):
                raise ValueError
        except OSError, UnicodeError, ValueError:
            raise ValueError(
                "invalid openai_tts key, timeout, language, default voice, "
                "or permitted voice configuration"
            ) from None
        self.voices = tuple(
            TTSVoice(v, v, self.language)
            for v in dict.fromkeys(self._allowlist or (self.default_voice,))
        )
        if self._api_key and self._url.scheme == "http":
            LOGGER.warning("openai_tts bearer key uses cleartext HTTP")

    def _valid_voice(self, value: object) -> bool:
        return (
            isinstance(value, str)
            and 0 < len(value) <= 128
            and bool(value.strip())
            and all(c.isascii() and (c.isalnum() or c in " ._-") for c in value)
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
            invalid = sum(not self._valid_voice(v) for v in names)
            if invalid:
                self.invalid_voice_names_total += invalid
                LOGGER.warning("openai_tts_invalid_voice_names count=%d", invalid)
                raise ValueError
            if len(set(names)) != len(names):
                raise ValueError
            selected = [v for v in names if not self._allowlist or v in self._allowlist]
            if self.default_voice not in selected:
                raise ValueError("invalid openai_tts default voice")
            self.voices = tuple(TTSVoice(v, v, self.language) for v in selected)
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
                    or wav.getnframes() > MAX_SENTENCE_SECONDS * wav.getframerate()
                ):
                    raise ValueError
                audio_format = TTSFormat("pcm_s16le", wav.getframerate(), 1, 2)
                pcm = wav.readframes(wav.getnframes() + 1)
                if len(pcm) != wav.getnframes() * 2:
                    raise ValueError
                return audio_format, pcm
        except wave.Error, EOFError, ValueError, RuntimeError:
            raise OpenAITTSError("openai_tts_bad_wav") from None

    def _resample(self, pcm: bytes, rate: int) -> bytes:
        samples = np.frombuffer(pcm, dtype="<i2")
        size = max(1, round(len(samples) * 22050 / rate))
        result = np.empty(size, dtype="<i2")
        offsets = np.arange(1 - SINC_TAPS // 2, 1 + SINC_TAPS // 2)
        window = np.kaiser(SINC_TAPS, 8.6).astype(np.float32)
        cutoff = np.float32(0.94 * min(1.0, 22050 / rate))
        # Each group of output samples reads its full input overlap.
        # Integer phase keeps sample positions the same at group boundaries.
        for start in range(0, size, RESAMPLE_BLOCK):
            stop = min(start + RESAMPLE_BLOCK, size)
            phase = np.arange(start, stop, dtype=np.int64) * rate
            centers = phase // 22050
            fractions = (phase % 22050).astype(np.float32) / np.float32(22050)
            distances = offsets.astype(np.float32)[None, :] - fractions[:, None]
            weights = np.sinc(cutoff * distances)
            weights *= cutoff * window
            weights /= weights.sum(axis=1, keepdims=True)
            indices = centers[:, None] + offsets
            np.clip(indices, 0, len(samples) - 1, out=indices)
            values = samples[indices].astype(np.float32)
            output = np.einsum("ij,ij->i", weights, values, dtype=np.float32)
            result[start:stop] = np.clip(np.rint(output), -32768, 32767).astype("<i2")
        return result.tobytes()

    async def stream(
        self, text: AsyncIterator[str], voice: str
    ) -> AsyncGenerator[TTSItem]:
        await self.refresh_voices()
        if voice not in {v.id for v in self.voices}:
            raise ValueError("unknown TTS voice")
        # The provider sends the reply format before the first synthesis request.
        # A caller can then start playback before it gets the first sentence audio.
        yield TTSFormat("pcm_s16le", 22050, 1, 2)
        sentence_stream = sentences(text)
        try:
            index = 0
            async for sentence, text_end in sentence_stream:
                raw = await self._request(
                    "v1/audio/speech", {"input": sentence, "voice": voice}
                )
                audio_format, pcm = self._pcm(raw)
                # The resampler changes each sentence on its own.
                # Thus sentences of one reply can have different source rates.
                if audio_format.rate != 22050:
                    pcm = await asyncio.to_thread(
                        self._resample, pcm, audio_format.rate
                    )
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

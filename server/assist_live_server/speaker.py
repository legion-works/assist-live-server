"""Optional HTTP speaker metadata. Audio stays in memory for one request."""

import io
import math
import wave
from dataclasses import dataclass
from typing import Protocol

import aiohttp
from assist_live_client.protocol import MAX_CONTROL, parse_json

from .destinations import (
    Destination,
    PinnedResolver,
    Resolver,
    checked_addresses,
    classify_address,
    resolve_addresses,
    speaker_url,
)
from .providers.parakeet import MAX_AUDIO_BYTES


@dataclass(frozen=True)
class SpeakerResult:
    name: str
    distance: float

    def __post_init__(self) -> None:
        if (
            not isinstance(self.name, str)
            or len(self.name) > 64
            or len(self.name.encode()) > 256
            or isinstance(self.distance, bool)
            or not isinstance(self.distance, (float, int))
            or not math.isfinite(self.distance)
            or not 0 <= self.distance <= 1
        ):
            raise ValueError("invalid speaker result")


class SpeakerPlugin(Protocol):
    destination: Destination

    async def identify(self, audio: bytes) -> SpeakerResult: ...


class MurdockSpeaker:
    def __init__(
        self,
        destination: str,
        *,
        local_only: bool = True,
        resolver: Resolver = resolve_addresses,
        mode: Destination = "local",
    ) -> None:
        self.url = speaker_url(destination)
        self.local_only = local_only
        self.resolver = resolver
        self.destination = mode

    async def identify(self, audio: bytes) -> SpeakerResult:
        if len(audio) > MAX_AUDIO_BYTES or len(audio) % 2:
            raise ValueError("invalid speaker audio")
        addresses, _ = await checked_addresses(
            self.url, local_only=self.local_only, resolver=self.resolver
        )
        # One pinned address makes the recorded destination the connection class.
        address = addresses[0]
        destination = classify_address(address)
        # A new connector prevents reuse of an earlier connection or DNS answer.
        connector = aiohttp.TCPConnector(
            resolver=PinnedResolver(self.url.host or "", (address,)),
            use_dns_cache=False,
        )
        content = io.BytesIO()
        with wave.open(content, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(audio)
        form = aiohttp.FormData()
        form.add_field(
            "audio", content.getvalue(), filename="audio.wav", content_type="audio/wav"
        )
        async with aiohttp.ClientSession(
            connector=connector,
            timeout=aiohttp.ClientTimeout(total=5),
            trust_env=False,
            auto_decompress=False,
        ) as client:
            async with client.post(
                self.url,
                data=form,
                allow_redirects=False,
                server_hostname=self.url.host,
            ) as response:
                if response.status != 200:
                    raise ValueError("speaker request refused")
                raw = bytearray()
                async for chunk in response.content.iter_chunked(1024):
                    if len(raw) + len(chunk) > MAX_CONTROL:
                        raise ValueError("speaker response too large")
                    raw.extend(chunk)
        value = parse_json(bytes(raw), MAX_CONTROL)
        if type(value.get("is_match")) is not bool:
            raise ValueError("invalid speaker match")
        result = SpeakerResult(
            value["matched_speaker"] if value["is_match"] else "",
            value["distance"],
        )
        self.destination = destination
        return result

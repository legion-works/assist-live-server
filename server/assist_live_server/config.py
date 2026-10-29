"""Load explicit provider selections before any content can leave the server."""

import os
from collections import ChainMap
from dataclasses import dataclass
from pathlib import Path

from assist_live_client.models import TTSVoice

from .destinations import (
    Destination,
    Resolver,
    checked_addresses,
    literal_destination,
    require_destination,
    resolve_addresses,
)
from .destinations import (
    speaker_url as parse_speaker_url,
)
from .providers import stt as stt_registry
from .providers import tts as tts_registry
from .providers.stt import STTProvider, STTSession
from .providers.tts import TTSProvider
from .speaker import MurdockSpeaker

PROVIDER_DESTINATIONS = ChainMap(
    stt_registry.PROVIDER_DESTINATIONS, tts_registry.PROVIDER_DESTINATIONS
)


def provider_destination(provider: str) -> Destination:
    try:
        return PROVIDER_DESTINATIONS[provider]
    except KeyError:
        raise ValueError("unknown provider") from None


@dataclass(frozen=True)
class ServerConfig:
    stt_provider: str | None = "parakeet"
    tts_provider: str | None = "piper"
    local_only: bool = True
    record_transcripts: bool = False
    speaker_url: str | None = None

    def __post_init__(self) -> None:
        if (
            type(self.local_only) is not bool
            or type(self.record_transcripts) is not bool
        ):
            raise ValueError("invalid server option")
        for provider, names in (
            (self.stt_provider, stt_registry.provider_names()),
            (self.tts_provider, tts_registry.provider_names()),
        ):
            if provider is not None:
                if provider not in names:
                    raise ValueError("unknown provider")
                require_destination(provider_destination(provider), self.local_only)
        if self.speaker_url is not None:
            parse_speaker_url(self.speaker_url)

    @classmethod
    async def load(
        cls,
        *,
        stt_provider: str | None = "parakeet",
        tts_provider: str | None = "piper",
        local_only: bool = True,
        record_transcripts: bool = False,
        speaker_url: str | None = None,
        resolver: Resolver = resolve_addresses,
    ) -> ServerConfig:
        config = cls(
            stt_provider, tts_provider, local_only, record_transcripts, speaker_url
        )
        if speaker_url is not None:
            await checked_addresses(
                parse_speaker_url(speaker_url),
                local_only=local_only,
                resolver=resolver,
            )
        return config


class ProviderFactory:
    def __init__(
        self,
        config: ServerConfig,
        stt: STTProvider | None = None,
        tts: TTSProvider | None = None,
        speaker: MurdockSpeaker | None = None,
    ) -> None:
        self.config = config
        self._stt = stt
        self._tts = tts
        self.speaker = speaker

    @property
    def tts_voices(self) -> tuple[TTSVoice, ...]:
        if self._tts is not None:
            return getattr(self._tts, "voices", ())
        if self.config.tts_provider == "piper":
            from .providers.piper import PiperProvider

            return PiperProvider.voices
        return ()

    @property
    def tts_default_voice(self) -> str | None:
        if self._tts is not None:
            return getattr(self._tts, "default_voice", None)
        if self.config.tts_provider == "piper":
            from .providers.piper import PiperProvider

            return PiperProvider.default_voice
        return None

    @classmethod
    async def load(
        cls,
        config: ServerConfig,
        *,
        cache: Path | str = ".",
        resolver: Resolver = resolve_addresses,
    ) -> ProviderFactory:
        # Validate even a directly constructed config before model or cloud setup.
        config = await ServerConfig.load(
            stt_provider=config.stt_provider,
            tts_provider=config.tts_provider,
            local_only=config.local_only,
            record_transcripts=config.record_transcripts,
            speaker_url=config.speaker_url,
            resolver=resolver,
        )
        stt: STTProvider | None = None
        tts: TTSProvider | None = None
        if config.stt_provider is not None:
            stt = await stt_registry.provider_factory(config.stt_provider)(
                os.environ.get(f"{config.stt_provider.upper()}_API_KEY", "")
                if provider_destination(config.stt_provider) == "cloud"
                else cache
            )
        if config.tts_provider is not None:
            tts = await tts_registry.provider_factory(config.tts_provider)(
                os.environ.get(f"{config.tts_provider.upper()}_API_KEY", "")
                if provider_destination(config.tts_provider) == "cloud"
                else cache
            )
        speaker = None
        if config.speaker_url is not None:
            _, mode = await checked_addresses(
                parse_speaker_url(config.speaker_url),
                local_only=config.local_only,
                resolver=resolver,
            )
            speaker = MurdockSpeaker(
                config.speaker_url,
                local_only=config.local_only,
                resolver=resolver,
                mode=mode,
            )
            speaker.checked_destination = literal_destination(speaker.url, mode)
        return cls(config, stt, tts, speaker)

    def stt(self, provider: str) -> STTSession:
        require_destination(provider_destination(provider), self.config.local_only)
        if provider != self.config.stt_provider or self._stt is None:
            raise ValueError("STT provider not selected")
        return self._stt.session()

    def tts(self, provider: str) -> TTSProvider:
        require_destination(provider_destination(provider), self.config.local_only)
        if provider != self.config.tts_provider or self._tts is None:
            raise ValueError("TTS provider not selected")
        return self._tts

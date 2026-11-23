import asyncio

import pytest
from aiohttp import web

from .test_openai_tts import configured, speech_server
from .test_openai_tts import loopback_only as loopback_only


def test_voices_default_to_english_so_an_en_pipeline_matches(monkeypatch):
    monkeypatch.delenv("OPENAI_TTS_LANGUAGE", raising=False)

    async def run():
        async with speech_server() as (url, _, _):
            factory = await configured(monkeypatch, url)
            assert factory.tts_voices
            assert {v.language for v in factory.tts_voices} == {"en"}

    asyncio.run(run())


def test_configured_language_applies_to_seed_and_refreshed_voices(monkeypatch):
    monkeypatch.setenv("OPENAI_TTS_LANGUAGE", "de-DE")
    monkeypatch.setenv("OPENAI_TTS_VOICES", "voice-a,voice-b")

    async def catalog(request, count):
        return web.json_response({"voices": ["voice-a", "voice-b", "voice-c"]})

    async def run():
        async with speech_server(catalog_handler=catalog) as (url, _, _):
            factory = await configured(monkeypatch, url)
            assert [(v.id, v.language) for v in factory.tts_voices] == [
                ("voice-a", "de-DE"),
                ("voice-b", "de-DE"),
            ]
            monkeypatch.setattr(
                "assist_live_server.providers.openai_tts.monotonic",
                lambda: 1000000000000.0,
            )
            await factory.refresh_tts_voices()
            assert {v.language for v in factory.tts_voices} == {"de-DE"}

    asyncio.run(run())


def test_seed_voices_carry_language_when_catalog_is_unavailable(monkeypatch):
    monkeypatch.setenv("OPENAI_TTS_LANGUAGE", "pt-BR")
    monkeypatch.setenv("OPENAI_TTS_VOICES", "voice-a,voice-b")

    async def run():
        async with speech_server() as (url, _, _):
            pass
        factory = await configured(monkeypatch, url)
        assert [(v.id, v.language) for v in factory.tts_voices] == [
            ("voice-a", "pt-BR"),
            ("voice-b", "pt-BR"),
        ]

    asyncio.run(run())


@pytest.mark.parametrize("value", ["en_US", "pt-BR", "zh-Hant-TW", "und"])
def test_common_language_tags_are_accepted(monkeypatch, value):
    monkeypatch.setenv("OPENAI_TTS_LANGUAGE", value)

    async def run():
        async with speech_server() as (url, _, _):
            factory = await configured(monkeypatch, url)
            assert {v.language for v in factory.tts_voices} == {value}

    asyncio.run(run())


@pytest.mark.parametrize(
    "value",
    ["", "EN", "english", "en us", "e", "en-", "en-US-x-y-z-w", "en\u200b", "en\n"],
)
def test_invalid_language_is_a_named_configuration_error(monkeypatch, value):
    monkeypatch.setenv("OPENAI_TTS_LANGUAGE", value)

    async def run():
        async with speech_server() as (url, _, _):
            with pytest.raises(ValueError, match="language"):
                await configured(monkeypatch, url)

    asyncio.run(run())

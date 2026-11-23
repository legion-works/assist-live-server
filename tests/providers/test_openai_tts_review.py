import asyncio
import logging
import tracemalloc

import numpy as np
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from assist_live_client.models import TTSAudio, TTSSentenceEnd
from assist_live_server.providers.openai_tts import OpenAITTSProvider

from .test_openai_tts import configured, parts, speech_server, wav_bytes
from .test_openai_tts import loopback_only as loopback_only


@pytest.mark.parametrize(
    "character",
    [
        "\u200b",
        "\u202a",
        "\u202b",
        "\u202c",
        "\u202d",
        "\u202e",
        "\u2066",
        "\u061c",
        "\ufeff",
        "\u00ad",
        "é",
        "/",
    ],
)
def test_unsafe_voice_names_are_counted_without_logging_names(
    monkeypatch, caplog, character
):
    async def run():
        unsafe = "voice" + character + "unsafe"
        async with speech_server(voices=["voice-a", unsafe]) as (url, _, _):
            with pytest.raises(ValueError, match="voice list"):
                await configured(monkeypatch, url)
            assert unsafe not in caplog.text
            assert unsafe.encode("unicode_escape").decode() not in caplog.text
            assert "openai_tts_invalid_voice_names count=1" in caplog.text

    asyncio.run(run())


def test_safe_ascii_voice_punctuation_reaches_catalog(monkeypatch):
    async def run():
        async with speech_server(voices=["voice-a", "Voice 2.v1_-A"]) as (url, _, _):
            factory = await configured(monkeypatch, url)
            assert [v.id for v in factory.tts_voices] == ["voice-a", "Voice 2.v1_-A"]

    asyncio.run(run())


def test_refresh_counts_each_unsafe_name_and_keeps_safe_health_choices(
    monkeypatch, caplog
):
    async def catalog(request, count):
        return web.json_response(
            {
                "voices": ["voice-a"]
                if count == 1
                else ["voice-a", "bad\u202e", "bad\u200b"]
            }
        )

    async def run():
        async with speech_server(catalog_handler=catalog) as (url, _, _):
            factory = await configured(monkeypatch, url)
            provider = factory.tts("openai_tts")
            monkeypatch.setattr(
                "assist_live_server.providers.openai_tts.monotonic",
                lambda: 1000000000000.0,
            )
            await factory.refresh_tts_voices()
            assert [v.id for v in factory.tts_voices] == ["voice-a"]
            assert provider.invalid_voice_names_total == 2
            assert "openai_tts_invalid_voice_names count=2" in caplog.text
            assert "bad\u202e" not in caplog.text
            assert "bad\u200b" not in caplog.text

    asyncio.run(run())


def test_invalid_voice_name_count_reaches_metrics(monkeypatch, tmp_path):
    from assist_live_server.app import create_app
    from assist_live_server.auth import Registry
    from assist_live_server.store import SQLiteStore

    async def catalog(request, count):
        return web.json_response(
            {"voices": ["voice-a"] if count == 1 else ["voice-a", "bad\u202e"]}
        )

    async def run():
        async with speech_server(catalog_handler=catalog) as (url, _, _):
            factory = await configured(monkeypatch, url)
            store = SQLiteStore(tmp_path / "metrics.sqlite3")
            registry = Registry(replay_store=store)
            registry.bind("satellite-a", "satellite-a", b"synthetic-device-secret")
            registry.add_token("test-token", ["satellite-a"])
            try:
                async with TestClient(
                    TestServer(create_app(registry, store, providers=factory))
                ) as client:

                    async def counter():
                        response = await client.get(
                            "/metrics", headers={"Authorization": "Bearer test-token"}
                        )
                        assert response.status == 200
                        lines = (await response.text()).splitlines()
                        name = "assist_live_tts_invalid_voice_names_total "
                        return [line for line in lines if line.startswith(name)]

                    assert await counter() == [
                        "assist_live_tts_invalid_voice_names_total 0"
                    ]
                    monkeypatch.setattr(
                        "assist_live_server.providers.openai_tts.monotonic",
                        lambda: 1000000000000.0,
                    )
                    await factory.refresh_tts_voices()
                    assert await counter() == [
                        "assist_live_tts_invalid_voice_names_total 1"
                    ]
            finally:
                store.close()

    asyncio.run(run())


@pytest.mark.parametrize("setting", ["OPENAI_TTS_DEFAULT_VOICE", "OPENAI_TTS_VOICES"])
def test_unsafe_configured_voice_cannot_seed_unavailable_catalog(
    monkeypatch, caplog, setting
):
    async def run():
        async with speech_server() as (url, _, _):
            pass
        unsafe = "bad\u202e"
        monkeypatch.setenv("OPENAI_TTS_DEFAULT_VOICE", "voice-a")
        monkeypatch.setenv(
            setting,
            unsafe if setting.endswith("DEFAULT_VOICE") else "voice-a," + unsafe,
        )
        with pytest.raises(ValueError, match="voice"):
            await OpenAITTSProvider.load(".", base_url=url)
        assert unsafe not in caplog.text

    asyncio.run(run())


@pytest.mark.parametrize("rate, frequency", [(11025, 3000), (8000, 2000)])
def test_upsampling_suppresses_mirror_images(monkeypatch, rate, frequency):
    clock = np.arange(rate) / rate
    pcm = np.rint(12000 * np.sin(2 * np.pi * frequency * clock)).astype("<i2").tobytes()

    async def handler(request, count):
        return web.Response(body=wav_bytes(rate=rate, pcm=pcm))

    async def run():
        async with speech_server(handler) as (url, _, _):
            factory = await configured(monkeypatch, url)
            audio = b"".join(
                [
                    i
                    async for i in factory.tts("openai_tts").synthesize(
                        parts("One."), "voice-a"
                    )
                ]
            )
            samples = np.frombuffer(audio, dtype="<i2").astype(float)
            assert len(samples) == 22050
            spectrum = np.abs(np.fft.rfft(samples * np.hanning(len(samples))))
            assert spectrum[frequency] > 4000 * len(samples) / 2
            assert spectrum[rate - frequency] < spectrum[frequency] * 0.01

    asyncio.run(run())


def test_resampler_block_joins_match_single_block_sinc_reference(monkeypatch):
    async def run():
        async with speech_server() as (url, _, _):
            provider = (await configured(monkeypatch, url)).tts("openai_tts")
            rate = 11025
            samples = np.random.default_rng(8).integers(
                -12000, 12000, 2577, dtype=np.int16
            )
            actual = np.frombuffer(
                provider._resample(samples.astype("<i2").tobytes(), rate), dtype="<i2"
            ).astype(float)
            positions = np.arange(5154) * rate / 22050
            centers = np.floor(positions).astype(int)
            offsets = np.arange(-63, 65)
            distances = offsets[None, :] - (positions - centers)[:, None]
            weights = 0.94 * np.sinc(0.94 * distances) * np.kaiser(128, 8.6)
            weights /= weights.sum(axis=1, keepdims=True)
            indices = np.clip(centers[:, None] + offsets, 0, len(samples) - 1)
            reference = np.rint(np.sum(weights * samples[indices], axis=1))
            assert len(actual) == len(reference)
            assert np.max(np.abs(actual - reference)) <= 1
            joins = np.arange(256, len(actual), 256)
            assert (
                np.max(
                    np.abs(np.diff(actual)[joins - 1] - np.diff(reference)[joins - 1])
                )
                <= 1
            )

    asyncio.run(run())


def test_resampler_extra_memory_is_bounded_for_sixty_seconds(monkeypatch):
    async def run():
        async with speech_server() as (url, _, _):
            provider = (await configured(monkeypatch, url)).tts("openai_tts")
            pcm = b"\x01\x00" * (60 * 24000)
            tracemalloc.start()
            try:
                audio = provider._resample(pcm, 24000)
                _, peak = tracemalloc.get_traced_memory()
            finally:
                tracemalloc.stop()
            assert len(audio) == 60 * 22050 * 2
            assert peak < 8 * 1024 * 1024

    asyncio.run(run())


def test_sinc_workspaces_are_float32_and_fixed_size_blocks(monkeypatch):
    sinc = np.sinc
    shapes = []

    def bounded(values):
        assert values.dtype == np.float32
        assert values.shape[0] <= 256
        assert values.shape[1] == 128
        shapes.append(values.shape)
        return sinc(values)

    async def run():
        async with speech_server() as (url, _, _):
            provider = (await configured(monkeypatch, url)).tts("openai_tts")
            monkeypatch.setattr(np, "sinc", bounded)
            assert provider._resample(b"\x01\x00" * 8000, 8000)
            assert len(shapes) > 1

    asyncio.run(run())


def test_sentence_response_cap_is_sixty_seconds_plus_headers(monkeypatch):
    async def handler(request, count):
        return web.Response(body=wav_bytes(pcm=b"\x01\x00" * (60 * 24000 + 32768)))

    async def run():
        async with speech_server(handler) as (url, _, _):
            provider = (await configured(monkeypatch, url)).tts("openai_tts")
            with pytest.raises(RuntimeError, match="openai_tts_response_too_large"):
                await anext(provider.stream(parts("One."), "voice-a"))

    asyncio.run(run())


def test_source_duration_is_limited_independently_of_response_bytes(monkeypatch):
    async def run():
        async with speech_server() as (url, _, _):
            provider = (await configured(monkeypatch, url)).tts("openai_tts")
            with pytest.raises(RuntimeError, match="openai_tts_bad_wav"):
                provider._pcm(wav_bytes(rate=8000, pcm=b"\x01\x00" * (60 * 8000 + 1)))

    asyncio.run(run())


@pytest.mark.parametrize("rate", [4000, 384000])
def test_unsupported_source_header_rate_has_named_wav_error(monkeypatch, rate):
    async def run():
        async with speech_server() as (url, _, _):
            provider = (await configured(monkeypatch, url)).tts("openai_tts")
            with pytest.raises(RuntimeError, match="openai_tts_bad_wav"):
                provider._pcm(wav_bytes(rate=rate))

    asyncio.run(run())


def test_odd_non_data_chunk_runtime_error_has_named_wav_error(monkeypatch):
    async def run():
        async with speech_server() as (url, _, _):
            provider = (await configured(monkeypatch, url)).tts("openai_tts")
            raw = wav_bytes()
            malformed = raw[:12] + b"JUNK\x01\x00\x00\x00x" + raw[12:]
            malformed = (
                malformed[:4]
                + (len(malformed) - 8).to_bytes(4, "little")
                + malformed[8:]
            )
            with pytest.raises(RuntimeError, match="openai_tts_bad_wav"):
                provider._pcm(malformed)

    asyncio.run(run())


def test_empty_key_file_is_named_configuration_error(monkeypatch, tmp_path):
    key_file = tmp_path / "empty-key"
    key_file.write_bytes(b"")
    monkeypatch.setenv("OPENAI_TTS_API_KEY_FILE", str(key_file))

    async def run():
        async with speech_server() as (url, _, _):
            with pytest.raises(ValueError, match="openai_tts.*key"):
                await configured(monkeypatch, url)

    asyncio.run(run())


def test_http_key_warns_once_without_key_or_url(monkeypatch, tmp_path, caplog):
    key = "synthetic-cleartext-key-canary"
    key_file = tmp_path / "key"
    key_file.write_text(key)
    monkeypatch.setenv("OPENAI_TTS_API_KEY_FILE", str(key_file))

    async def run():
        async with speech_server() as (url, _, _):
            factory = await configured(monkeypatch, url)
            provider = factory.tts("openai_tts")
            [i async for i in provider.stream(parts("One. Two."), "voice-a")]
            warnings = [
                record
                for record in caplog.records
                if record.levelno >= logging.WARNING
                and "cleartext HTTP" in record.message
            ]
            assert len(warnings) == 1
            assert key not in caplog.text
            assert url not in caplog.text

    asyncio.run(run())


@pytest.mark.parametrize("scheme, key", [("http", False), ("https", True)])
def test_cleartext_warning_requires_http_and_key(
    monkeypatch, tmp_path, caplog, scheme, key
):
    from assist_live_server.destinations import resolve_addresses

    monkeypatch.setenv("OPENAI_TTS_DEFAULT_VOICE", "voice-a")
    if key:
        key_file = tmp_path / "key"
        key_file.write_text("synthetic-cleartext-key-canary")
        monkeypatch.setenv("OPENAI_TTS_API_KEY_FILE", str(key_file))
    OpenAITTSProvider(
        f"{scheme}://speech.invalid", local_only=True, resolver=resolve_addresses
    )
    assert "cleartext HTTP" not in caplog.text


def test_concurrent_turns_keep_voice_audio_and_positions_isolated(monkeypatch):
    async def run():
        entered = asyncio.Event()
        release = asyncio.Event()
        formats_ready = asyncio.Event()
        formats = 0
        active = 0

        async def handler(request, count):
            nonlocal active
            value = await request.json()
            active += 1
            if active == 2:
                entered.set()
            await release.wait()
            return web.Response(body=wav_bytes(pcm=value["voice"].encode("utf-16le")))

        async with speech_server(handler) as (url, requests, _):
            provider = (await configured(monkeypatch, url)).tts("openai_tts")

            async def collect(voice, text):
                nonlocal formats
                stream = provider.stream(parts(text), voice)
                try:
                    first = await anext(stream)
                    formats += 1
                    if formats == 2:
                        formats_ready.set()
                    await formats_ready.wait()
                    return [first, *[i async for i in stream]]
                finally:
                    await stream.aclose()

            tasks = [
                asyncio.create_task(collect("voice-a", "One.")),
                asyncio.create_task(collect("voice-b", "Second!")),
            ]
            try:
                await asyncio.wait_for(entered.wait(), 1)
                release.set()
                first, second = await asyncio.wait_for(asyncio.gather(*tasks), 1)
                for items, voice, end in [
                    (first, "voice-a", 4),
                    (second, "voice-b", 7),
                ]:
                    assert b"".join(
                        i.data for i in items if isinstance(i, TTSAudio)
                    ) == voice.encode("utf-16le")
                    assert [i for i in items if isinstance(i, TTSSentenceEnd)] == [
                        TTSSentenceEnd(0, end)
                    ]
                assert len(requests) == 2
            finally:
                release.set()
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

    asyncio.run(run())


def test_concurrent_health_refresh_is_single_flight(monkeypatch):
    from assist_live_server.app import create_app
    from assist_live_server.auth import Registry

    async def run():
        entered = asyncio.Event()
        release = asyncio.Event()

        async def catalog(request, count):
            if count > 1:
                entered.set()
                await release.wait()
            return web.json_response({"voices": ["voice-a", "voice-b"]})

        async with speech_server(catalog_handler=catalog) as (url, _, voice_requests):
            factory = await configured(monkeypatch, url)
            registry = Registry()
            registry.bind("satellite-a", "satellite-a", b"synthetic-device-secret")
            registry.add_token("test-token", ["satellite-a"])
            monkeypatch.setattr(
                "assist_live_server.providers.openai_tts.monotonic",
                lambda: 1000000000000.0,
            )
            async with TestClient(
                TestServer(create_app(registry, providers=factory))
            ) as client:
                monkeypatch.setattr(
                    "assist_live_server.providers.openai_tts.METADATA_TIMEOUT", 2.0
                )

                async def health():
                    response = await client.get(
                        "/health", headers={"Authorization": "Bearer test-token"}
                    )
                    return await response.json()

                first = asyncio.create_task(health())
                try:
                    await asyncio.wait_for(entered.wait(), 1)
                    others = await asyncio.wait_for(
                        asyncio.gather(*(health() for _ in range(8))), 0.5
                    )
                    assert len(voice_requests) == 2
                    assert not first.done()
                    assert all(
                        v["capabilities"]["tts_default_voice"] == "voice-a"
                        for v in others
                    )
                    release.set()
                    assert (await asyncio.wait_for(first, 0.5))["capabilities"][
                        "tts_default_voice"
                    ] == "voice-a"
                finally:
                    release.set()
                    first.cancel()
                    await asyncio.gather(first, return_exceptions=True)

    asyncio.run(run())

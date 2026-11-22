import asyncio
import io
import ipaddress
import json
import socket
import traceback
import wave
from contextlib import asynccontextmanager
from uuid import uuid7

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from assist_live_client.models import TTSAudio, TTSFormat, TTSSentenceEnd
from assist_live_server.config import ProviderFactory, ServerConfig
from assist_live_server.providers.tts import provider_names
from assist_live_server.tts_gateway import TTSGateway


@pytest.fixture(autouse=True)
def loopback_only(monkeypatch):
    connect = socket.socket.connect
    connect_ex = socket.socket.connect_ex
    resolve = socket.getaddrinfo

    def checked(original, sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            assert ipaddress.ip_address(address[0]).is_loopback, (
                "external network refused"
            )
        return original(sock, address)

    monkeypatch.setattr(
        socket.socket, "connect", lambda sock, address: checked(connect, sock, address)
    )
    monkeypatch.setattr(
        socket.socket,
        "connect_ex",
        lambda sock, address: checked(connect_ex, sock, address),
    )

    def checked_resolve(host, port, *args, **kwargs):
        try:
            assert ipaddress.ip_address(host).is_loopback
        except ValueError:
            raise AssertionError("external DNS refused") from None
        return resolve(host, port, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", checked_resolve)


async def parts(*values):
    for value in values:
        yield value


def wav_bytes(rate=22050, channels=1, width=2, pcm=b"\x01\x00" * 20000):
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(width)
        wav.setframerate(rate)
        wav.writeframes(pcm)
    return output.getvalue()


@asynccontextmanager
async def speech_server(handler=None, voices=None, catalog_handler=None):
    requests = []
    voice_requests = []

    async def speech(request):
        requests.append(await request.json())
        if handler is not None:
            return await handler(request, len(requests))
        return web.Response(body=wav_bytes(), content_type="audio/wav")

    async def catalog(request):
        voice_requests.append(True)
        if catalog_handler is not None:
            return await catalog_handler(request, len(voice_requests))
        return web.json_response(
            {"voices": ["voice-a", "voice-b"] if voices is None else voices}
        )

    app = web.Application()
    app.router.add_post("/v1/audio/speech", speech)
    app.router.add_get("/v1/audio/voices", catalog)

    async def redirected(request):
        requests.append({"redirected": True})
        return web.Response(body=wav_bytes())

    app.router.add_get("/redirect-target", redirected)
    async with TestServer(app) as server:
        yield str(server.make_url("/")), requests, voice_requests


async def configured(monkeypatch, url, **options):
    assert "openai_tts" in provider_names(), "OpenAI-compatible TTS is not registered"
    monkeypatch.setenv("OPENAI_TTS_BASE_URL", url)
    monkeypatch.setenv("OPENAI_TTS_DEFAULT_VOICE", "voice-a")
    config = await ServerConfig.load(
        stt_provider=None, tts_provider="openai_tts", **options
    )
    return await ProviderFactory.load(config)


def test_two_sentences_use_piper_text_positions_and_header_rate(monkeypatch):
    async def run():
        async with speech_server() as (url, requests, _):
            factory = await configured(monkeypatch, url)
            provider = factory.tts("openai_tts")
            stream = provider.stream(parts("**One**. ", "Two!"), "voice-a")
            assert await anext(stream) == TTSFormat("pcm_s16le", 22050, 1, 2)
            assert requests == [{"input": "One.", "voice": "voice-a"}]
            items = [item async for item in stream]
            assert requests == [
                {"input": "One.", "voice": "voice-a"},
                {"input": "Two!", "voice": "voice-a"},
            ]
            assert [i for i in items if isinstance(i, TTSSentenceEnd)] == [
                TTSSentenceEnd(0, 8),
                TTSSentenceEnd(1, 13),
            ]
            audio = [i.data for i in items if isinstance(i, TTSAudio)]
            assert list(map(len, audio)) == [32000, 8000, 32000, 8000]
            assert b"".join(audio) == b"\x01\x00" * 40000
            assert factory.tts_destination == "local"

    asyncio.run(run())


def test_unknown_voice_stops_before_text_or_http(monkeypatch):
    async def run():
        async with speech_server() as (url, requests, _):
            factory = await configured(monkeypatch, url)

            async def forbidden():
                raise AssertionError("unknown voice read text")
                yield "Never."

            with pytest.raises(ValueError, match="unknown TTS voice"):
                await anext(factory.tts("openai_tts").stream(forbidden(), "unknown"))
            assert requests == []

    asyncio.run(run())


def test_checked_hostname_connection_is_pinned_and_keeps_host(monkeypatch):
    async def run():
        from assist_live_server.providers.openai_tts import OpenAITTSProvider
        from yarl import URL

        seen = []
        resolutions = []

        async def handler(request, count):
            seen.append(request.host)
            return web.Response(body=wav_bytes())

        async def resolver(host, port):
            resolutions.append((host, port))
            return ("127.0.0.1",)

        async with speech_server(handler) as (url, _, _):
            port = URL(url).port
            monkeypatch.setenv("OPENAI_TTS_DEFAULT_VOICE", "voice-a")
            provider = await OpenAITTSProvider.load(
                ".", base_url=f"http://speech.invalid:{port}", resolver=resolver
            )
            items = [i async for i in provider.stream(parts("One. Two."), "voice-a")]
            assert len([i for i in items if isinstance(i, TTSSentenceEnd)]) == 2
            assert resolutions == [("speech.invalid", port)] * 3
            assert seen == [f"speech.invalid:{port}"] * 2

    asyncio.run(run())


@pytest.mark.parametrize(
    "names", [[], ["voice-a", "voice-a"], ["voice-a", None], ["voice-a"] * 257]
)
def test_invalid_catalog_never_reaches_health(monkeypatch, names):
    async def run():
        async with speech_server(voices=names) as (url, _, _):
            with pytest.raises(ValueError, match="voice list"):
                await configured(monkeypatch, url)

    asyncio.run(run())


@pytest.mark.parametrize(
    "body",
    [
        b"not a WAV",
        wav_bytes(channels=2),
        wav_bytes(width=1),
        wav_bytes()[:-2],
        wav_bytes(pcm=b""),
        wav_bytes().replace(b"WAVE", b"NOPE", 1),
        wav_bytes().replace(b"RIFF", b"RIFX", 1),
        wav_bytes(pcm=b"\x01\x00\x02"),
        wav_bytes()[:4] + b"\xff\xff\xff\x7f" + wav_bytes()[8:],
        wav_bytes()[:24] + b"\x00" * 4 + wav_bytes()[28:],
        b"RIFF\x10\x00\x00\x00WAVEfmt \x03\x00\x00\x00abc\x00",
    ],
    ids=[
        "header",
        "stereo",
        "pcm8",
        "truncated",
        "empty",
        "wave",
        "riff",
        "odd",
        "riff_size",
        "zero_rate",
        "short_fmt",
    ],
)
def test_bad_wav_is_named_failure_before_audio(monkeypatch, body):
    async def handler(request, count):
        return web.Response(body=body)

    async def run():
        async with speech_server(handler) as (url, requests, _):
            factory = await configured(monkeypatch, url)
            with pytest.raises(RuntimeError, match="openai_tts_bad_wav"):
                await anext(factory.tts("openai_tts").stream(parts("One."), "voice-a"))
            assert len(requests) == 1

    asyncio.run(run())


@pytest.mark.parametrize("failure", ["status", "rate", "connect", "bad_wav"])
def test_mid_reply_failure_keeps_only_completed_sentence_no_retry(monkeypatch, failure):
    async def handler(request, count):
        if count == 1:
            return web.Response(body=wav_bytes(pcm=b"\x01\x00"))
        if failure == "connect":
            request.transport.close()
            return web.Response()
        return web.Response(
            status=503 if failure == "status" else 200,
            body=b"invalid" if failure == "bad_wav" else wav_bytes(rate=16000),
        )

    async def run():
        async with speech_server(handler) as (url, requests, _):
            factory = await configured(monkeypatch, url)
            records = []

            async def record(stage, data):
                records.append((stage, data))

            gateway = TTSGateway(
                factory.tts, provider="openai_tts", destination="local"
            )
            messages = [
                item
                async for item in gateway.stream(
                    str(uuid7()),
                    parts("One. Two. Three."),
                    "voice-a",
                    text_positions=True,
                    record=record,
                )
            ]
            assert [i for i in messages if isinstance(i, bytes)] == [b"\x01\x00"]
            markers = [
                i
                for i in messages
                if isinstance(i, dict) and i["type"] == "sentence_end"
            ]
            assert [(i["sentence"], i["text_end"]) for i in markers] == [(0, 4)]
            assert messages[-1]["code"] == "tts_failed"
            assert records[-1][0] == "tts_failed"
            assert len(requests) == 2

    asyncio.run(run())


@pytest.mark.parametrize("status", [302, 400, 503])
def test_non_200_with_valid_wav_is_not_audio(monkeypatch, status):
    async def handler(request, count):
        return web.Response(
            status=status, body=wav_bytes(), headers={"Location": "/redirect-target"}
        )

    async def run():
        async with speech_server(handler) as (url, requests, _):
            factory = await configured(monkeypatch, url)
            with pytest.raises(RuntimeError, match="openai_tts_http_error"):
                await anext(factory.tts("openai_tts").stream(parts("One."), "voice-a"))
            assert len(requests) == 1

    asyncio.run(run())


def test_timeout_starts_existing_fallback_error_path(monkeypatch):
    async def run():
        release = asyncio.Event()
        entered = asyncio.Event()

        async def handler(request, count):
            entered.set()
            await release.wait()
            return web.Response(body=wav_bytes())

        monkeypatch.setenv("OPENAI_TTS_TIMEOUT", "0.02")
        async with speech_server(handler) as (url, requests, _):
            factory = await configured(monkeypatch, url)
            gateway = TTSGateway(
                factory.tts, provider="openai_tts", destination="local"
            )

            async def collect():
                return [
                    i
                    async for i in gateway.stream(
                        str(uuid7()), parts("One."), "voice-a"
                    )
                ]

            task = asyncio.create_task(collect())
            try:
                await asyncio.wait_for(entered.wait(), 1)
                messages = await asyncio.wait_for(task, 1)
                assert [i["code"] for i in messages] == ["tts_failed"]
                assert len(requests) == 1
            finally:
                release.set()
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())


def test_public_address_refused_at_config_and_each_connection(monkeypatch):
    async def run():
        from assist_live_server.providers.openai_tts import OpenAITTSProvider

        answers = ("127.0.0.1", "192.0.2.1")

        async def resolver(host, port):
            return answers

        monkeypatch.setenv("OPENAI_TTS_BASE_URL", "http://speech.invalid")
        with pytest.raises(ValueError, match="local only"):
            await ServerConfig.load(
                stt_provider=None, tts_provider="openai_tts", resolver=resolver
            )
        config = await ServerConfig.load(
            stt_provider=None,
            tts_provider="openai_tts",
            local_only=False,
            resolver=resolver,
        )
        assert config.tts_destination == "cloud"
        async with speech_server() as (url, requests, _):
            answers = ("127.0.0.1",)
            monkeypatch.setenv("OPENAI_TTS_DEFAULT_VOICE", "voice-a")
            provider = await OpenAITTSProvider.load(
                ".", base_url=url, resolver=resolver
            )
            answers = ("192.0.2.1",)
            with pytest.raises(RuntimeError, match="openai_tts_destination_refused"):
                await anext(provider.stream(parts("One."), "voice-a"))
            assert requests == []

    asyncio.run(run())


@pytest.mark.parametrize("rate", [8000, 16000, 22050, 24000, 44100, 48000])
def test_header_rates_use_compatible_format_and_preserve_duration(monkeypatch, rate):
    pcm = b"\x10\x00" * rate

    async def handler(request, count):
        return web.Response(body=wav_bytes(rate=rate, pcm=pcm))

    async def run():
        async with speech_server(handler) as (url, _, _):
            factory = await configured(monkeypatch, url)
            items = [
                i
                async for i in factory.tts("openai_tts").stream(
                    parts("One."), "voice-a"
                )
            ]
            output_rate = 16000 if rate == 16000 else 22050
            assert items[0] == TTSFormat("pcm_s16le", output_rate, 1, 2)
            audio = b"".join(i.data for i in items if isinstance(i, TTSAudio))
            assert len(audio) == 2 * output_rate
            if rate in {16000, 22050}:
                assert audio == pcm

    asyncio.run(run())


def test_later_header_rate_change_fails_even_when_output_rates_match(monkeypatch):
    async def handler(request, count):
        return web.Response(body=wav_bytes(rate=24000 if count == 1 else 48000))

    async def run():
        async with speech_server(handler) as (url, requests, _):
            factory = await configured(monkeypatch, url)
            stream = factory.tts("openai_tts").stream(parts("One. Two."), "voice-a")
            assert await anext(stream) == TTSFormat("pcm_s16le", 22050, 1, 2)
            while not isinstance(await anext(stream), TTSSentenceEnd):
                pass
            with pytest.raises(RuntimeError, match="openai_tts_rate_changed"):
                await anext(stream)
            assert len(requests) == 2

    asyncio.run(run())


def test_expired_voice_refresh_failure_keeps_last_known_list_and_throttles(monkeypatch):
    async def run():
        import assist_live_server.providers.openai_tts as module

        async with speech_server() as (url, _, _):
            factory = await configured(monkeypatch, url)
            provider = factory.tts("openai_tts")
        monkeypatch.setattr(module, "monotonic", lambda: 1000000000000.0)
        attempts = []
        request = provider._request

        async def counted(*args, **kwargs):
            attempts.append(True)
            return await request(*args, **kwargs)

        monkeypatch.setattr(provider, "_request", counted)
        await factory.refresh_tts_voices()
        assert [v.id for v in factory.tts_voices] == ["voice-a", "voice-b"]
        assert factory.tts_default_voice == "voice-a"
        await factory.refresh_tts_voices()
        assert attempts == [True]

    asyncio.run(run())


def test_expired_catalog_refresh_keeps_health_within_deadline(monkeypatch):
    async def run():
        release = asyncio.Event()

        async def catalog(request, count):
            if count > 1:
                await release.wait()
            return web.json_response({"voices": ["voice-a", "voice-b"]})

        async with speech_server(catalog_handler=catalog) as (url, _, voice_requests):
            factory = await configured(monkeypatch, url)
            monkeypatch.setattr(
                "assist_live_server.providers.openai_tts.monotonic",
                lambda: 1000000000000.0,
            )
            try:
                await asyncio.wait_for(factory.refresh_tts_voices(), 0.3)
                assert [v.id for v in factory.tts_voices] == ["voice-a", "voice-b"]
                assert len(voice_requests) == 2
            finally:
                release.set()

    asyncio.run(run())


@pytest.mark.parametrize("allowlist", ["", "voice-a,voice-b"])
def test_unavailable_initial_voice_fetch_keeps_configured_choices(
    monkeypatch, allowlist
):
    async def run():
        async with speech_server() as (url, _, _):
            pass
        monkeypatch.setenv("OPENAI_TTS_VOICES", allowlist)
        factory = await configured(monkeypatch, url)
        assert [v.id for v in factory.tts_voices] == (
            ["voice-a", "voice-b"] if allowlist else ["voice-a"]
        )
        assert factory.tts_default_voice == "voice-a"

    asyncio.run(run())


def test_voice_catalog_cannot_publish_key_canary(monkeypatch, tmp_path, caplog):
    key = "synthetic-tts-catalog-canary"
    key_file = tmp_path / "key"
    key_file.write_text(key)
    monkeypatch.setenv("OPENAI_TTS_API_KEY_FILE", str(key_file))

    async def run():
        async with speech_server(voices=["voice-a", key]) as (url, _, _):
            with pytest.raises(ValueError) as error:
                await configured(monkeypatch, url)
            assert key not in "".join(traceback.format_exception(error.value))
            assert key not in caplog.text

    asyncio.run(run())


def test_response_size_is_bounded_before_audio(monkeypatch):
    async def run():
        async with speech_server() as (url, requests, _):
            factory = await configured(monkeypatch, url)
            monkeypatch.setattr(
                "assist_live_server.providers.openai_tts.MAX_WAV_BYTES", 100
            )
            with pytest.raises(RuntimeError, match="openai_tts_response_too_large"):
                await anext(factory.tts("openai_tts").stream(parts("One."), "voice-a"))
            assert len(requests) == 1

    asyncio.run(run())


def test_cancellation_starts_no_next_sentence(monkeypatch):
    async def run():
        entered = asyncio.Event()
        release = asyncio.Event()

        async def handler(request, count):
            entered.set()
            await release.wait()
            return web.Response(body=wav_bytes())

        async with speech_server(handler) as (url, requests, _):
            factory = await configured(monkeypatch, url)
            stream = factory.tts("openai_tts").stream(parts("One. Two."), "voice-a")
            task = asyncio.create_task(anext(stream))
            try:
                await asyncio.wait_for(entered.wait(), 1)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                assert len(requests) == 1
            finally:
                release.set()
                await stream.aclose()

    asyncio.run(run())


def test_downsampling_attenuates_above_nyquist_tone_without_alias(monkeypatch):
    import numpy as np

    clock = np.arange(24000) / 24000
    tone = (
        np.rint(
            10000
            * (np.sin(2 * np.pi * 1000 * clock) + np.sin(2 * np.pi * 11500 * clock))
        )
        .astype("<i2")
        .tobytes()
    )

    async def handler(request, count):
        return web.Response(body=wav_bytes(rate=24000, pcm=tone))

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
            assert spectrum[1000] > 4000 * len(samples) / 2
            assert spectrum[10550] < spectrum[1000] * 0.01

    asyncio.run(run())


def test_resampled_24khz_reply_is_accepted_by_real_typed_client(monkeypatch, tmp_path):
    from assist_live_client import AssistLiveClient, TurnOpen
    from assist_live_server.app import create_app
    from assist_live_server.auth import Registry
    from assist_live_server.store import SQLiteStore

    async def handler(request, count):
        return web.Response(body=wav_bytes(rate=24000, pcm=b"\x10\x00" * 2400))

    async def run():
        async with speech_server(handler) as (url, _, _):
            factory = await configured(monkeypatch, url)
            store = SQLiteStore(tmp_path / "turns.sqlite3")
            registry = Registry(replay_store=store)
            registry.bind("satellite-a", "satellite-a", b"synthetic-device-secret")
            registry.add_token("test-token", ["satellite-a"])
            try:
                async with TestClient(
                    TestServer(create_app(registry, store, providers=factory))
                ) as service:
                    client = AssistLiveClient(
                        str(service.make_url("/")).rstrip("/"),
                        "test-token",
                        session=service.session,
                    )
                    turn_id = str(uuid7())
                    await client.open_turn(
                        TurnOpen(turn_id, "satellite-a", "tts_only", None, None)
                    )
                    items = [
                        i
                        async for i in client.tts_stream(
                            turn_id, parts("One. Two."), text_positions=True
                        )
                    ]
                    assert items[0] == TTSFormat("pcm_s16le", 22050, 1, 2)
                    assert (
                        len(b"".join(i.data for i in items if isinstance(i, TTSAudio)))
                        == 8820
                    )
                    assert [
                        i.text_end for i in items if isinstance(i, TTSSentenceEnd)
                    ] == [4, 9]
            finally:
                store.close()

    asyncio.run(run())


def test_key_file_is_used_but_absent_from_errors_health_metrics_and_records(
    monkeypatch, tmp_path, caplog
):
    key = "synthetic-tts-key-canary"
    key_file = tmp_path / "key"
    key_file.write_text(key + "\n")
    monkeypatch.setenv("OPENAI_TTS_API_KEY_FILE", str(key_file))
    headers = []

    async def handler(request, count):
        headers.append(request.headers.get("Authorization"))
        return web.Response(status=500, text=key)

    async def run():
        from assist_live_client import AssistLiveClient, TurnOpen
        from assist_live_client.protocol import ProtocolError
        from assist_live_server.app import create_app
        from assist_live_server.auth import Registry
        from assist_live_server.store import SQLiteStore

        async with speech_server(handler) as (url, _, _):
            factory = await configured(monkeypatch, url)
            with pytest.raises(RuntimeError) as error:
                await anext(factory.tts("openai_tts").stream(parts("One."), "voice-a"))
            rendered = "".join(traceback.format_exception(error.value))
            assert key not in rendered
            records = []

            async def record(stage, data):
                records.append((stage, data))

            gateway = TTSGateway(
                factory.tts, provider="openai_tts", destination="local"
            )
            messages = [
                i
                async for i in gateway.stream(
                    str(uuid7()), parts("One."), "voice-a", record=record
                )
            ]
            store = SQLiteStore(tmp_path / "key-turns.sqlite3")
            registry = Registry(replay_store=store)
            registry.bind("satellite-a", "satellite-a", b"synthetic-device-secret")
            registry.add_token("test-token", ["satellite-a"])
            try:
                async with TestClient(
                    TestServer(create_app(registry, store, providers=factory))
                ) as client:
                    typed = AssistLiveClient(
                        str(client.make_url("/")).rstrip("/"),
                        "test-token",
                        session=client.session,
                    )
                    turn_id = str(uuid7())
                    await typed.open_turn(
                        TurnOpen(turn_id, "satellite-a", "tts_only", None, None)
                    )
                    with pytest.raises(ProtocolError, match="audio refused"):
                        [i async for i in typed.tts_stream(turn_id, parts("One."))]
                    turn = await typed.turn(turn_id)
                    assert key not in json.dumps(turn)
                    assert any(row["stage"] == "tts_failed" for row in turn["events"])
                    health = await client.get(
                        "/health", headers={"Authorization": "Bearer test-token"}
                    )
                    metrics = await client.get(
                        "/metrics", headers={"Authorization": "Bearer test-token"}
                    )
                    assert key not in await health.text()
                    assert key not in await metrics.text()
            finally:
                store.close()
            assert key not in json.dumps(records)
            assert key not in json.dumps(messages)
            assert key not in caplog.text
            assert headers == ["Bearer " + key] * 3

    asyncio.run(run())


def test_health_voices_are_filtered_and_cached_with_bounded_refresh(monkeypatch):
    async def run():
        from assist_live_server.app import create_app
        from assist_live_server.auth import Registry

        monkeypatch.setenv("OPENAI_TTS_VOICES", "voice-a")
        async with speech_server() as (url, _, voice_requests):
            factory = await configured(monkeypatch, url)
            registry = Registry()
            registry.bind("satellite-a", "satellite-a", b"synthetic-device-secret")
            registry.add_token("test-token", ["satellite-a"])
            async with TestClient(
                TestServer(create_app(registry, providers=factory))
            ) as client:
                for _ in range(3):
                    response = await client.get(
                        "/health", headers={"Authorization": "Bearer test-token"}
                    )
                    body = await response.json()
                    assert body["capabilities"]["tts_voices"] == [
                        {"id": "voice-a", "name": "voice-a", "language": "und"}
                    ]
                    assert body["capabilities"]["tts_default_voice"] == "voice-a"
                    assert body["destinations"]["tts"] == "local"
                assert len(voice_requests) == 1
                provider = factory.tts("openai_tts")
                monkeypatch.setattr(
                    "assist_live_server.providers.openai_tts.monotonic",
                    lambda: 1000000000000.0,
                )
                response = await client.get(
                    "/health", headers={"Authorization": "Bearer test-token"}
                )
                assert response.status == 200
                assert len(voice_requests) == 2
                assert provider.default_voice == "voice-a"

    asyncio.run(run())


def test_default_voice_must_be_in_configured_and_discovered_lists(monkeypatch):
    async def run():
        async with speech_server(voices=["voice-b"]) as (url, _, _):
            with pytest.raises(ValueError, match="default voice"):
                await configured(monkeypatch, url)
        monkeypatch.setenv("OPENAI_TTS_VOICES", "voice-b")
        async with speech_server() as (url, _, _):
            with pytest.raises(ValueError, match="default voice"):
                await configured(monkeypatch, url)

    asyncio.run(run())


def test_default_voice_must_be_allowlisted_even_when_catalog_is_unavailable(
    monkeypatch,
):
    async def run():
        async with speech_server() as (url, _, _):
            pass
        monkeypatch.setenv("OPENAI_TTS_VOICES", "voice-b")
        with pytest.raises(ValueError, match="default voice"):
            await configured(monkeypatch, url)

    asyncio.run(run())

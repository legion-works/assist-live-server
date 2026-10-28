import asyncio
from contextlib import asynccontextmanager

import pytest


def test_three_health_arguments_keep_positions_disabled():
    from assist_live_client import HealthCapabilities

    assert HealthCapabilities(True, True, True).text_positions is False
    assert HealthCapabilities(False, False, False).text_positions is False
    assert HealthCapabilities(True, True, True).local_only is None
    assert HealthCapabilities(True, True, True).destinations is None


@pytest.mark.parametrize(
    "stt,tts", [(False, True), (True, False), (True, True), (False, False)]
)
@pytest.mark.parametrize("ready", [True, False])
def test_health_capabilities_and_legacy_boolean(
    tmp_path, store, replay_store, stt, tts, ready, monkeypatch
):
    from types import SimpleNamespace

    from aiohttp import ClientSession, UnixConnector, web
    from assist_live_client import AssistLiveClient
    from assist_live_server.app import create_app
    from assist_live_server.audio_gateway import AudioGateway
    from assist_live_server.auth import Registry
    from assist_live_server.providers.parakeet import ParakeetSession
    from assist_live_server.providers.piper import PiperProvider

    monkeypatch.setattr(PiperProvider, "destination", "local", raising=False)

    async def run():
        registry = Registry(replay_store=replay_store)
        registry.bind("satellite-a", "satellite-a", b"test-device-secret")
        registry.add_token("test-token", ["satellite-a"])
        runner = web.AppRunner(
            create_app(
                registry,
                store if ready else None,
                audio_gateway=AudioGateway(
                    lambda _: ParakeetSession(lambda _: ""), destination="local"
                )
                if stt
                else None,
                tts_provider=PiperProvider(
                    SimpleNamespace(config=SimpleNamespace(sample_rate=22050))
                )
                if tts
                else None,
            )
        )
        await runner.setup()
        path = tmp_path / "health.sock"
        await web.UnixSite(runner, path).start()
        try:
            async with ClientSession(connector=UnixConnector(path=path)) as session:
                client = AssistLiveClient(
                    "http://service.invalid", "test-token", session=session
                )
                assert await client.health() is ready
                async with session.get(
                    "http://service.invalid/health",
                    headers={"Authorization": "Bearer test-token"},
                ) as response:
                    assert await response.json() == {
                        "version": 1,
                        "healthy": ready,
                        "capabilities": {"stt": stt, "tts": tts, "text_positions": tts},
                        "local_only": None,
                        "destinations": {
                            **({"stt": "local"} if stt else {}),
                            **({"tts": "local"} if tts else {}),
                        },
                    }
                status = await client.health_capabilities()
                assert status.stt is stt
                assert status.tts is tts
                assert status.text_positions is tts
                assert status.healthy is ready
        finally:
            await runner.cleanup()

    asyncio.run(run())


@pytest.mark.parametrize("healthy", [True, False])
def test_old_health_body_has_no_audio_capabilities(tmp_path, healthy):
    from aiohttp import ClientSession, UnixConnector, web
    from assist_live_client import AssistLiveClient, HealthCapabilities

    async def run():
        async def health(request):
            return web.json_response({"version": 1, "healthy": healthy})

        app = web.Application()
        app.router.add_get("/health", health)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        path = tmp_path / "old-health.sock"
        await web.UnixSite(runner, path).start()
        try:
            async with ClientSession(connector=UnixConnector(path=path)) as session:
                client = AssistLiveClient(
                    "http://service.invalid", "test-token", session=session
                )
                assert await client.health() is healthy
                assert await client.health_capabilities() == HealthCapabilities(
                    healthy, False, False
                )
                assert not (await client.health_capabilities()).text_positions
                assert (await client.health_capabilities()).local_only is None
                assert (await client.health_capabilities()).destinations is None
        finally:
            await runner.cleanup()

    asyncio.run(run())


@asynccontextmanager
async def health_service(tmp_path, app):
    from aiohttp import ClientSession, UnixConnector, web
    from assist_live_client import AssistLiveClient

    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    path = tmp_path / "destinations.sock"
    await web.UnixSite(runner, path).start()
    try:
        async with ClientSession(connector=UnixConnector(path=path)) as session:
            yield (
                AssistLiveClient(
                    "http://service.invalid", "test-token", session=session
                ),
                session,
            )
    finally:
        await runner.cleanup()


@pytest.mark.parametrize("local_only", [True, False])
@pytest.mark.parametrize(
    "provider,want", [("parakeet", "local"), ("deepgram", "cloud")]
)
def test_health_reports_config_policy_and_registry_destination(
    tmp_path, store, replay_store, local_only, provider, want
):
    from assist_live_server.app import create_app
    from assist_live_server.auth import Registry
    from assist_live_server.config import ProviderFactory, ServerConfig

    async def run():
        if local_only and provider == "deepgram":
            with pytest.raises(ValueError, match="local only"):
                ServerConfig(stt_provider=provider, tts_provider=None, local_only=True)
            return
        config = ServerConfig(
            stt_provider=provider, tts_provider=None, local_only=local_only
        )
        registry = Registry(replay_store=replay_store)
        registry.bind("satellite-a", "satellite-a", b"test-device-secret")
        registry.add_token("test-token", ["satellite-a"])
        app = create_app(registry, store, providers=ProviderFactory(config))
        async with health_service(tmp_path, app) as (client, session):
            status = await client.health_capabilities()
            assert status.local_only is local_only
            assert status.destinations == {"stt": want}
            assert status.stt is True
            assert status.tts is False
            async with session.get("http://service.invalid/health") as response:
                assert response.status == 401
                assert await response.text() == "unauthorized"

    asyncio.run(run())


@pytest.mark.parametrize("mode", [None, "local", "cloud"])
def test_health_reports_checked_speaker_without_resolving(tmp_path, store, mode):
    from assist_live_server.app import create_app
    from assist_live_server.auth import Registry
    from assist_live_server.config import ProviderFactory, ServerConfig
    from assist_live_server.speaker import MurdockSpeaker

    async def forbidden(host, port):
        raise AssertionError("health must not resolve a speaker address")

    async def run():
        speaker = MurdockSpeaker(
            "http://speaker.invalid/identify",
            local_only=False,
            mode=mode,
            resolver=forbidden,
        )
        config = ServerConfig(
            stt_provider="parakeet",
            tts_provider=None,
            local_only=False,
            speaker_url="http://speaker.invalid/identify",
        )
        registry = Registry()
        registry.bind("satellite-a", "satellite-a", b"test-device-secret")
        registry.add_token("test-token", ["satellite-a"])
        app = create_app(
            registry, store, providers=ProviderFactory(config, speaker=speaker)
        )
        async with health_service(tmp_path, app) as (client, _):
            assert (await client.health_capabilities()).destinations == {
                "stt": "local",
                "speaker": "unknown",
            }

    asyncio.run(run())


def test_health_tts_only_and_no_speaker(tmp_path, store):
    from assist_live_server.app import create_app
    from assist_live_server.auth import Registry
    from assist_live_server.config import ProviderFactory, ServerConfig

    async def run():
        registry = Registry()
        registry.bind("satellite-a", "satellite-a", b"test-device-secret")
        registry.add_token("test-token", ["satellite-a"])
        config = ServerConfig(stt_provider=None, tts_provider="piper")
        async with health_service(
            tmp_path, create_app(registry, store, providers=ProviderFactory(config))
        ) as (client, _):
            status = await client.health_capabilities()
            assert status.local_only is True
            assert status.destinations == {"tts": "local"}
            assert status.stt is False
            assert status.tts is True

    asyncio.run(run())


@pytest.mark.parametrize(
    "host,address,want",
    [
        ("127.0.0.1", "127.0.0.1", "local"),
        ("192.0.2.1", "192.0.2.1", "cloud"),
        ("speaker.invalid", "127.0.0.1", "unknown"),
        ("speaker.invalid", "192.0.2.1", "unknown"),
    ],
)
def test_health_reports_loaded_speaker_address_class(
    tmp_path, store, host, address, want
):
    from assist_live_server.app import create_app
    from assist_live_server.auth import Registry
    from assist_live_server.config import ProviderFactory, ServerConfig

    resolutions = []

    async def resolve(host, port):
        resolutions.append(host)
        return (address,)

    async def run():
        registry = Registry()
        registry.bind("satellite-a", "satellite-a", b"test-device-secret")
        registry.add_token("test-token", ["satellite-a"])
        config = ServerConfig(
            stt_provider=None,
            tts_provider=None,
            local_only=False,
            speaker_url=f"http://{host}/identify",
        )
        providers = await ProviderFactory.load(config, resolver=resolve)
        checked = len(resolutions)
        async with health_service(
            tmp_path, create_app(registry, store, providers=providers)
        ) as (client, _):
            assert (await client.health_capabilities()).destinations == {
                "speaker": want
            }
            assert len(resolutions) == checked

    asyncio.run(run())


@pytest.fixture
def stopped_speaker_connection(monkeypatch):
    def stopped(*args, **kwargs):
        raise ConnectionError("synthetic connection stop")

    monkeypatch.setattr("assist_live_server.speaker.aiohttp.TCPConnector", stopped)


@asynccontextmanager
async def speaker_health_service(tmp_path, store, speaker):
    from assist_live_server.app import create_app
    from assist_live_server.auth import Registry
    from assist_live_server.config import ProviderFactory, ServerConfig

    registry = Registry()
    registry.bind("satellite-a", "satellite-a", b"test-device-secret")
    registry.add_token("test-token", ["satellite-a"])
    config = ServerConfig(
        stt_provider=None,
        tts_provider=None,
        local_only=False,
        speaker_url=str(speaker.url),
    )
    app = create_app(
        registry, store, providers=ProviderFactory(config, speaker=speaker)
    )
    async with health_service(tmp_path, app) as (client, _):
        yield client


@pytest.mark.parametrize("mode", ["local", "cloud"])
def test_speaker_mode_is_not_a_completed_check(mode):
    from assist_live_server.speaker import MurdockSpeaker

    speaker = MurdockSpeaker("http://127.0.0.1/identify", mode=mode)
    assert speaker.destination == mode
    assert speaker.checked_destination is None


def test_health_speaker_publishes_refused_literal_class(
    tmp_path, store, stopped_speaker_connection
):
    from assist_live_server.destinations import speaker_url
    from assist_live_server.speaker import MurdockSpeaker

    async def resolve(host, port):
        return (host,)

    async def run():
        speaker = MurdockSpeaker("http://127.0.0.1/identify", resolver=resolve)
        async with speaker_health_service(tmp_path, store, speaker) as client:
            with pytest.raises(ConnectionError, match="synthetic connection stop"):
                await speaker.identify(b"\x00\x00")
            assert (await client.health_capabilities()).destinations == {
                "speaker": "local"
            }
            speaker.url = speaker_url("http://192.0.2.1/identify")
            with pytest.raises(
                ValueError, match="destination refused by local only policy"
            ):
                await speaker.identify(b"\x00\x00")
            assert (await client.health_capabilities()).destinations == {
                "speaker": "cloud"
            }

    asyncio.run(run())


def test_health_speaker_is_unknown_during_check(
    tmp_path, store, stopped_speaker_connection
):
    from assist_live_server.speaker import MurdockSpeaker

    async def run():
        entered = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def resolve(host, port):
            nonlocal calls
            calls += 1
            if calls == 2:
                entered.set()
                await release.wait()
            return (host,)

        speaker = MurdockSpeaker("http://127.0.0.1/identify", resolver=resolve)
        async with speaker_health_service(tmp_path, store, speaker) as client:
            with pytest.raises(ConnectionError, match="synthetic connection stop"):
                await speaker.identify(b"\x00\x00")
            assert (await client.health_capabilities()).destinations == {
                "speaker": "local"
            }
            pending = asyncio.create_task(speaker.identify(b"\x00\x00"))
            try:
                await entered.wait()
                assert (await client.health_capabilities()).destinations == {
                    "speaker": "unknown"
                }
            finally:
                release.set()
                with pytest.raises(ConnectionError, match="synthetic connection stop"):
                    await pending
            assert (await client.health_capabilities()).destinations == {
                "speaker": "local"
            }
            assert calls == 2

    asyncio.run(asyncio.wait_for(run(), 5))


def test_health_speaker_newer_check_wins(tmp_path, store, stopped_speaker_connection):
    from assist_live_server.speaker import MurdockSpeaker

    async def run():
        older_entered = asyncio.Event()
        newer_entered = asyncio.Event()
        older_release = asyncio.Event()
        newer_release = asyncio.Event()
        calls = 0

        async def resolve(host, port):
            nonlocal calls
            calls += 1
            if calls == 1:
                older_entered.set()
                await older_release.wait()
                return ("127.0.0.1",)
            newer_entered.set()
            await newer_release.wait()
            return ("192.0.2.1",)

        speaker = MurdockSpeaker(
            "http://127.0.0.1/identify",
            local_only=False,
            resolver=resolve,
        )
        async with speaker_health_service(tmp_path, store, speaker) as client:
            older = asyncio.create_task(speaker.identify(b"\x00\x00"))
            newer = None
            try:
                await older_entered.wait()
                newer = asyncio.create_task(speaker.identify(b"\x00\x00"))
                await newer_entered.wait()
                newer_release.set()
                with pytest.raises(ConnectionError, match="synthetic connection stop"):
                    await newer
                assert (await client.health_capabilities()).destinations == {
                    "speaker": "cloud"
                }
                older_release.set()
                with pytest.raises(ConnectionError, match="synthetic connection stop"):
                    await older
                assert (await client.health_capabilities()).destinations == {
                    "speaker": "cloud"
                }
                assert calls == 2
            finally:
                older_release.set()
                newer_release.set()
                await asyncio.gather(
                    *(task for task in (older, newer) if task is not None),
                    return_exceptions=True,
                )

    asyncio.run(asyncio.wait_for(run(), 5))


def test_health_speaker_resolve_failure_clears_class(
    tmp_path, store, stopped_speaker_connection
):
    from assist_live_server.speaker import MurdockSpeaker

    async def run():
        calls = 0

        async def resolve(host, port):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("synthetic resolution failure")
            return (host,)

        speaker = MurdockSpeaker("http://127.0.0.1/identify", resolver=resolve)
        async with speaker_health_service(tmp_path, store, speaker) as client:
            with pytest.raises(ConnectionError, match="synthetic connection stop"):
                await speaker.identify(b"\x00\x00")
            assert (await client.health_capabilities()).destinations == {
                "speaker": "local"
            }
            with pytest.raises(OSError, match="synthetic resolution failure"):
                await speaker.identify(b"\x00\x00")
            assert (await client.health_capabilities()).destinations == {
                "speaker": "unknown"
            }

    asyncio.run(run())


@pytest.mark.parametrize("part", ["stt", "tts"])
def test_health_injected_provider_without_destination_is_unknown(tmp_path, store, part):
    from types import SimpleNamespace

    from assist_live_server.app import create_app
    from assist_live_server.audio_gateway import AudioGateway
    from assist_live_server.auth import Registry

    async def run():
        registry = Registry()
        registry.bind("satellite-a", "satellite-a", b"test-device-secret")
        registry.add_token("test-token", ["satellite-a"])
        options = (
            {"audio_gateway": AudioGateway(lambda _: None)}
            if part == "stt"
            else {"tts_provider": SimpleNamespace()}
        )
        async with health_service(tmp_path, create_app(registry, store, **options)) as (
            client,
            _,
        ):
            status = await client.health_capabilities()
            assert status.local_only is None
            assert status.destinations == {part: "unknown"}
            assert status.stt is (part == "stt")
            assert status.tts is (part == "tts")

    asyncio.run(run())


def test_health_legacy_backend_does_not_invent_destinations(tmp_path, store):
    from types import SimpleNamespace

    from assist_live_server.app import create_app
    from assist_live_server.auth import Registry

    async def run():
        registry = Registry()
        registry.bind("satellite-a", "satellite-a", b"test-device-secret")
        registry.add_token("test-token", ["satellite-a"])
        async with health_service(
            tmp_path, create_app(registry, store, backend=SimpleNamespace())
        ) as (client, _):
            status = await client.health_capabilities()
            assert status.local_only is None
            assert status.destinations == {"stt": "unknown", "tts": "unknown"}

    asyncio.run(run())


@pytest.mark.parametrize(
    "fields",
    [
        {"local_only": "true"},
        {"local_only": 1},
        {"destinations": []},
        {"destinations": {"stt": "remote"}},
        {"destinations": {"stt": False}},
        {"destinations": {"stt": []}},
    ],
)
def test_health_client_refuses_invalid_destination_fields(tmp_path, fields):
    from aiohttp import web
    from assist_live_client.protocol import ProtocolError

    async def health(request):
        return web.json_response({"version": 1, "healthy": True, **fields})

    async def run():
        app = web.Application()
        app.router.add_get("/health", health)
        async with health_service(tmp_path, app) as (client, _):
            with pytest.raises(ProtocolError):
                await client.health_capabilities()

    asyncio.run(run())


def test_health_client_accepts_unknown_keys_and_explicit_null(tmp_path):
    from aiohttp import web

    async def health(request):
        return web.json_response(
            {
                "version": 1,
                "healthy": True,
                "future": True,
                "capabilities": {"stt": True, "future": True},
                "local_only": None,
                "destinations": None,
            }
        )

    async def run():
        app = web.Application()
        app.router.add_get("/health", health)
        async with health_service(tmp_path, app) as (client, _):
            status = await client.health_capabilities()
            assert status.healthy and status.stt
            assert status.local_only is None
            assert status.destinations is None

    asyncio.run(run())


def test_health_client_keeps_unknown_destination_but_drops_future_parts(tmp_path):
    from aiohttp import web

    async def health(request):
        return web.json_response(
            {
                "version": 1,
                "healthy": True,
                "local_only": False,
                "destinations": {"stt": "unknown", "future": "local"},
            }
        )

    async def run():
        app = web.Application()
        app.router.add_get("/health", health)
        async with health_service(tmp_path, app) as (client, _):
            status = await client.health_capabilities()
            assert status.local_only is False
            assert status.destinations == {"stt": "unknown"}

    asyncio.run(run())


@pytest.mark.parametrize("destination", [None, "remote"])
def test_injected_tts_declared_invalid_destination_stays_refused(destination):
    from types import SimpleNamespace

    from assist_live_server.app import create_app
    from assist_live_server.auth import Registry

    with pytest.raises(ValueError, match="unknown TTS destination"):
        create_app(Registry(), tts_provider=SimpleNamespace(destination=destination))


@pytest.mark.parametrize("part", ["stt", "tts", "speaker"])
@pytest.mark.parametrize("invalid", ["remote", False, []])
def test_health_client_rejects_invalid_known_destination(tmp_path, part, invalid):
    from aiohttp import web
    from assist_live_client.protocol import ProtocolError

    async def health(request):
        return web.json_response(
            {
                "version": 1,
                "healthy": True,
                "destinations": {part: invalid, "synthetic-token-content": "local"},
            }
        )

    async def run():
        app = web.Application()
        app.router.add_get("/health", health)
        async with health_service(tmp_path, app) as (client, _):
            with pytest.raises(ProtocolError, match="^invalid health destinations$"):
                await client.health_capabilities()

    asyncio.run(run())


@pytest.mark.parametrize("extra_value", ["cloud", "remote", False, []])
def test_health_client_drops_hostile_destination_keys(tmp_path, extra_value):
    from aiohttp import web

    known = {"stt": "local", "tts": "cloud", "speaker": "unknown"}

    async def health(request):
        return web.json_response(
            {
                "version": 1,
                "healthy": True,
                "destinations": {
                    **known,
                    "synthetic-token-content": extra_value,
                    "synthetic-transcript-content": "local",
                    "http://service.invalid": "unknown",
                },
            }
        )

    async def run():
        app = web.Application()
        app.router.add_get("/health", health)
        async with health_service(tmp_path, app) as (client, _):
            assert (await client.health_capabilities()).destinations == known

    asyncio.run(run())


@pytest.mark.parametrize("revision", ["8f3d60a", "eb35c61", "b4a9202"])
@pytest.mark.parametrize("healthy", [True, False])
def test_health_client_decodes_historical_bodies(tmp_path, revision, healthy):
    from aiohttp import web
    from assist_live_client import HealthCapabilities

    positions = revision == "b4a9202"

    async def health(request):
        return web.json_response(
            {
                "version": 1,
                "healthy": healthy,
                "capabilities": {
                    "stt": True,
                    "tts": True,
                    **({"text_positions": True} if positions else {}),
                },
            }
        )

    async def run():
        app = web.Application()
        app.router.add_get("/health", health)
        async with health_service(tmp_path, app) as (client, _):
            assert await client.health_capabilities() == HealthCapabilities(
                healthy, True, True, positions, None, None
            )

    asyncio.run(run())


@pytest.mark.parametrize("source", ["injected", "gateway", "legacy", "packaged"])
@pytest.mark.parametrize("part", ["stt", "tts"])
def test_timing_destinations_match_declared_sources(tmp_path, store, source, part):
    from uuid import uuid7

    from assist_live_client import TTSAudio, TTSFormat, TTSSentenceEnd, TurnOpen
    from assist_live_client.protocol import encode_control
    from assist_live_server.app import create_app
    from assist_live_server.audio_gateway import AudioGateway
    from assist_live_server.auth import Registry
    from assist_live_server.config import ProviderFactory, ServerConfig
    from assist_live_server.providers.parakeet import ParakeetSession
    from assist_live_server.tts_gateway import TTSGateway
    from assist_live_server.turn.detector import TurnDetector

    class STT:
        def session(self):
            return ParakeetSession(lambda _: "synthetic final")

    class TTS:
        async def stream(self, text, voice):
            assert [part async for part in text] == ["Synthetic text."]
            yield TTSFormat("pcm_s16le", 16000, 1, 2)
            yield TTSAudio(b"\x01\x00")
            yield TTSSentenceEnd(0)

    class Backend:
        audio_format = TTSFormat("pcm_s16le", 16000, 1, 2)

        async def stt(self, turn_id, audio):
            async for _ in audio:
                pass
            yield encode_control("final", turn_id, text="synthetic final")

        async def tts(self, turn_id, text, voice):
            assert [part async for part in text] == ["Synthetic text."]
            yield b"\x01\x00"
            yield encode_control("sentence_end", turn_id, sentence=0)

    async def run():
        registry = Registry()
        registry.bind("satellite-a", "satellite-a", b"synthetic-device-key")
        registry.add_token("test-token", ["satellite-a"])
        if source == "packaged":
            options = {
                "providers": ProviderFactory(
                    ServerConfig(stt_provider="parakeet", tts_provider="piper"),
                    STT(),
                    TTS(),
                )
            }
        elif source == "legacy":
            options = {"backend": Backend()}
        else:
            options = {
                "audio_gateway": AudioGateway(
                    lambda _: STT().session(),
                    detector_factory=lambda: TurnDetector(
                        vad_probability=lambda _: 0, turn_probability=lambda _: 0
                    ),
                ),
                **(
                    {"tts_provider": TTS()}
                    if source == "injected"
                    else {"tts_gateway": TTSGateway(lambda _: TTS())}
                ),
            }
        app = create_app(registry, store, **options)
        async with health_service(tmp_path, app) as (client, _):
            want = "local" if source == "packaged" else "unknown"
            turn_id = str(uuid7())
            await client.open_turn(
                TurnOpen(turn_id, "satellite-a", "voice", None, None, False)
            )

            async def audio():
                yield b"\x01\x00"

            async def text():
                yield "Synthetic text."

            if part == "stt":
                replies = [item async for item in client.stt(turn_id, audio())]
                assert any(item["type"] == "final" for item in replies)
            else:
                assert [item async for item in client.tts(turn_id, text(), "voice")]
            events = [
                row["event"] for row in store.rows if row["event"]["turn_id"] == turn_id
            ]
            expected = (
                {"stt_final"} if part == "stt" else {"tts_first_text", "tts_first_byte"}
            )
            assert expected <= {event["stage"] for event in events}
            metadata = [e["data"] for e in events if e["data"].get("part") == part]
            if source == "packaged":
                assert metadata
                assert all(data["destination"] == "local" for data in metadata)
                assert all(
                    data["provider"] == ("parakeet" if part == "stt" else "piper")
                    for data in metadata
                )
            else:
                assert all("destination" not in data for data in metadata)
            assert (await client.health_capabilities()).destinations == {
                "stt": want,
                "tts": want,
            }

    asyncio.run(asyncio.wait_for(run(), 15))

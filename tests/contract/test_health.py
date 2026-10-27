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
    tmp_path, store, replay_store, stt, tts, ready
):
    from types import SimpleNamespace

    from aiohttp import ClientSession, UnixConnector, web
    from assist_live_client import AssistLiveClient
    from assist_live_server.app import create_app
    from assist_live_server.audio_gateway import AudioGateway
    from assist_live_server.auth import Registry
    from assist_live_server.providers.parakeet import ParakeetSession
    from assist_live_server.providers.piper import PiperProvider

    async def run():
        registry = Registry(replay_store=replay_store)
        registry.bind("satellite-a", "satellite-a", b"test-device-secret")
        registry.add_token("test-token", ["satellite-a"])
        runner = web.AppRunner(
            create_app(
                registry,
                store if ready else None,
                audio_gateway=AudioGateway(lambda _: ParakeetSession(lambda _: ""))
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
                "speaker": mode or "unknown",
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
    "address,want", [("127.0.0.1", "local"), ("192.0.2.1", "cloud")]
)
def test_health_reports_loaded_speaker_address_class(tmp_path, store, address, want):
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
            speaker_url="http://speaker.invalid/identify",
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


def test_health_client_keeps_unknown_and_future_parts(tmp_path):
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
            assert status.destinations == {"stt": "unknown", "future": "local"}

    asyncio.run(run())

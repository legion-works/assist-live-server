import asyncio

import pytest


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
                        "capabilities": {"stt": stt, "tts": tts},
                    }
                status = await client.health_capabilities()
                assert status.stt is stt
                assert status.tts is tts
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
        finally:
            await runner.cleanup()

    asyncio.run(run())

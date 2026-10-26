import asyncio
import ipaddress
from dataclasses import replace

import pytest


def test_explicit_local_class_boundaries():
    from assist_live_server.destinations import _LOCAL_NETWORKS, classify_address

    classes = [
        (
            subnet.version,
            subnet.prefixlen,
            int(subnet.network_address) >> (24 if subnet.version == 4 else 112),
        )
        for subnet in _LOCAL_NETWORKS
    ]
    assert classes == [
        (4, 8, 10),
        (4, 12, 172),
        (4, 16, 192),
        (4, 16, 169),
        (4, 8, 127),
        (6, 7, 0xFC00),
        (6, 10, 0xFE80),
        (6, 128, 0),
    ]
    assert int(_LOCAL_NETWORKS[-1].network_address) == 1
    for subnet in _LOCAL_NETWORKS:
        address = subnet.network_address
        attribute = (
            "is_loopback"
            if address.is_loopback
            else "is_link_local"
            if address.is_link_local
            else "is_private"
        )
        assert getattr(address, attribute)
        assert not getattr(address - 1, attribute)
        assert not getattr(subnet.broadcast_address + 1, attribute)
        assert classify_address(str(address)) == "local"
        assert classify_address(str(next(subnet.hosts()))) == "local"
        assert classify_address(str(subnet.broadcast_address)) == "local"
        assert classify_address(str(address - 1)) == "cloud"
        assert classify_address(str(subnet.broadcast_address + 1)) == "cloud"


@pytest.mark.parametrize(
    "address",
    [
        "192.0.2.1",
        "198.18.0.1",
        ipaddress.IPv4Address((100 << 24) | (64 << 16) | 1),
        "0.0.0.0",
        "2001:db8::1",
        "::",
        "::ffff:127.0.0.1",
        "ff02::1",
        "224.0.0.1",
    ],
)
def test_other_non_global_ranges_are_not_local(address):
    from assist_live_server.destinations import classify_address

    assert classify_address(str(address)) == "cloud"


def test_config_and_request_refuse_cloud_before_construction(monkeypatch):
    from assist_live_server.config import ProviderFactory, ServerConfig
    from assist_live_server.providers.deepgram import DeepgramProvider
    from assist_live_server.providers.elevenlabs import ElevenLabsProvider

    def forbidden(*args, **kwargs):
        raise AssertionError("unselected cloud constructed")

    monkeypatch.setattr(DeepgramProvider, "__init__", forbidden)
    monkeypatch.setattr(ElevenLabsProvider, "__init__", forbidden)
    monkeypatch.setenv("DEEPGRAM_API_KEY", "test-key")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")

    async def run():
        config = await ServerConfig.load(stt_provider=None, tts_provider=None)
        factory = await ProviderFactory.load(config)
        assert factory.config.record_transcripts is False
        for key, name in [("stt_provider", "deepgram"), ("tts_provider", "elevenlabs")]:
            with pytest.raises(ValueError, match="local only"):
                await ServerConfig.load(**{key: name}, local_only=True)
        factory.config = replace(config, local_only=True)
        with pytest.raises(ValueError):
            factory.stt("deepgram")
        with pytest.raises(ValueError):
            factory.tts("elevenlabs")

        class Forbidden:
            def session(self):
                raise AssertionError("cloud session selected under local only")

        selected = ServerConfig(
            stt_provider="deepgram", tts_provider="elevenlabs", local_only=False
        )
        factory = ProviderFactory(selected, Forbidden(), Forbidden())
        # Simulate a policy change after construction to test the request guard.
        object.__setattr__(selected, "local_only", True)
        with pytest.raises(ValueError, match="local only"):
            factory.stt("deepgram")
        with pytest.raises(ValueError, match="local only"):
            factory.tts("elevenlabs")

    asyncio.run(run())


def test_configured_stt_factory_sends_only_to_selected_destination(monkeypatch):
    from aiohttp import WSMsgType, web
    from assist_live_server.audio_gateway import AudioGateway
    from assist_live_server.config import ProviderFactory, ServerConfig
    from assist_live_server.providers.deepgram import DeepgramProvider
    from assist_live_server.providers.parakeet import ParakeetProvider, ParakeetSession
    from assist_live_server.turn.detector import TurnDetector

    sent = []
    local_audio = []
    constructors = []
    original = DeepgramProvider.__init__

    async def run():
        async def handler(request):
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            async for message in ws:
                if message.type == WSMsgType.BINARY:
                    sent.append(message.data)
                elif message.json() == {"type": "CloseStream"}:
                    await ws.send_json(
                        {
                            "type": "Results",
                            "is_final": True,
                            "channel": {
                                "alternatives": [{"transcript": "cloud final"}]
                            },
                        }
                    )
                    await ws.send_json({"type": "Metadata"})
                    await ws.close()
            return ws

        app = web.Application()
        app.router.add_get("/v1/listen", handler)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]

        def construct(self, key):
            constructors.append(True)
            original(self, key, endpoint=f"http://127.0.0.1:{port}/v1/listen")

        class Local:
            def session(self):
                def decode(audio):
                    local_audio.append(audio)
                    return "local final"

                return ParakeetSession(decode)

        async def load(cls, cache):
            return Local()

        monkeypatch.setattr(DeepgramProvider, "__init__", construct)
        monkeypatch.setattr(ParakeetProvider, "load", classmethod(load))
        monkeypatch.setenv("DEEPGRAM_API_KEY", "synthetic-key")
        try:
            for name in ("parakeet", "deepgram"):
                config = await ServerConfig.load(
                    stt_provider=name, tts_provider=None, local_only=False
                )
                factory = await ProviderFactory.load(config)
                gateway = AudioGateway(
                    factory.stt,
                    provider=name,
                    destination="local" if name == "parakeet" else "cloud",
                    local_only=False,
                    detector_factory=lambda: TurnDetector(
                        vad_probability=lambda _: 1, turn_probability=lambda _: 0
                    ),
                )

                async def audio():
                    yield b"\x01\x00"

                replies = [
                    r
                    async for r in gateway.stt(
                        "01900000-0000-7000-8000-000000000001", audio()
                    )
                ]
                assert next(r["text"] for r in replies if r["type"] == "final") == (
                    "local final" if name == "parakeet" else "cloud final"
                )
                if name == "parakeet":
                    assert sent == []
                    assert constructors == []
                    assert local_audio == [b"\x01\x00"]
                else:
                    assert sent == [b"\x01\x00"]
                    assert constructors == [True]
                    assert local_audio == [b"\x01\x00"]
        finally:
            await runner.cleanup()

    asyncio.run(asyncio.wait_for(run(), 10))


def test_config_checks_all_dns_answers():
    from assist_live_server.config import ServerConfig

    async def resolve(host, port):
        return ("127.0.0.1", "192.0.2.1")

    async def run():
        with pytest.raises(ValueError, match="local only"):
            await ServerConfig.load(
                speaker_url="http://speaker.invalid/api/speakers/verify",
                local_only=True,
                resolver=resolve,
            )

    asyncio.run(run())

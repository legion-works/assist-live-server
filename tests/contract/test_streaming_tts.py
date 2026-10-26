import asyncio

import pytest


def test_cloud_gateway_refuses_before_provider_or_text():
    from assist_live_server.tts_gateway import TTSGateway

    def forbidden(_):
        raise AssertionError("cloud factory reached")

    async def text():
        raise AssertionError("text consumed before refusal")
        yield ""

    async def run():
        gateway = TTSGateway(
            forbidden, provider="elevenlabs", destination="cloud", local_only=True
        )
        replies = [
            r
            async for r in gateway.stream(
                "01900000-0000-7000-8000-000000000001", text(), "voice"
            )
        ]
        assert [r["type"] for r in replies] == ["error"]
        assert replies[0]["code"] == "tts_failed"

    asyncio.run(run())


def test_configured_cloud_tts_factory_keeps_wire_contract(
    monkeypatch, tmp_path, turn_id, store, replay_store
):
    import base64

    from aiohttp import ClientSession, UnixConnector, web
    from assist_live_client import (
        AssistLiveClient,
        TTSAudio,
        TTSFormat,
        TTSSentenceEnd,
        TurnOpen,
    )
    from assist_live_server.app import create_app
    from assist_live_server.auth import Registry
    from assist_live_server.config import ProviderFactory, ServerConfig
    from assist_live_server.providers.elevenlabs import ElevenLabsProvider

    sent = []
    original = ElevenLabsProvider.__init__

    async def run():
        async def handler(request):
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            async for message in ws:
                value = message.json()
                sent.append(value)
                if value.get("text") == "Test. ":
                    await ws.send_json(
                        {
                            "audio": base64.b64encode(b"\x01\x00").decode(),
                            "alignment": {
                                "chars": list("Test. "),
                                "charStartTimesMs": list(range(6)),
                                "charDurationsMs": [1] * 6,
                            },
                        }
                    )
                if value == {"text": ""}:
                    await ws.send_json({"isFinal": True})
                    await ws.close()
            return ws

        fake = web.Application()
        fake.router.add_get("/v1/text-to-speech/test-voice/stream-input", handler)
        cloud = web.AppRunner(fake, access_log=None)
        await cloud.setup()
        site = web.TCPSite(cloud, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]

        def construct(self, key):
            original(self, key, endpoint=f"http://127.0.0.1:{port}")

        monkeypatch.setattr(ElevenLabsProvider, "__init__", construct)
        monkeypatch.setenv("ELEVENLABS_API_KEY", "synthetic-key")
        config = await ServerConfig.load(
            stt_provider=None, tts_provider="elevenlabs", local_only=False
        )
        providers = await ProviderFactory.load(config)
        registry = Registry(replay_store=replay_store)
        registry.bind("satellite-a", "satellite-a", b"synthetic-device-key")
        registry.add_token("synthetic-token", ["satellite-a"])
        runner = web.AppRunner(
            create_app(registry, store, providers=providers), access_log=None
        )
        await runner.setup()
        path = tmp_path / "tts.sock"
        await web.UnixSite(runner, path).start()
        try:
            async with ClientSession(connector=UnixConnector(path=path)) as session:
                client = AssistLiveClient(
                    "http://service.invalid", "synthetic-token", session=session
                )
                await client.open_turn(
                    TurnOpen(turn_id, "satellite-a", "tts_only", None, None)
                )

                async def text():
                    yield "Test. "

                assert [
                    item
                    async for item in client.tts_stream(turn_id, text(), "test-voice")
                ] == [
                    TTSFormat("pcm_s16le", 16000, 1, 2),
                    TTSAudio(b"\x01\x00"),
                    TTSSentenceEnd(0),
                ]
                status = await client.health_capabilities()
                assert status.tts and not status.stt and status.healthy
                assert sent == [
                    {"text": " ", "generation_config": {"chunk_length_schedule": [50]}},
                    {"text": "Test. "},
                    {"text": " ", "flush": True},
                    {"text": ""},
                ]
                events = [row["event"] for row in store.rows]
                assert [e["stage"] for e in events] == [
                    "tts_first_text",
                    "tts_first_byte",
                ]
                assert all(e["data"]["destination"] == "cloud" for e in events)
        finally:
            await runner.cleanup()
            await cloud.cleanup()

    asyncio.run(asyncio.wait_for(run(), 10))


@pytest.mark.parametrize(
    "failure", [None, "missing_end", "wrong_index", "missing_format"]
)
def test_legacy_backend_declares_native_format_and_finishes_sentences(
    tmp_path, turn_id, store, replay_store, failure
):
    from aiohttp import ClientSession, UnixConnector, web
    from assist_live_client import (
        AssistLiveClient,
        TTSAudio,
        TTSFormat,
        TTSSentenceEnd,
        TurnOpen,
    )
    from assist_live_client.protocol import ProtocolError, encode_control
    from assist_live_server.app import create_app
    from assist_live_server.auth import Registry
    from assist_live_server.tts_gateway import TTSGateway

    class Backend:
        audio_format = (
            TTSFormat("pcm_s16le", 22050, 1, 2) if failure != "missing_format" else None
        )

        async def tts(self, turn, text, voice):
            assert [chunk async for chunk in text] == ["First. Second."]
            yield b"\x01\x00"
            if failure != "missing_end":
                yield encode_control(
                    "sentence_end", turn, sentence=1 if failure == "wrong_index" else 0
                )
            if failure is None:
                yield b"\x02\x00"
                yield encode_control("sentence_end", turn, sentence=1)

    async def run():
        async def text():
            yield "First. Second."

        if failure is not None:
            gateway = TTSGateway(backend=Backend(), provider="legacy")
            replies = [
                item async for item in gateway.stream(turn_id, text(), "test-voice")
            ]
            assert isinstance(replies[-1], dict)
            assert replies[-1].get("code") == "tts_failed"
        registry = Registry(replay_store=replay_store)
        registry.bind("satellite-a", "satellite-a", b"synthetic-key")
        registry.add_token("test-token", ["satellite-a"])
        runner = web.AppRunner(create_app(registry, store, Backend()), access_log=None)
        await runner.setup()
        path = tmp_path / "legacy-tts.sock"
        await web.UnixSite(runner, path).start()
        try:
            async with ClientSession(connector=UnixConnector(path=path)) as session:
                client = AssistLiveClient(
                    "http://service.invalid", "test-token", session=session
                )
                await client.open_turn(
                    TurnOpen(turn_id, "satellite-a", "tts_only", None, None)
                )

                stream = client.tts_stream(turn_id, text(), "test-voice")
                if failure is None:
                    assert [item async for item in stream] == [
                        TTSFormat("pcm_s16le", 22050, 1, 2),
                        TTSAudio(b"\x01\x00"),
                        TTSSentenceEnd(0),
                        TTSAudio(b"\x02\x00"),
                        TTSSentenceEnd(1),
                    ]
                else:
                    with pytest.raises(ProtocolError):
                        [item async for item in stream]
        finally:
            await runner.cleanup()

    asyncio.run(asyncio.wait_for(run(), 5))


def test_typed_items_and_legacy_bytes(service, turn_id):
    async def run():
        from assist_live_client import TTSAudio, TTSFormat, TTSSentenceEnd, TurnOpen

        async def text():
            yield "test text"

        async with service() as (client, state):
            await client.open_turn(
                TurnOpen(turn_id, "satellite-a", "tts_only", None, None)
            )
            items = [
                item async for item in client.tts_stream(turn_id, text(), "test-voice")
            ]
            assert items == [
                TTSFormat("pcm_s16le", 16000, 1, 2),
                TTSAudio(b"\x01\x00"),
                TTSSentenceEnd(0),
            ]
            assert [
                chunk async for chunk in client.tts(turn_id, text(), "test-voice")
            ] == [b"\x01\x00"]

    asyncio.run(run())


def test_legacy_bytes_refuse_native_piper_before_audio(monkeypatch):
    from assist_live_client import AssistLiveClient
    from assist_live_client.protocol import ProtocolError

    async def wire(self, *args):
        yield {
            "type": "format",
            "codec": "pcm_s16le",
            "rate": 22050,
            "channels": 1,
            "width": 2,
        }
        yield b"\x01\x00"
        yield {"type": "sentence_end", "sentence": 0}

    monkeypatch.setattr(AssistLiveClient, "_audio", wire)

    async def text():
        yield "Test. "

    async def run():
        async with AssistLiveClient("http://service.invalid", "test-token") as client:
            with pytest.raises(
                ProtocolError, match="audio format needs the typed TTS stream"
            ):
                await anext(client.tts("unused", text(), "test-voice"))
            assert [
                item async for item in client.tts_stream("unused", text(), "test-voice")
            ]

    asyncio.run(run())


def test_native_format_schema_and_codec(turn_id):
    import json
    from pathlib import Path

    from assist_live_client.protocol import decode_control
    from jsonschema import Draft202012Validator

    schema = json.loads(
        (
            Path(__file__).resolve().parents[2] / "protocol/control.schema.json"
        ).read_text()
    )
    value = {
        "type": "format",
        "version": 1,
        "turn_id": turn_id,
        "codec": "pcm_s16le",
        "rate": 22050,
        "channels": 1,
        "width": 2,
    }
    assert not list(Draft202012Validator(schema).iter_errors(value))
    assert decode_control(json.dumps(value)) == value


@pytest.mark.parametrize(
    "failure", ["missing_format", "duplicate_format", "missing_end", "wrong_index"]
)
def test_typed_stream_rejects_incomplete_sentence_contract(monkeypatch, failure):
    from assist_live_client import AssistLiveClient
    from assist_live_client.protocol import ProtocolError

    async def wire(self, *args):
        fmt = {
            "type": "format",
            "codec": "pcm_s16le",
            "rate": 22050,
            "channels": 1,
            "width": 2,
        }
        if failure != "missing_format":
            yield fmt
        if failure == "duplicate_format":
            yield fmt
        yield b"\x01\x00"
        if failure != "missing_end":
            yield {
                "type": "sentence_end",
                "sentence": 1 if failure == "wrong_index" else 0,
            }

    monkeypatch.setattr(AssistLiveClient, "_audio", wire)

    async def text():
        yield "Test. "

    async def run():
        async with AssistLiveClient("http://service.invalid", "test-token") as client:
            with pytest.raises(ProtocolError):
                [
                    item
                    async for item in client.tts_stream("unused", text(), "test-voice")
                ]

    asyncio.run(run())


@pytest.mark.parametrize(
    "provider_id,destination", [("piper", "local"), ("custom-cloud", "cloud")]
)
def test_local_provider_route_streams_before_text_end_and_marks_each_sentence(
    tmp_path, turn_id, store, provider_id, destination
):
    from types import SimpleNamespace

    from aiohttp import ClientSession, UnixConnector, web
    from assist_live_client import (
        AssistLiveClient,
        TTSAudio,
        TTSFormat,
        TTSSentenceEnd,
        TurnOpen,
    )
    from assist_live_server.app import create_app
    from assist_live_server.auth import Registry
    from assist_live_server.providers.piper import PiperProvider

    class Voice:
        config = SimpleNamespace(sample_rate=22050)

        def synthesize(self, text):
            yield SimpleNamespace(audio_int16_bytes=text.encode("utf-16le"))

    async def run():
        registry = Registry()
        registry.bind("satellite-a", "satellite-a", b"test-device-secret")
        registry.add_token("test-token", ["satellite-a"])
        provider = PiperProvider(Voice())
        provider.provider_id = provider_id
        provider.destination = destination
        runner = web.AppRunner(create_app(registry, store, tts_provider=provider))
        await runner.setup()
        path = tmp_path / "tts.sock"
        await web.UnixSite(runner, path).start()
        gate = asyncio.Event()

        async def text():
            yield "First. "
            await gate.wait()
            yield "Tail"

        try:
            async with ClientSession(connector=UnixConnector(path=path)) as session:
                async with AssistLiveClient(
                    "http://service.invalid", "test-token", session=session
                ) as client:
                    await client.open_turn(
                        TurnOpen(turn_id, "satellite-a", "tts_only", None, None)
                    )
                    stream = client.tts_stream(turn_id, text(), "en_US-lessac-medium")
                    assert await anext(stream) == TTSFormat("pcm_s16le", 22050, 1, 2)
                    assert await anext(stream) == TTSAudio("First.".encode("utf-16le"))
                    assert not gate.is_set()
                    assert await anext(stream) == TTSSentenceEnd(0)
                    gate.set()
                    assert [item async for item in stream] == [
                        TTSAudio("Tail".encode("utf-16le")),
                        TTSSentenceEnd(1),
                    ]
                    assert store.rows
                    assert all(
                        row["event"]["data"]["provider"] == provider_id
                        for row in store.rows
                    )
        finally:
            await runner.cleanup()

    asyncio.run(asyncio.wait_for(run(), 5))

import asyncio

import pytest


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


def test_local_provider_route_streams_before_text_end_and_marks_each_sentence(
    tmp_path, turn_id, store
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
        runner = web.AppRunner(
            create_app(registry, store, tts_provider=PiperProvider(Voice()))
        )
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
        finally:
            await runner.cleanup()

    asyncio.run(asyncio.wait_for(run(), 5))

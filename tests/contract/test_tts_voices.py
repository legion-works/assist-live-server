import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from aiohttp import ClientSession, UnixConnector, web
from assist_live_client import AssistLiveClient, TTSAudio, TTSFormat, TurnOpen
from assist_live_client.protocol import encode_audio, encode_control
from assist_live_server.app import create_app
from assist_live_server.auth import Registry
from assist_live_server.providers.piper import PiperProvider


@asynccontextmanager
async def service(tmp_path, app):
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    path = tmp_path / "voices.sock"
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


@pytest.mark.parametrize("voice", [None, "", "en_US-lessac-medium", "unknown-voice"])
@pytest.mark.parametrize("wire", [False, True])
def test_piper_request_default_and_named_unknown_voice(
    tmp_path, store, turn_id, voice, wire
):
    spoken = []

    class Model:
        config = SimpleNamespace(sample_rate=22050)

        def synthesize(self, text):
            spoken.append(text)
            yield SimpleNamespace(audio_int16_bytes=b"\x01\0")

    async def text():
        yield "Hello. "

    async def run():
        registry = Registry()
        registry.bind("satellite-a", "satellite-a", b"synthetic-device-secret")
        registry.add_token("test-token", ["satellite-a"])
        async with service(
            tmp_path, create_app(registry, store, tts_provider=PiperProvider(Model()))
        ) as (client, session):
            await client.open_turn(
                TurnOpen(turn_id, "satellite-a", "tts_only", None, None)
            )
            if wire:
                async with session.ws_connect(
                    "http://service.invalid/v1/audio",
                    headers={"Authorization": "Bearer test-token"},
                ) as ws:
                    await ws.send_json(
                        encode_control(
                            "negotiate",
                            turn_id,
                            mode="tts",
                            capabilities=["tts"],
                            **({"voice": voice} if voice is not None else {}),
                        )
                    )
                    assert (await ws.receive_json())["type"] == "negotiated"
                    await ws.send_json(encode_control("text", turn_id, text="Hello. "))
                    await ws.send_json(encode_control("end", turn_id))
                    messages = []
                    while True:
                        message = await ws.receive()
                        if message.type == web.WSMsgType.BINARY:
                            messages.append(message.data)
                        else:
                            value = message.json()
                            messages.append(value)
                            if value["type"] in {"error", "end"}:
                                break
                    if voice == "unknown-voice":
                        assert messages == [
                            encode_control("error", turn_id, code="unknown_tts_voice")
                        ]
                    else:
                        assert encode_audio(turn_id, 0, b"\x01\0") in messages
            else:
                from assist_live_client.protocol import ProtocolError

                stream = client.tts_stream(
                    turn_id, text(), **({"voice": voice} if voice is not None else {})
                )
                if voice == "unknown-voice":
                    with pytest.raises(ProtocolError, match="unknown_tts_voice"):
                        [item async for item in stream]
                else:
                    items = [item async for item in stream]
                    assert items[0] == TTSFormat("pcm_s16le", 22050, 1, 2)
                    assert TTSAudio(b"\x01\0") in items
            assert spoken == ([] if voice == "unknown-voice" else ["Hello."])

    asyncio.run(asyncio.wait_for(run(), 5))


@pytest.mark.parametrize("method", ["tts", "tts_stream"])
@pytest.mark.parametrize("voice", [None, "", "test-voice"])
def test_client_optional_voice_wire_field(tmp_path, turn_id, method, voice):
    received = []

    async def peer(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        received.append(await ws.receive_json())
        await ws.send_json(encode_control("negotiated", turn_id, capabilities=["tts"]))
        while (await ws.receive_json())["type"] != "end":
            pass
        await ws.send_json(
            encode_control(
                "format", turn_id, codec="pcm_s16le", rate=16000, channels=1, width=2
            )
        )
        await ws.send_bytes(encode_audio(turn_id, 0, b"\x01\0"))
        await ws.send_json(encode_control("sentence_end", turn_id, sentence=0))
        await ws.send_json(encode_control("end", turn_id))
        await ws.close()
        return ws

    async def text():
        yield "Hello. "

    async def run():
        app = web.Application()
        app.router.add_get("/v1/audio", peer)
        async with service(tmp_path, app) as (client, _):
            stream = getattr(client, method)(turn_id, text(), voice)
            assert [item async for item in stream]
        assert len(received) == 1
        if voice is None:
            assert "voice" not in received[0]
        else:
            assert received[0]["voice"] == voice

    asyncio.run(asyncio.wait_for(run(), 5))

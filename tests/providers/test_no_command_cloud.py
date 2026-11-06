import asyncio

import pytest
from aiohttp import WSMsgType, web
from assist_live_server.audio_gateway import AudioGateway
from assist_live_server.providers.deepgram import DeepgramProvider
from assist_live_server.providers.openai_stt import OpenAIProvider
from assist_live_server.turn.detector import TurnDetector

TURN = "01900000-0000-7000-8000-000000000001"


@pytest.mark.parametrize("provider", ["deepgram", "openai"])
@pytest.mark.parametrize("audio_ms", [200, 5000])
def test_no_command_cloud_closes_without_final_request(
    cloud_ws_server, monkeypatch, provider, audio_ms
):
    async def run():
        controls = []
        closed = asyncio.Event()

        async def handler(request):
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            async for message in ws:
                if message.type == WSMsgType.TEXT:
                    controls.append(message.json()["type"])
            closed.set()
            return ws

        async with cloud_ws_server(handler) as fake:
            cls = DeepgramProvider if provider == "deepgram" else OpenAIProvider
            session = cls("cloud-test-credential", endpoint=fake.url).session()

            async def forbidden_finish():
                raise AssertionError("cloud finish without command start")

            monkeypatch.setattr(session, "finish", forbidden_finish)
            gateway = AudioGateway(
                lambda _: session,
                provider=provider,
                local_only=False,
                destination="cloud",
                detector_factory=lambda: TurnDetector(
                    vad_probability=lambda _: 0, turn_probability=lambda _: 0
                ),
            )

            async def audio():
                yield bytes(audio_ms * 32)

            replies = []
            try:
                async for reply in gateway.stt(TURN, audio()):
                    replies.append(reply)
                    if reply["type"] == "final":
                        assert session._client.closed
                        assert session._ws.closed
                        assert session._reader.done()
                await asyncio.wait_for(closed.wait(), 2)
                assert [r["text"] for r in replies if r["type"] == "final"] == [""]
                assert "input_audio_buffer.commit" not in controls
                assert "CloseStream" not in controls
                assert session._cancelled
                assert session._closing.done()
            finally:
                await session.aclose()

    asyncio.run(asyncio.wait_for(run(), 5))

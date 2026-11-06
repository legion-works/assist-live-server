import asyncio
import base64

import numpy as np
import pytest
from aiohttp import WSMsgType, web
from assist_live_server.audio_gateway import AudioGateway
from assist_live_server.providers.deepgram import DeepgramProvider
from assist_live_server.providers.openai_stt import OpenAIProvider
from assist_live_server.turn.detector import TurnDetector

TURN = "01900000-0000-7000-8000-000000000001"


@pytest.mark.parametrize("provider", ["openai", "deepgram"])
@pytest.mark.parametrize("chunk", [320, 640, 1024, 2048])
@pytest.mark.parametrize("end", ["detector", "client"])
def test_cloud_wire_receives_no_audio_after_turn_end(
    cloud_ws_server, provider, chunk, end
):
    pcm = np.ones(8192, dtype="<i2").tobytes() + bytes(60000)
    expected_samples = 32256 if end == "detector" else 9003
    if end == "client":
        pcm = pcm[: expected_samples * 2]

    async def run():
        received = bytearray()

        async def handler(request):
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            async for message in ws:
                if message.type == WSMsgType.BINARY:
                    received.extend(message.data)
                    continue
                control = message.json()
                if control["type"] == "input_audio_buffer.append":
                    received.extend(base64.b64decode(control["audio"]))
                elif control["type"] == "input_audio_buffer.commit":
                    await ws.send_json(
                        {"type": "input_audio_buffer.committed", "item_id": "turn"}
                    )
                    await ws.send_json(
                        {
                            "type": (
                                "conversation.item.input_audio_transcription.completed"
                            ),
                            "item_id": "turn",
                            "transcript": "exact turn",
                        }
                    )
                elif control["type"] == "CloseStream":
                    await ws.send_json(
                        {
                            "type": "Results",
                            "is_final": True,
                            "speech_final": True,
                            "channel": {"alternatives": [{"transcript": "exact turn"}]},
                        }
                    )
                    await ws.send_json({"type": "Metadata"})
                    await ws.close()
            return ws

        async with cloud_ws_server(handler) as fake:
            cls = OpenAIProvider if provider == "openai" else DeepgramProvider
            session = cls("cloud-test-credential", endpoint=fake.url).session()
            gateway = AudioGateway(
                lambda _: session,
                provider=provider,
                destination="cloud",
                local_only=False,
                detector_factory=lambda: TurnDetector(
                    vad_probability=lambda a: float(np.any(a)),
                    turn_probability=lambda _: 0,
                ),
            )

            async def audio():
                for offset in range(0, len(pcm), chunk):
                    yield pcm[offset : offset + chunk]

            try:
                replies = [r async for r in gateway.stt(TURN, audio())]
                assert [r["text"] for r in replies if r["type"] == "final"] == [
                    "exact turn"
                ]
                if provider == "deepgram":
                    assert received == pcm[: expected_samples * 2], (
                        "cloud received a lost or extra sample"
                    )
                else:
                    assert len(received) // 2 == expected_samples * 3 // 2, (
                        "24 kHz cloud audio must end at the 16 kHz turn end"
                    )
                    assert received == (
                        np.ones(12288, dtype="<i2").tobytes()
                        + bytes((expected_samples * 3 // 2 - 12288) * 2)
                    ), "cloud resampling must keep the exact turn prefix"
            finally:
                await session.aclose()

    asyncio.run(asyncio.wait_for(run(), 5))

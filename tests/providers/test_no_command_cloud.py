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


@pytest.mark.parametrize("provider", ["deepgram", "openai"])
def test_no_command_refusal_at_end_decision_gives_stt_failed(
    cloud_ws_server, monkeypatch, provider
):
    key = "cloud-test-credential"

    async def run():
        controls = []
        release = asyncio.Event()

        async def handler(request):
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            sent = False
            async for message in ws:
                if message.type == WSMsgType.TEXT:
                    controls.append(message.json()["type"])
                if sent or (
                    message.type == WSMsgType.TEXT
                    and controls[-1] != "input_audio_buffer.append"
                ):
                    continue
                sent = True
                # Send the result only after the gateway stops partial reads.
                await release.wait()
                if provider == "deepgram":
                    await ws.send_json(
                        {
                            "type": "Results",
                            "is_final": True,
                            "channel": {"alternatives": [{"transcript": key}]},
                        }
                    )
                else:
                    await ws.send_json(
                        {
                            "type": "conversation.item.input_audio_transcription.delta",
                            "item_id": "selected",
                            "delta": key,
                        }
                    )
            return ws

        async with cloud_ws_server(handler) as fake:
            cls = DeepgramProvider if provider == "deepgram" else OpenAIProvider
            session = cls(key, endpoint=fake.url).session()

            finish_calls = []
            observed = []

            async def forbidden_finish():
                finish_calls.append(True)
                return ""

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

            async def record(stage, data):
                # Hold the end decision until the adapter has seen the result.
                # Record the state, but do not raise here: the gateway would
                # change an error in this callback into the expected failure.
                if stage == "end_decision":
                    release.set()
                    for _ in range(200):
                        if session._reader is not None and session._reader.done():
                            break
                        await asyncio.sleep(0.01)
                    observed.append(session.transcript_refused)

            async def audio():
                yield bytes(200 * 32)

            try:
                replies = [
                    reply async for reply in gateway.stt(TURN, audio(), record=record)
                ]
            finally:
                await session.aclose()
            # The refusal itself, not a setup error, must cause the failure.
            assert observed == [True], "adapter did not refuse at the end decision"
            assert finish_calls == []
            assert [r["type"] for r in replies] == ["end_decision", "error"], replies
            assert replies[-1]["code"] == "stt_failed"
            assert not any(r["type"] == "final" for r in replies)
            assert all(key not in str(r) for r in replies)
            assert "input_audio_buffer.commit" not in controls
            assert "CloseStream" not in controls

    asyncio.run(asyncio.wait_for(run(), 5))

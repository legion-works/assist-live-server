import asyncio

import pytest
from aiohttp import web
from aiohttp._websocket.writer import WebSocketWriter
from assist_live_client.protocol import encode_audio, encode_control
from assist_live_server import app
from assist_live_server.audio_gateway import AudioGateway
from assist_live_server.providers.parakeet import ParakeetSession

from tests.contract.test_streaming_stt import connect, service
from tests.stt_fakes import FirstBlockCommandDetector

# Hang guard only: service startup and cleanup under host load are not timed behavior.
HANG_GUARD = 60


@pytest.mark.parametrize(
    "phase", ["negotiated", "heartbeat", "final", "post_end", "close_drain"]
)
def test_nonreading_audio_peer_releases_connection_and_turn(
    tmp_path, monkeypatch, phase
):
    monkeypatch.setattr(app, "MAX_AUTH_CONNECTIONS", 1)
    monkeypatch.setattr(app, "MESSAGE_DEADLINE", 0.05)
    monkeypatch.setattr(app, "HEARTBEAT_INTERVAL", 0.01)
    monkeypatch.setattr(app, "TURN_TIMEOUT", 0.15 if phase == "post_end" else 60)

    async def run():
        blocked = asyncio.Event()
        finished = asyncio.Event()
        cancelled = asyncio.Event()
        release_close = asyncio.Event()
        closes = []
        original_send = web.WebSocketResponse.send_json
        original_close = web.WebSocketResponse.close
        original_writer_close = WebSocketWriter.close
        blocked_writer = None

        async def writer_close(writer, *args, **kwargs):
            if writer is blocked_writer:
                await release_close.wait()
            return await original_writer_close(writer, *args, **kwargs)

        async def send(ws, value, *args, **kwargs):
            nonlocal blocked_writer
            if value["type"] == ("heartbeat" if phase == "close_drain" else phase):
                if phase == "close_drain":
                    blocked_writer = ws._writer
                blocked.set()
                await asyncio.Event().wait()
            return await original_send(ws, value, *args, **kwargs)

        async def close(ws, *args, **kwargs):
            closes.append((kwargs.get("code"), kwargs.get("message")))
            asyncio.current_task().add_done_callback(lambda _: finished.set())
            return await original_close(ws, *args, **kwargs)

        class Session(ParakeetSession):
            async def finish(self):
                if phase == "post_end":
                    blocked.set()
                    await asyncio.Event().wait()
                return await super().finish()

            def cancel(self):
                cancelled.set()
                super().cancel()

        gateway = AudioGateway(
            lambda _: Session(lambda _: "final words"),
            detector_factory=FirstBlockCommandDetector,
        )
        monkeypatch.setattr(web.WebSocketResponse, "send_json", send)
        monkeypatch.setattr(web.WebSocketResponse, "close", close)
        monkeypatch.setattr(WebSocketWriter, "close", writer_close)
        async with service(tmp_path, gateway=gateway) as (_, session, turn, _):
            if phase == "negotiated":
                ws = await session.ws_connect(
                    "http://service.invalid/v1/audio",
                    headers={"Authorization": "Bearer synthetic-token"},
                )
                await ws.send_json(
                    encode_control(
                        "negotiate", turn.turn_id, mode="stt", capabilities=["stt"]
                    )
                )
            else:
                ws = await connect(session, turn)
                if phase not in {"heartbeat", "close_drain"}:
                    await ws.send_bytes(encode_audio(turn.turn_id, 0, bytes(1024)))
                    await ws.send_json(encode_control("end", turn.turn_id))
            try:
                await asyncio.wait_for(blocked.wait(), 1)
                await asyncio.wait_for(finished.wait(), 1)
                assert (1008, b"audio timeout") in closes
                if phase != "negotiated":
                    assert cancelled.is_set()
                monkeypatch.setattr(web.WebSocketResponse, "send_json", original_send)
                monkeypatch.setattr(web.WebSocketResponse, "close", original_close)
                other = await connect(session, turn)
                await other.close()
            finally:
                release_close.set()
                await ws.close()

    asyncio.run(asyncio.wait_for(run(), HANG_GUARD))


def test_end_decision_switches_inflight_input_read_to_final_wait(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "MESSAGE_DEADLINE", 0.03)

    async def run():
        final_wait = asyncio.Event()
        receive = web.WebSocketResponse.receive

        async def observed_receive(ws, *args, **kwargs):
            if kwargs.get("timeout") == app.TURN_TIMEOUT:
                final_wait.set()
            return await receive(ws, *args, **kwargs)

        class Detector(FirstBlockCommandDetector):
            def push(self, pcm, partial):
                super().push(pcm, partial)
                return [{"reason": "early"}]

        class Session(ParakeetSession):
            async def finish(self):
                await final_wait.wait()
                return "final words"

        gateway = AudioGateway(
            lambda _: Session(lambda _: ""), detector_factory=Detector
        )
        monkeypatch.setattr(web.WebSocketResponse, "receive", observed_receive)
        async with service(tmp_path, gateway=gateway) as (_, session, turn, _):
            ws = await connect(session, turn)
            await ws.send_bytes(encode_audio(turn.turn_id, 0, bytes(1024)))
            replies = [message.json() async for message in ws]
            assert [r["text"] for r in replies if r["type"] == "final"] == [
                "final words"
            ], "FINAL_WAIT_WAS_CUT_BY_LIVE_INPUT_DEADLINE"
            assert final_wait.is_set()

    asyncio.run(asyncio.wait_for(run(), HANG_GUARD))

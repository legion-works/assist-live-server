import asyncio
import threading
from contextlib import asynccontextmanager
from uuid import uuid7

import numpy as np
import pytest
from aiohttp import ClientSession, UnixConnector, WSMsgType, web
from assist_live_client import AssistLiveClient
from assist_live_client.models import TurnOpen
from assist_live_client.protocol import encode_audio, encode_control
from assist_live_server.app import create_app
from assist_live_server.audio_gateway import AudioGateway
from assist_live_server.auth import Registry
from assist_live_server.providers.parakeet import ParakeetSession
from assist_live_server.store import SQLiteStore
from assist_live_server.turn.detector import TurnDetector


@asynccontextmanager
async def service(tmp_path, factory=None, *, policy=False, gateway=None):
    registry = Registry()
    registry.bind("satellite-a", "satellite-a", b"synthetic-device-key")
    registry.add_token("synthetic-token", ["satellite-a"])
    store = SQLiteStore(tmp_path / "turns.db", record_transcripts=policy)
    if gateway is None:
        gateway = AudioGateway(
            factory or (lambda _: ParakeetSession(lambda _: "unchanged final")),
            detector_factory=lambda: TurnDetector(
                vad_probability=lambda a: float(np.any(a)),
                turn_probability=lambda _: 1,
            ),
        )
    runner = web.AppRunner(
        create_app(registry, store, audio_gateway=gateway, record_transcripts=policy),
        access_log=None,
    )
    await runner.setup()
    path = tmp_path / "stream.sock"
    await web.UnixSite(runner, path).start()
    try:
        async with ClientSession(connector=UnixConnector(path=path)) as session:
            async with AssistLiveClient(
                "http://service.invalid", "synthetic-token", session=session
            ) as client:
                turn = TurnOpen(
                    str(uuid7()), "satellite-a", "voice", None, None, policy
                )
                await client.open_turn(turn)
                yield client, session, turn, store
    finally:
        await runner.cleanup()
        store.close()


async def connect(session, turn):
    ws = await session.ws_connect(
        "http://service.invalid/v1/audio",
        headers={"Authorization": "Bearer synthetic-token"},
    )
    await ws.send_json(
        encode_control(
            "negotiate",
            turn.turn_id,
            mode="stt",
            capabilities=["stt", "speaker_result"],
        )
    )
    assert (await ws.receive_json())["type"] == "negotiated"
    return ws


def test_silence_without_frames_keeps_five_heartbeats(tmp_path):
    async def run():
        async with service(tmp_path) as (_, session, turn, _):
            ws = await connect(session, turn)
            messages = []
            async with asyncio.timeout(0.58):
                for _ in range(5):
                    messages.append(await ws.receive_json())
            assert sum(m["type"] == "heartbeat" for m in messages) >= 5
            await ws.close()

    asyncio.run(run())


def test_store_has_detection_partial_and_complete_with_transcript_policy(tmp_path):
    async def run():
        async with service(tmp_path, policy=True) as (client, session, turn, store):
            ws = await connect(session, turn)
            await ws.send_bytes(
                encode_audio(turn.turn_id, 0, np.ones(6400, dtype="<i2").tobytes())
            )
            async with asyncio.timeout(3):
                while (await ws.receive_json())["type"] != "partial":
                    pass
            await ws.send_bytes(encode_audio(turn.turn_id, 1, bytes(32000)))
            replies = []
            async for message in ws:
                assert message.type == WSMsgType.TEXT
                replies.append(message.json())
            assert [r["type"] for r in replies if r["type"] != "heartbeat"] == [
                "end_decision",
                "final",
                "speaker_result",
                "end",
            ]
            assert (
                next(r["text"] for r in replies if r["type"] == "final")
                == "unchanged final"
            )
            record = await store.get_turn(turn.turn_id)
            events = record["events"]
            assert (
                "transcript"
                not in next(e for e in events if e["stage"] == "stt_partial")["data"]
            )
            stages = [e["stage"] for e in events]
            for stage in [
                "speech_start",
                "end_candidate",
                "end_decision",
                "stt_partial",
                "stt_final",
                "speaker_result",
            ]:
                assert stages.count(stage) == 1
            assert all(
                e["source"] == "server" and e["quality"] == "measured" for e in events
            )
            assert (
                next(e for e in events if e["stage"] == "stt_final")["data"][
                    "transcript"
                ]
                == "unchanged final"
            )

    asyncio.run(run())


def test_one_terminal_result_even_if_backend_repeats_it(tmp_path):
    class RepeatingGateway(AudioGateway):
        async def stt(self, turn_id, audio, *, record=None):
            for kind in ["final", "final", "error", "speaker_result", "speaker_result"]:
                fields = (
                    {"text": "unaltered"} if kind == "final" else {"code": "failed"}
                )
                if kind == "speaker_result":
                    fields = {"name": "", "distance": 0.0, "destination": "local"}
                yield encode_control(kind, turn_id, **fields)

    async def run():
        gateway = RepeatingGateway(lambda _: None)
        async with service(tmp_path, gateway=gateway) as (_, session, turn, store):
            ws = await connect(session, turn)
            replies = [message.json() async for message in ws]
            assert [r["type"] for r in replies] == ["final", "speaker_result", "end"]
            events = (await store.get_turn(turn.turn_id))["events"]
            assert [e["stage"] for e in events] == ["stt_final", "speaker_result"]

    asyncio.run(run())


def test_inclusive_audio_maximum_and_one_byte_above_it(tmp_path):
    async def run():
        async with service(tmp_path) as (client, session, turn, store):
            ws = await connect(session, turn)
            frame = encode_audio(turn.turn_id, 0, b"\x01\x00" * 16000)
            assert len(frame) == 32021
            await ws.send_bytes(frame)
            await ws.send_json(encode_control("end", turn.turn_id))
            replies = [m.json() async for m in ws]
            assert sum(r["type"] == "final" for r in replies) == 1
            assert not any(r["type"] == "error" for r in replies)
            later = TurnOpen(str(uuid7()), "satellite-a", "voice", None, None)
            await client.open_turn(later)
            ws = await connect(session, later)
            frame = encode_audio(later.turn_id, 0, bytes(32000)) + b"\x00"
            assert len(frame) == 32022
            await ws.send_bytes(frame)
            async with asyncio.timeout(2):
                message = await ws.receive()
                while message.type == WSMsgType.TEXT:
                    message = await ws.receive()
            assert message.type == WSMsgType.CLOSE and message.data == 1009
            await ws.close()

    asyncio.run(run())


def test_provider_failure_sends_one_terminal_error_and_no_final(tmp_path):
    def fail(pcm):
        raise RuntimeError("synthetic sensitive error text")

    async def run():
        async with service(tmp_path, lambda _: ParakeetSession(fail)) as (
            _,
            session,
            turn,
            store,
        ):
            ws = await connect(session, turn)
            await ws.send_bytes(encode_audio(turn.turn_id, 0, b"\x01\x00" * 16))
            await ws.send_json(encode_control("end", turn.turn_id))
            replies = [message.json() async for message in ws]
            assert [r for r in replies if r["type"] in {"error", "final"}] == [
                encode_control("error", turn.turn_id, code="stt_failed")
            ]
            events = (await store.get_turn(turn.turn_id))["events"]
            assert sum(e["stage"] == "stt_failed" for e in events) == 1
            assert not any(e["stage"] == "stt_final" for e in events)

    asyncio.run(run())


def test_endpoint_final_survives_sender_transport_error_during_close(tmp_path):
    from aiohttp import ClientConnectionResetError

    async def run():
        async with service(tmp_path) as (client, _, turn, _):
            cancelled = asyncio.Event()

            async def audio():
                yield b"\x01\x00" * 7000
                yield bytes(32000)
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    cancelled.set()
                    raise ClientConnectionResetError(
                        "synthetic closing transport"
                    ) from None

            replies = [r async for r in client.stt(turn.turn_id, audio())]
            assert cancelled.is_set()
            assert [r["text"] for r in replies if r["type"] == "final"] == [
                "unchanged final"
            ]

    asyncio.run(run())


@pytest.mark.parametrize("error", ["transport", "source"])
def test_sender_error_before_final_is_not_ignored(tmp_path, error):
    from aiohttp import ClientConnectionResetError

    async def run():
        async with service(tmp_path) as (client, _, turn, _):

            async def audio():
                yield b"\x01\x00" * 16
                if error == "transport":
                    raise ClientConnectionResetError("synthetic transport failure")
                raise RuntimeError("synthetic source failure")

            expected = (
                ClientConnectionResetError if error == "transport" else RuntimeError
            )
            with pytest.raises(expected):
                _ = [r async for r in client.stt(turn.turn_id, audio())]

    asyncio.run(run())


def test_non_transport_source_error_after_final_still_propagates(tmp_path):
    async def run():
        async with service(tmp_path) as (client, _, turn, _):

            async def audio():
                yield b"\x01\x00" * 7000
                yield bytes(32000)
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    raise RuntimeError("synthetic source cleanup failure") from None

            with pytest.raises(RuntimeError, match="source cleanup failure"):
                _ = [r async for r in client.stt(turn.turn_id, audio())]

    asyncio.run(run())


def test_final_stops_source_before_speaker_result_and_stream_close(tmp_path):
    async def run():
        stopped = asyncio.Event()
        pulls = []

        class Gateway(AudioGateway):
            async def stt(self, turn_id, audio, *, record=None):
                await anext(audio)
                yield encode_control("final", turn_id, text="complete")
                await asyncio.wait_for(stopped.wait(), 2)
                yield encode_control(
                    "speaker_result",
                    turn_id,
                    name="",
                    distance=0.0,
                    destination="local",
                )

        async def audio():
            try:
                pulls.append(1)
                yield b"\x01\x00" * 16
                await asyncio.Event().wait()
                pulls.append(2)
                yield b"\x01\x00" * 16
            finally:
                stopped.set()

        gateway = Gateway(lambda _: None)
        async with service(tmp_path, gateway=gateway) as (client, _, turn, _):
            async with asyncio.timeout(3):
                replies = [r async for r in client.stt(turn.turn_id, audio())]
            assert pulls == [1]
            assert stopped.is_set()
            assert [r["type"] for r in replies] == ["final", "speaker_result"]

    asyncio.run(run())


def test_disconnect_cancels_old_decode_without_writing_later_turn(tmp_path):
    async def run():
        loop = asyncio.get_running_loop()
        entered = asyncio.Event()
        cancelled = asyncio.Event()
        release = threading.Event()
        sessions = []

        class Session(ParakeetSession):
            def cancel(self):
                super().cancel()
                if self is sessions[0]:
                    cancelled.set()

        def factory(name):
            first = not sessions

            def decode(pcm):
                if first:
                    loop.call_soon_threadsafe(entered.set)
                    assert release.wait(5)
                return "old turn" if first else "later turn"

            session = Session(decode)
            sessions.append(session)
            return session

        try:
            async with service(tmp_path, factory) as (client, session, turn, store):
                ws = await connect(session, turn)
                await ws.send_bytes(encode_audio(turn.turn_id, 0, b"\x01\x00" * 7000))
                await asyncio.wait_for(entered.wait(), 2)
                await ws.close()
                await asyncio.wait_for(cancelled.wait(), 2)
                later = TurnOpen(str(uuid7()), "satellite-a", "voice", None, None)
                await client.open_turn(later)
                release.set()

                async def audio():
                    yield b"\x01\x00" * 7000

                replies = [r async for r in client.stt(later.turn_id, audio())]
                assert [r["text"] for r in replies if r["type"] == "final"] == [
                    "later turn"
                ]
                old_events = (await store.get_turn(turn.turn_id))["events"]
                new_events = (await store.get_turn(later.turn_id))["events"]
                assert not any(
                    e["stage"] in {"stt_final", "stt_partial"} for e in old_events
                )
                assert sum(e["stage"] == "stt_final" for e in new_events) == 1
                assert all(e["turn_id"] == later.turn_id for e in new_events)
        finally:
            release.set()

    asyncio.run(run())

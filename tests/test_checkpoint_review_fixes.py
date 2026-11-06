import asyncio
import threading
import time

import numpy as np
import pytest
from assist_live_client.protocol import encode_audio, encode_control
from assist_live_server.audio_gateway import AudioGateway
from assist_live_server.checkpoint_turn import CheckpointTurn
from assist_live_server.providers import stt
from assist_live_server.providers.parakeet import ParakeetSession
from assist_live_server.turn.detector import TurnDetector

from tests.contract.test_streaming_stt import connect, service

TURN = "01900000-0000-7000-8000-000000000001"
PCM = np.ones(8192, dtype="<i2").tobytes() + bytes(60000)


def test_four_checkpoint_turns_reserve_final_admission():
    async def run():
        entered = threading.Event()
        release = threading.Event()
        order = []

        def decode(prefix):
            entered.set()
            assert release.wait(3)
            order.append("checkpoint")
            return "checkpoint words"

        def final(prefix):
            order.append("final")
            return "final words"

        sessions = [ParakeetSession(decode, final) for _ in range(4)]
        for session in sessions:
            await session.push(PCM[:16384])
            session.speech_started()
        checkpoints = [
            asyncio.create_task(session.checkpoint(PCM[:12800])) for session in sessions
        ]
        finishing = None
        try:
            assert await asyncio.to_thread(entered.wait, 1)
            await asyncio.sleep(0)
            finishing = asyncio.create_task(sessions[0].finish())
            await asyncio.sleep(0)
            assert not finishing.done(), "FINAL_ADMISSION_RESERVED_FOR_CHECKPOINT_TURNS"
        finally:
            release.set()
            await asyncio.gather(*checkpoints, return_exceptions=True)
            if finishing is not None:
                result = await asyncio.gather(finishing, return_exceptions=True)
        assert result == ["final words"]
        assert order[:2] == ["checkpoint", "final"]

    asyncio.run(asyncio.wait_for(run(), 4))


def test_cancelled_queued_checkpoint_releases_its_slot_before_worker_finishes():
    async def run():
        entered = threading.Event()
        release = threading.Event()

        def held():
            entered.set()
            assert release.wait(3)

        blocker = asyncio.create_task(stt.run_decode(held))
        checkpoint = None
        try:
            assert await asyncio.to_thread(entered.wait, 1)
            session = ParakeetSession(lambda _: "checkpoint words")
            checkpoint = asyncio.create_task(session.checkpoint(PCM[:12800]))
            await asyncio.sleep(0)
            assert len(stt._QUEUE) == 1
            checkpoint.cancel()
            await asyncio.gather(checkpoint, return_exceptions=True)
            assert not stt._QUEUE, "CANCELLED_CHECKPOINT_MUST_RELEASE_QUEUED_ADMISSION"
            acquired = 0
            try:
                for _ in range(3):
                    assert stt._SLOTS.acquire(blocking=False)
                    acquired += 1
            finally:
                for _ in range(acquired):
                    stt._SLOTS.release()
        finally:
            release.set()
            if checkpoint is not None:
                checkpoint.cancel()
            await asyncio.gather(
                blocker, *([checkpoint] if checkpoint else []), return_exceptions=True
            )

    asyncio.run(asyncio.wait_for(run(), 4))


@pytest.mark.parametrize("blocked", ["admission", "decode"])
def test_session_checkpoint_has_its_own_one_second_deadline(blocked):
    async def run():
        entered = threading.Event()
        release = threading.Event()

        def held(*args):
            entered.set()
            assert release.wait(3)
            return "checkpoint words"

        blockers = []
        try:
            if blocked == "admission":
                blockers = [asyncio.create_task(stt.run_decode(held)) for _ in range(4)]
                assert await asyncio.to_thread(entered.wait, 1)
                await asyncio.sleep(0)
            session = ParakeetSession(held)
            started = time.monotonic()
            with pytest.raises(RuntimeError, match="checkpoint.*deadline") as error:
                await asyncio.wait_for(session.checkpoint(PCM[:12800]), 1.2)
            assert type(error.value).__name__ == "STTCheckpointDeadlineError"
            assert time.monotonic() - started <= 1.15
        finally:
            release.set()
            await asyncio.gather(*blockers, return_exceptions=True)

    asyncio.run(asyncio.wait_for(run(), 4))


def test_session_deadline_error_maps_to_checkpoint_failure_counter():
    async def run():
        class Session(ParakeetSession):
            async def checkpoint(self, prefix):
                raise stt.STTCheckpointDeadlineError(
                    "checkpoint decode deadline expired"
                )

        gateway = AudioGateway(
            lambda _: Session(lambda _: ""),
            detector_factory=lambda: TurnDetector(
                vad_probability=lambda _: 1, turn_probability=lambda _: 0
            ),
            end_rule="checkpoint",
        )

        async def audio():
            yield PCM

        replies = [r async for r in gateway.stt(TURN, audio())]
        assert replies[-1]["code"] == "stt_failed"
        assert gateway.checkpoint_deadline_failures_total == 1
        assert gateway.checkpoint_wall_limit_failures_total == 0

    asyncio.run(asyncio.wait_for(run(), 3))


def test_checkpoint_ui_waits_for_command_start():
    async def run():
        checkpoint_ready = asyncio.Event()
        inspected = asyncio.Event()
        replies = []

        class Session(ParakeetSession):
            async def checkpoint(self, prefix):
                checkpoint_ready.set()
                return "wake word text"

        gateway = AudioGateway(
            lambda _: Session(lambda _: ""),
            detector_factory=lambda: TurnDetector(
                vad_probability=lambda _: 0, turn_probability=lambda _: 0
            ),
            end_rule="checkpoint",
        )

        async def audio():
            yield bytes(12800)
            await inspected.wait()

        async def consume():
            replies.extend([r async for r in gateway.stt(TURN, audio())])

        task = asyncio.create_task(consume())
        try:
            await asyncio.wait_for(checkpoint_ready.wait(), 1)
            await asyncio.sleep(0.03)
        finally:
            inspected.set()
            await task
        assert not any(r["type"] == "partial" for r in replies)

    asyncio.run(asyncio.wait_for(run(), 3))


@pytest.mark.parametrize("past_result", ["ready", "queued"])
def test_checkpoint_ui_and_past_end_cancellation_use_processed_position(past_result):
    async def run():
        past_ready = threading.Event()
        turn = None
        published = []
        decided = False

        class Session(ParakeetSession):
            async def checkpoint(self, prefix):
                position = len(prefix) // 2
                if position > 14848:
                    past_ready.set()
                    if past_result == "queued" or position >= 25600:
                        await asyncio.Event().wait()
                return str(position)

        def score(_):
            assert past_ready.wait(1)
            return 1

        detector = TurnDetector(
            vad_probability=lambda a: float(np.any(a)), turn_probability=score
        )

        async def note(*args, **kwargs):
            pass

        async def decide(reason):
            nonlocal decided
            decided = True
            assert detector.samples == 14848
            pending = [
                t
                for q, t in turn.checkpoints.items()
                if q > detector.samples and not t.done()
            ]
            assert not pending, "PAST_END_CHECKPOINTS_CANCELLED_BEFORE_FINAL_ADMISSION"

        async def publish(text):
            published.append((int(text), detector.samples, decided))

        turn = CheckpointTurn(
            Session(lambda _: ""),
            detector,
            note=note,
            decide=decide,
            publish=publish,
            failure=lambda _: None,
            observe=lambda _: None,
            speaker_audio=None,
            limit=480000,
        )

        async def audio():
            for offset in range(0, len(PCM), 1024):
                yield PCM[offset : offset + 1024]

        assert await turn.run(audio(), timeout=3, grace=0.5) == "12800"
        assert published
        assert all(q <= position and q <= 14848 for q, position, _ in published), (
            "UI_PARTIAL_MUST_NOT_USE_AUDIO_PAST_END"
        )
        assert all(not after_decision for _, _, after_decision in published)

    asyncio.run(asyncio.wait_for(run(), 4))


@pytest.mark.parametrize("end_rule", ["partial", "checkpoint"])
def test_final_admission_refusal_counter_is_exported_in_both_modes(tmp_path, end_rule):
    async def run():
        before = stt.final_admission_refusals_total()
        entered = threading.Event()
        release = threading.Event()

        def held():
            entered.set()
            assert release.wait(3)

        class Session(ParakeetSession):
            async def partial(self):
                return ""

            async def checkpoint(self, prefix):
                return ""

        gateway = AudioGateway(
            lambda _: Session(lambda _: ""),
            detector_factory=lambda: TurnDetector(
                vad_probability=lambda _: 1, turn_probability=lambda _: 0
            ),
            end_rule=end_rule,
        )
        blockers = [asyncio.create_task(stt.run_decode(held)) for _ in range(4)]
        try:
            assert await asyncio.to_thread(entered.wait, 1)
            await asyncio.sleep(0)
            async with service(tmp_path, gateway=gateway) as (_, session, turn, _):
                ws = await connect(session, turn)
                await ws.send_bytes(encode_audio(turn.turn_id, 0, PCM[:18006]))
                await ws.send_json(encode_control("end", turn.turn_id))
                replies = [message.json() async for message in ws]
                assert any(r.get("code") == "stt_failed" for r in replies)
                assert not any(r["type"] == "final" for r in replies)
                assert stt.final_admission_refusals_total() == before + 1
                async with session.get(
                    "http://service.invalid/metrics",
                    headers={"Authorization": "Bearer synthetic-token"},
                ) as response:
                    metrics = await response.text()
                assert (
                    f"assist_live_stt_final_admission_refusals_total {before + 1}"
                    in metrics
                )
        finally:
            release.set()
            await asyncio.gather(*blockers, return_exceptions=True)

    asyncio.run(asyncio.wait_for(run(), 4))


def test_checkpoint_failure_counts_each_named_cause_and_refuses_others():
    gateway = AudioGateway.__new__(AudioGateway)
    gateway.checkpoint_deadline_failures_total = 0
    gateway.checkpoint_wall_limit_failures_total = 0

    gateway.checkpoint_failure("deadline")
    gateway.checkpoint_failure("wall_limit")

    assert gateway.checkpoint_deadline_failures_total == 1
    assert gateway.checkpoint_wall_limit_failures_total == 1
    with pytest.raises(ValueError, match="unknown checkpoint failure cause"):
        gateway.checkpoint_failure("other")
    assert gateway.checkpoint_wall_limit_failures_total == 1

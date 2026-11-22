import asyncio

import pytest
from assist_live_server import audio_gateway, checkpoint_turn
from assist_live_server.audio_gateway import AudioGateway
from assist_live_server.providers import stt
from assist_live_server.providers.parakeet import ParakeetSession

from tests.stt_fakes import FirstBlockCommandDetector

# Hang guard only: service startup and cleanup under host load are not timed behavior.
HANG_GUARD = 60

TURN = "01900000-0000-7000-8000-000000000001"


def test_refused_partial_keeps_last_text_and_retries_on_next_audio():
    async def run():
        partials = asyncio.Queue()
        refused = asyncio.Event()

        class Session(ParakeetSession):
            async def partial(self):
                try:
                    return await super().partial()
                except stt.STTBusyError:
                    refused.set()
                    raise

        session = Session(lambda _: "last partial", lambda _: "last final")
        gateway = AudioGateway(
            lambda _: session, detector_factory=FirstBlockCommandDetector
        )

        async def audio():
            yield bytes(12800)
            assert await partials.get() == "last partial"
            acquired = 0
            try:
                for _ in range(4):
                    assert stt._SLOTS.acquire(blocking=False)
                    acquired += 1
                yield bytes(12800)
                await refused.wait()
            finally:
                for _ in range(acquired):
                    stt._SLOTS.release()
            yield bytes(12800)
            await retried.wait()

        retried = asyncio.Event()
        loop = asyncio.get_running_loop()
        original = session._decode

        def decode(pcm):
            if refused.is_set():
                loop.call_soon_threadsafe(retried.set)
            return original(pcm)

        session._decode = decode
        replies = []
        async for reply in gateway.stt(TURN, audio()):
            replies.append(reply)
            if reply["type"] == "partial":
                partials.put_nowait(reply["text"])
        assert [r["type"] for r in replies] == [
            "partial",
            "end_decision",
            "final",
            "speaker_result",
        ]
        assert next(r["text"] for r in replies if r["type"] == "final") == "last final"
        assert retried.is_set()
        assert gateway.stt_partial_refusals_total == 1

    asyncio.run(asyncio.wait_for(run(), HANG_GUARD))


@pytest.mark.parametrize("mode", ["partial", "checkpoint"])
@pytest.mark.parametrize("cause", ["callee", "wall"])
def test_only_expired_wall_timer_gets_wall_limit_cause(monkeypatch, mode, cause):
    monkeypatch.setattr(audio_gateway, "TURN_TIMEOUT", 0.05)

    async def run():
        stages = []

        class Session(ParakeetSession):
            pushes = 0

            async def push(self, pcm):
                self.pushes += 1
                if self.pushes == 2 and cause == "callee":
                    raise TimeoutError("synthetic provider deadline")
                await super().push(pcm)

        gateway = AudioGateway(
            lambda _: Session(lambda _: "final words"),
            detector_factory=FirstBlockCommandDetector,
            end_rule=mode,
        )

        async def record(stage, data):
            stages.append((stage, data))

        async def audio():
            yield bytes(2048 if cause == "callee" else 1024)
            await asyncio.Event().wait()

        replies = [r async for r in gateway.stt(TURN, audio(), record=record)]
        if cause == "wall" and mode == "partial":
            assert [r["type"] for r in replies] == [
                "end_decision",
                "final",
                "speaker_result",
            ]
            assert [
                data["reason"] for stage, data in stages if stage == "end_decision"
            ] == ["hard"]
        else:
            assert [r["type"] for r in replies] == ["error"]
            assert replies[0]["code"] == "stt_failed"
            assert not any(stage == "end_decision" for stage, _ in stages)
        assert gateway.checkpoint_wall_limit_failures_total == (
            mode == "checkpoint" and cause == "wall"
        )

    asyncio.run(asyncio.wait_for(run(), HANG_GUARD))


def test_callee_timeout_during_hard_end_is_not_a_grace_expiry(monkeypatch):
    monkeypatch.setattr(audio_gateway, "TURN_TIMEOUT", 0.02)

    class Detector(FirstBlockCommandDetector):
        def flush(self, partial):
            raise TimeoutError("synthetic detector deadline")

    async def run():
        gateway = AudioGateway(
            lambda _: ParakeetSession(lambda _: "final words"),
            detector_factory=Detector,
        )

        async def audio():
            yield bytes(1024)
            await asyncio.Event().wait()

        replies = [r async for r in gateway.stt(TURN, audio())]
        assert [r["type"] for r in replies] == ["error"]
        assert replies[0]["code"] == "stt_failed"

    asyncio.run(asyncio.wait_for(run(), HANG_GUARD))


@pytest.mark.parametrize("cause", ["callee", "timer", "session_timer"])
def test_only_checkpoint_timer_expiry_counts_as_deadline(monkeypatch, cause):
    monkeypatch.setattr(checkpoint_turn, "CHECKPOINT_TIMEOUT", 0.03)

    async def run():
        entered = asyncio.Event()

        class Session(ParakeetSession):
            async def checkpoint(self, pcm):
                entered.set()
                if cause == "callee":
                    raise TimeoutError("synthetic callee timeout")
                if cause == "session_timer":
                    raise stt.STTCheckpointDeadlineError("checkpoint deadline expired")
                await asyncio.Event().wait()

        gateway = AudioGateway(
            lambda _: Session(lambda _: ""),
            detector_factory=FirstBlockCommandDetector,
            end_rule="checkpoint",
        )

        async def audio():
            yield bytes(12800)
            await asyncio.Event().wait()

        replies = [r async for r in gateway.stt(TURN, audio())]
        assert entered.is_set()
        assert [r["type"] for r in replies] == ["error"]
        assert replies[0]["code"] == "stt_failed"
        assert gateway.checkpoint_deadline_failures_total == (cause != "callee"), (
            "CALLEE_TIMEOUT_IS_NOT_CHECKPOINT_TIMER_EXPIRY"
        )
        assert gateway.checkpoint_wall_limit_failures_total == 0

    asyncio.run(asyncio.wait_for(run(), HANG_GUARD))

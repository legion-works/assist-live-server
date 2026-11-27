import asyncio
import threading

import numpy as np
import pytest
from assist_live_client import TimingEvent, TurnOpen
from assist_live_server import audio_gateway
from assist_live_server.audio_gateway import AudioGateway
from assist_live_server.providers.parakeet import ParakeetSession
from assist_live_server.speaker import SpeakerResult
from assist_live_server.store import SQLiteStore
from assist_live_server.turn.detector import TurnDetector

from tests.stt_fakes import FirstBlockCommandDetector

# Hang guard only: service startup and cleanup under host load are not timed behavior.
HANG_GUARD = 60

TURN = "01900000-0000-7000-8000-000000000001"


@pytest.fixture
def stored_decision(tmp_path):
    async def run(gateway, audio):
        store = SQLiteStore(tmp_path / "turns.db")
        turn = TurnOpen(TURN, "satellite-a", "voice", None, None)

        async def record(stage, data):
            await store.append(
                TimingEvent(
                    TURN,
                    turn.satellite,
                    "server",
                    stage,
                    store.now_ns(),
                    "measured",
                    None,
                    data=data,
                )
            )

        try:
            await store.open_turn(turn)
            replies = [r async for r in gateway.stt(TURN, audio, record=record)]
            saved = await store.get_turn(TURN)
            assert saved is not None
            [decision] = [
                row["data"] for row in saved["events"] if row["stage"] == "end_decision"
            ]
            return replies, decision
        finally:
            store.close()

    return run


@pytest.mark.parametrize("mode", ["partial", "checkpoint"])
def test_speaker_and_final_get_same_end_sample_under_both_rules(mode, stored_decision):
    pcm = np.arange(1024, dtype="<i2").tobytes()
    speaker_inputs = []
    final_inputs = []

    class Detector(FirstBlockCommandDetector):
        def push(self, block, partial):
            super().push(block[: (700 - self.samples) * 2], partial)
            return [{"reason": "early"}] if self.samples == 700 else []

    class Speaker:
        destination = "local"

        async def identify(self, audio):
            speaker_inputs.append(audio)
            return SpeakerResult("", 0.0)

    def final(audio):
        final_inputs.append(audio)
        return "final words"

    async def run():
        async def audio():
            yield pcm

        gateway = AudioGateway(
            lambda _: ParakeetSession(lambda _: "", final),
            detector_factory=Detector,
            speaker=Speaker(),
            end_rule=mode,
        )
        return await stored_decision(gateway, audio())

    replies, decision = asyncio.run(asyncio.wait_for(run(), HANG_GUARD))
    assert [r["text"] for r in replies if r["type"] == "final"] == ["final words"]
    assert any(r["type"] == "speaker_result" for r in replies)
    assert final_inputs == [pcm[:1400]]
    assert speaker_inputs == final_inputs, "SPEAKER_MUST_USE_FINAL_AUDIO_PREFIX"
    assert decision["reason"] == "early"
    assert decision["end_sample"] == len(final_inputs[0]) // 2


@pytest.mark.parametrize("chunk", [320, 640, 1024, 2048])
@pytest.mark.parametrize("end", ["detector", "client", "no_command"])
def test_final_decode_uses_exact_turn_prefix(chunk, end, stored_decision):
    pcm = np.ones(8192, dtype="<i2").tobytes() + bytes(60000)
    expected_samples = 32256
    if end == "client":
        expected_samples = 9003
        pcm = pcm[: expected_samples * 2]
    elif end == "no_command":
        pcm = bytes(18006)
    final_audio = []
    detectors = []

    def detector():
        instance = TurnDetector(
            vad_probability=lambda a: float(np.any(a)),
            turn_probability=lambda _: 0,
        )
        detectors.append(instance)
        return instance

    def final(data):
        final_audio.append(data)
        return f"turn samples {len(data) // 2}"

    async def run():
        async def audio():
            for offset in range(0, len(pcm), chunk):
                yield pcm[offset : offset + chunk]

        gateway = AudioGateway(
            lambda _: ParakeetSession(lambda _: "", final),
            detector_factory=detector,
        )
        return await stored_decision(gateway, audio())

    replies, decision = asyncio.run(asyncio.wait_for(run(), HANG_GUARD))
    finals = [r["text"] for r in replies if r["type"] == "final"]
    assert decision["reason"] == ("max_silence" if end == "detector" else "input_end")
    if end == "no_command":
        assert final_audio == [], "no command must not run a final decode"
        assert finals == [""]
        assert decision["end_sample"] == len(pcm) // 2
    else:
        assert detectors[0].samples == expected_samples
        assert final_audio == [pcm[: expected_samples * 2]], (
            "final decode must receive the exact turn prefix, "
            "without a lost or extra sample"
        )
        assert finals == [f"turn samples {expected_samples}"]
        assert decision["end_sample"] == len(final_audio[0]) // 2


@pytest.fixture
def gateway_deadline(monkeypatch):
    deadlines = []
    expired = asyncio.Event()
    original_timeout = asyncio.timeout

    class Deadline(asyncio.Timeout):
        async def __aexit__(self, *args):
            try:
                return await super().__aexit__(*args)
            finally:
                if self.expired():
                    expired.set()

    def timeout(delay):
        if delay != audio_gateway.TURN_TIMEOUT:
            return original_timeout(delay)
        deadline = Deadline(None)
        deadlines.append(deadline)
        return deadline

    monkeypatch.setattr(audio_gateway.asyncio, "timeout", timeout)
    return deadlines, expired


@pytest.mark.parametrize(
    "end", ["wall_timeout", "audio_cap", "checkpoint_cap", "stalled_flush"]
)
def test_end_limit_uses_accepted_audio_prefix(
    monkeypatch, gateway_deadline, end, stored_decision
):
    pcm = np.ones(8192, dtype="<i2").tobytes()
    tail = np.arange(900, dtype="<i2").tobytes()
    final_audio = []
    deadlines, _ = gateway_deadline
    grace = []
    if end != "wall_timeout":
        monkeypatch.setattr(audio_gateway, "MAX_AUDIO_BYTES", len(pcm + tail))
    if end == "stalled_flush":
        original_timeout = asyncio.timeout

        def timeout(delay):
            if delay == audio_gateway.HARD_END_GRACE:
                deadline = asyncio.Timeout(None)
                grace.append(deadline)
                return deadline
            return original_timeout(delay)

        monkeypatch.setattr(audio_gateway.asyncio, "timeout", timeout)

    class Session(ParakeetSession):
        async def checkpoint(self, pcm):
            return ""

        async def push(self, block):
            if end == "stalled_flush" and block == tail[1024:]:
                grace[0].reschedule(asyncio.get_running_loop().time())
                await asyncio.Event().wait()
            await super().push(block)

    def final(data):
        final_audio.append(data)
        return f"turn samples {len(data) // 2}"

    async def run():
        async def audio():
            yield pcm
            yield tail if end == "wall_timeout" else tail + bytes(1024)
            if end == "wall_timeout":
                deadlines[0].reschedule(asyncio.get_running_loop().time())
            await asyncio.Event().wait()

        gateway = AudioGateway(
            lambda _: Session(lambda _: "", final),
            detector_factory=lambda: TurnDetector(
                vad_probability=lambda a: float(np.any(a)),
                turn_probability=lambda _: 0,
            ),
            end_rule="checkpoint" if end == "checkpoint_cap" else "partial",
        )
        return await stored_decision(gateway, audio())

    replies, decision = asyncio.run(asyncio.wait_for(run(), HANG_GUARD))
    expected = pcm + (tail[:1024] if end == "stalled_flush" else tail)
    assert final_audio == [expected]
    assert [r["text"] for r in replies if r["type"] == "final"] == [
        f"turn samples {len(expected) // 2}"
    ]
    assert decision["reason"] == ("input_end" if end == "checkpoint_cap" else "hard")
    assert decision["end_sample"] == len(final_audio[0]) // 2


@pytest.mark.parametrize("tail_samples", [388, 512])
def test_wall_timeout_waits_for_detector_before_flush(gateway_deadline, tail_samples):
    pcm = np.ones(8192, dtype="<i2").tobytes()
    tail = np.arange(tail_samples, dtype="<i2").tobytes()
    final_audio = []
    races = []
    release = threading.Event()
    active = threading.Event()
    deadlines, expired = gateway_deadline

    async def run():
        entered = asyncio.Event()
        flushed = asyncio.Event()
        loop = asyncio.get_running_loop()

        class Detector(TurnDetector):
            def push(self, block, partial):
                if block != tail:
                    return super().push(block, partial)
                active.set()
                loop.call_soon_threadsafe(entered.set)
                try:
                    assert release.wait(3), "detector barrier not released"
                    return super().push(block, partial)
                finally:
                    active.clear()

            def flush(self, partial):
                races.append(active.is_set())
                loop.call_soon_threadsafe(flushed.set)
                return super().flush(partial)

        def final(data):
            final_audio.append(data)
            return f"turn samples {len(data) // 2}"

        async def audio():
            yield pcm
            yield tail
            await asyncio.Event().wait()

        gateway = AudioGateway(
            lambda _: ParakeetSession(lambda _: "", final),
            detector_factory=lambda: Detector(
                vad_probability=lambda a: float(np.any(a)),
                turn_probability=lambda _: 0,
            ),
        )

        async def consume():
            return [r async for r in gateway.stt(TURN, audio())]

        task = asyncio.create_task(consume())
        try:
            await asyncio.wait_for(entered.wait(), 2)
            deadlines[0].reschedule(loop.time())
            await asyncio.wait_for(expired.wait(), 2)
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(flushed.wait(), 0.1)
        finally:
            release.set()
            replies = await asyncio.wait_for(task, 2)
        assert races == [False], "flush overlapped the detector worker"
        assert final_audio == [pcm + tail], "timeout lost or repeated received samples"
        assert [r["text"] for r in replies if r["type"] == "final"] == [
            f"turn samples {8192 + tail_samples}"
        ]

    asyncio.run(asyncio.wait_for(run(), HANG_GUARD))


@pytest.mark.parametrize(
    ("worker_state", "push_state"),
    [
        ("returned_during_cancel", "ready"),
        ("still_running", "ready"),
        ("returned_during_cancel", "stalled"),
        ("returned_during_cancel", "callee_timeout"),
    ],
)
def test_hard_grace_recovers_only_audio_from_finished_detector(
    monkeypatch, gateway_deadline, worker_state, push_state
):
    head = bytes(1024)
    tail = np.arange(512, dtype="<i2").tobytes()
    release = threading.Event()
    finished = threading.Event()
    final_audio = []
    speaker_audio = []
    pushes = []
    decisions = []
    deadlines, _ = gateway_deadline
    grace_deadlines = []
    timeout_with_wall = asyncio.timeout
    original_to_thread = asyncio.to_thread

    def timeout(delay):
        if delay != audio_gateway.HARD_END_GRACE:
            return timeout_with_wall(delay)
        when = None if grace_deadlines else asyncio.get_running_loop().time()
        deadline = asyncio.Timeout(when)
        grace_deadlines.append(deadline)
        return deadline

    async def to_thread(function, *args, **kwargs):
        work = asyncio.create_task(original_to_thread(function, *args, **kwargs))
        try:
            return await asyncio.shield(work)
        except asyncio.CancelledError:
            if worker_state == "returned_during_cancel":
                release.set()
                await work
            raise

    monkeypatch.setattr(audio_gateway.asyncio, "timeout", timeout)
    monkeypatch.setattr(audio_gateway.asyncio, "to_thread", to_thread)

    async def run():
        entered = asyncio.Event()
        push_cancelled = asyncio.Event()
        loop = asyncio.get_running_loop()

        class Detector(FirstBlockCommandDetector):
            def push(self, block, partial):
                events = super().push(block, partial)
                if block == tail:
                    loop.call_soon_threadsafe(entered.set)
                    try:
                        assert release.wait(HANG_GUARD), "detector barrier not released"
                    finally:
                        finished.set()
                return events

        class Speaker:
            destination = "local"

            async def identify(self, pcm):
                speaker_audio.append(pcm)
                return SpeakerResult("", 0.0)

        def final(pcm):
            final_audio.append(pcm)
            return "final words"

        class Session(ParakeetSession):
            async def push(self, block):
                pushes.append(block)
                if block == tail:
                    if push_state == "callee_timeout":
                        raise TimeoutError("synthetic catch-up push timeout")
                    if push_state == "stalled":
                        grace_deadlines[1].reschedule(loop.time())
                        try:
                            await asyncio.Event().wait()
                        finally:
                            push_cancelled.set()
                await super().push(block)

        detector = Detector()
        gateway = AudioGateway(
            lambda _: Session(lambda _: "", final),
            detector_factory=lambda: detector,
            speaker=Speaker(),
        )

        async def audio():
            yield head
            yield tail
            await asyncio.Event().wait()

        async def record(stage, data):
            if stage == "end_decision":
                decisions.append(data)

        async def consume():
            return [r async for r in gateway.stt(TURN, audio(), record=record)]

        task = asyncio.create_task(consume())
        try:
            await entered.wait()
            deadlines[0].reschedule(loop.time())
            replies = await asyncio.shield(task)
            assert detector.samples == 1024
            assert (worker_state == "returned_during_cancel") == finished.is_set()
            assert pushes == ([head, tail] if finished.is_set() else [head]), (
                "CATCHUP_MUST_NOT_REPEAT_PROVIDER_PUSH"
            )
            if push_state == "callee_timeout":
                assert [r["type"] for r in replies] == ["error"]
                assert replies[0]["code"] == "stt_failed"
                assert final_audio == speaker_audio == []
                assert decisions == []
                return
            expected = (
                head + tail if finished.is_set() and push_state == "ready" else head
            )
            assert final_audio == [expected], "FINISHED_DETECTOR_AUDIO_MUST_REACH_FINAL"
            [decision] = decisions
            assert decision["end_sample"] == len(final_audio[0]) // 2
            assert speaker_audio == [expected]
            assert push_cancelled.is_set() == (push_state == "stalled")
            assert [r["text"] for r in replies if r["type"] == "final"] == [
                "final words"
            ]
        finally:
            release.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(asyncio.wait_for(run(), HANG_GUARD))


@pytest.mark.parametrize("stall", ["provider_push", "detector_push", "detector_flush"])
def test_wall_timeout_bounds_stalled_operation(monkeypatch, gateway_deadline, stall):
    monkeypatch.setattr(audio_gateway, "HARD_END_GRACE", 0.05, raising=False)
    pcm = np.ones(8192, dtype="<i2").tobytes()
    tail = np.arange(512 if stall == "provider_push" else 388, dtype="<i2").tobytes()
    release = threading.Event()
    active = threading.Event()
    pushes = []
    flushes = []
    deadlines, expired = gateway_deadline

    async def run():
        loop = asyncio.get_running_loop()
        entered = asyncio.Event()
        ready = asyncio.Event()
        push_cancelled = asyncio.Event()

        class Detector(TurnDetector):
            def push(self, block, partial):
                if stall != "detector_push" or block != tail:
                    return super().push(block, partial)
                active.set()
                loop.call_soon_threadsafe(entered.set)
                try:
                    assert release.wait(3), "detector released only during cleanup"
                    return super().push(block, partial)
                finally:
                    active.clear()

            def flush(self, partial):
                flushes.append(active.is_set())
                if stall != "detector_flush":
                    return super().flush(partial)
                active.set()
                loop.call_soon_threadsafe(entered.set)
                try:
                    assert release.wait(3), "detector released only during cleanup"
                    return super().flush(partial)
                finally:
                    active.clear()

        class Session(ParakeetSession):
            async def push(self, block):
                pushes.append(block)
                if stall == "provider_push" and block == tail:
                    entered.set()
                    try:
                        await asyncio.Event().wait()
                    finally:
                        push_cancelled.set()
                await super().push(block)

        session = Session(lambda _: "", lambda _: "accepted prefix")
        gateway = AudioGateway(
            lambda _: session,
            detector_factory=lambda: Detector(
                vad_probability=lambda a: float(np.any(a)),
                turn_probability=lambda _: 0,
            ),
        )

        async def audio():
            yield pcm
            yield tail
            ready.set()
            await asyncio.Event().wait()

        async def consume():
            return [r async for r in gateway.stt(TURN, audio())]

        task = asyncio.create_task(consume())
        try:
            await asyncio.wait_for(
                ready.wait() if stall == "detector_flush" else entered.wait(), 2
            )
            deadlines[0].reschedule(loop.time())
            await asyncio.wait_for(expired.wait(), 2)
            await asyncio.wait_for(entered.wait(), 2)
            replies = await asyncio.wait_for(asyncio.shield(task), 0.3)
            assert [r["type"] for r in replies].count("end_decision") == 1
            assert [r["text"] for r in replies if r["type"] == "final"] == [
                "accepted prefix"
            ]
            assert flushes == ([False] if stall == "detector_flush" else [])
            assert pushes == [pcm[i : i + 1024] for i in range(0, len(pcm), 1024)] + (
                [tail] if stall == "provider_push" else []
            ), "timeout repeated a provider push"
            if stall == "provider_push":
                assert push_cancelled.is_set()
            else:
                assert active.is_set(), "turn waited for the stuck detector to return"
        finally:
            release.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(asyncio.wait_for(run(), HANG_GUARD))

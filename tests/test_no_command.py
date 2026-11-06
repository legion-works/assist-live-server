import asyncio

import numpy as np
import pytest
from assist_live_server.audio_gateway import AudioGateway
from assist_live_server.turn.detector import TurnDetector

TURN = "01900000-0000-7000-8000-000000000001"


class Session:
    final_redecodes = True

    def __init__(self):
        self.finish_calls = 0
        self.cancelled = False
        self.closed = False
        self.interim = ""

    async def push(self, pcm):
        pass

    async def partial(self):
        return self.interim

    def speech_started(self):
        pass

    async def finish(self):
        self.finish_calls += 1
        return "provider final text"

    def cancel(self):
        self.cancelled = True

    async def aclose(self):
        assert self.cancelled
        self.closed = True


class StartAndEndInOneBlock(TurnDetector):
    def push(self, pcm, partial):
        self.samples += len(pcm) // 2
        if self.speech_started_at_ms is None:
            self.speech_started_at_ms = 0.0
            return [{"reason": "max_silence"}]
        return []


class HardWithoutStart(TurnDetector):
    def push(self, pcm, partial):
        self.samples += len(pcm) // 2
        if self.samples >= 240000:
            return [{"reason": "hard"}]
        return []


@pytest.mark.parametrize(
    "case", ["noise_only", "all_zero", "wake_tail_320ms", "input_end", "hard_15s"]
)
def test_no_command_empty_final_without_provider_finish(case):
    async def run():
        session = Session()
        detector = TurnDetector(
            vad_probability=lambda a: float(np.any(a)),
            turn_probability=lambda _: 1,
        )
        pcm = bytes(160000)
        expected_reason = "max_silence"
        if case == "noise_only":
            pcm = (
                np.random.default_rng(38)
                .integers(-30, 31, 80000, dtype=np.int16)
                .tobytes()
            )
            detector.vad_probability = lambda _: 0.1
        elif case == "wake_tail_320ms":
            pcm = np.ones(5120, dtype="<i2").tobytes() + bytes(149760)
        elif case == "input_end":
            pcm = bytes(6400)
            expected_reason = "input_end"
        elif case == "hard_15s":
            pcm = bytes(480000)
            expected_reason = "hard"
            detector = HardWithoutStart(
                vad_probability=lambda _: 0.1, turn_probability=lambda _: 0
            )
        gateway = AudioGateway(lambda _: session, detector_factory=lambda: detector)
        notes = []

        async def record(stage, data):
            notes.append((stage, data))

        async def audio():
            for offset in range(0, len(pcm), 1024):
                yield pcm[offset : offset + 1024]

        replies = [r async for r in gateway.stt(TURN, audio(), record=record)]
        assert detector.speech_started_at_ms is None
        assert (
            next(data["reason"] for stage, data in notes if stage == "end_decision")
            == expected_reason
        )
        assert session.finish_calls == 0, "final decode without command start"
        assert [r["text"] for r in replies if r["type"] == "final"] == [""]
        assert session.cancelled and session.closed
        assert gateway.stt_final_replaced_total == 0

    asyncio.run(run())


def test_command_start_in_end_block_keeps_final():
    async def run():
        session = Session()
        detector = StartAndEndInOneBlock(
            vad_probability=lambda _: 0.1, turn_probability=lambda _: 0
        )
        gateway = AudioGateway(lambda _: session, detector_factory=lambda: detector)
        notes = []

        async def record(stage, data):
            notes.append((stage, data))

        async def audio():
            yield bytes(1024)

        replies = [r async for r in gateway.stt(TURN, audio(), record=record)]
        stages = [stage for stage, _ in notes]
        assert stages.index("speech_start") < stages.index("end_decision")
        assert session.finish_calls == 1, "command start in the end block lost"
        assert [r["text"] for r in replies if r["type"] == "final"] == [
            "provider final text"
        ]

    asyncio.run(run())


def test_command_start_preserves_final_after_max_silence():
    async def run():
        session = Session()
        detector = TurnDetector(
            vad_probability=lambda a: float(np.any(a)),
            turn_probability=lambda _: 0,
        )
        gateway = AudioGateway(lambda _: session, detector_factory=lambda: detector)

        async def audio():
            yield bytes(9600)
            yield np.ones(8000, dtype="<i2").tobytes()
            yield bytes(48000)

        replies = [r async for r in gateway.stt(TURN, audio())]
        assert detector.speech_started_at_ms is not None
        assert session.finish_calls == 1
        assert [r["text"] for r in replies if r["type"] == "final"] == [
            "provider final text"
        ]

    asyncio.run(run())


def test_accepted_short_command_limit_192ms_run_has_empty_final():
    async def run():
        session = Session()
        detector = TurnDetector(
            vad_probability=lambda a: float(np.any(a)),
            turn_probability=lambda _: 1,
        )
        gateway = AudioGateway(lambda _: session, detector_factory=lambda: detector)

        async def audio():
            yield bytes(61440)
            yield np.ones(3072, dtype="<i2").tobytes()
            yield bytes(96000)

        replies = [r async for r in gateway.stt(TURN, audio())]
        assert detector.speech_started_at_ms is None
        assert detector.samples == 80000
        assert session.finish_calls == 0
        assert [r["text"] for r in replies if r["type"] == "final"] == [""]

    asyncio.run(run())


def test_no_command_never_replaces_final_with_emitted_partial():
    async def run():
        emitted = asyncio.Event()
        session = Session()
        session.interim = "a provider partial"
        detector = TurnDetector(
            vad_probability=lambda _: 0, turn_probability=lambda _: 0
        )
        gateway = AudioGateway(lambda _: session, detector_factory=lambda: detector)

        async def audio():
            yield bytes(6400)
            await emitted.wait()

        replies = []
        async for reply in gateway.stt(TURN, audio()):
            replies.append(reply)
            if reply["type"] == "partial":
                emitted.set()
        assert emitted.is_set()
        assert [r["text"] for r in replies if r["type"] == "final"] == [""]
        assert session.finish_calls == 0
        assert session.cancelled and session.closed
        assert gateway.stt_final_replaced_total == 0

    asyncio.run(asyncio.wait_for(run(), 3))

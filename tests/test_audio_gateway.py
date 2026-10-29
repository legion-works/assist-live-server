import asyncio
import subprocess
import sys
import threading

import numpy as np
from assist_live_server.audio_gateway import AudioGateway
from assist_live_server.providers.parakeet import ParakeetSession
from assist_live_server.turn.detector import TurnDetector

TURN = "01900000-0000-7000-8000-000000000001"


def test_cloud_request_refused_before_factory_and_audio():
    def forbidden(_):
        raise AssertionError("cloud factory reached")

    async def audio():
        raise AssertionError("audio consumed before refusal")
        yield b""

    async def run():
        gateway = AudioGateway(
            forbidden, provider="deepgram", destination="cloud", local_only=True
        )
        assert [r["type"] async for r in gateway.stt(TURN, audio())] == ["error"]

    asyncio.run(run())


def test_gateway_uses_provider_audio_bound():
    code = """
import asyncio
from assist_live_server.providers import parakeet
parakeet.MAX_AUDIO_BYTES = 1024
from assist_live_server.audio_gateway import AudioGateway
from assist_live_server.turn.detector import TurnDetector
async def run():
    gateway = AudioGateway(
        lambda _: parakeet.ParakeetSession(lambda pcm: str(len(pcm))),
        detector_factory=lambda: TurnDetector(
            vad_probability=lambda _: 1, turn_probability=lambda _: 0
        ),
    )
    async def audio():
        yield bytes(1024)
        raise AssertionError("gateway read beyond provider bound")
    replies = [r async for r in gateway.stt(
        "01900000-0000-7000-8000-000000000001", audio()
    )]
    assert [r["text"] for r in replies if r["type"] == "final"] == ["1024"]
asyncio.run(run())
"""
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, result.stdout + result.stderr


def detector():
    return TurnDetector(
        vad_probability=lambda a: float(np.any(a)), turn_probability=lambda _: 1
    )


def test_oversized_local_partial_is_one_terminal_error():
    async def run():
        class Session(ParakeetSession):
            async def partial(self):
                return "\U0001f642" * 2000

        session = Session(lambda _: "final must not follow an error")
        gateway = AudioGateway(lambda _: session, detector_factory=detector)

        async def audio():
            yield np.ones(12800, dtype="<i2").tobytes()
            await asyncio.Event().wait()

        replies = [r async for r in gateway.stt(TURN, audio())]
        assert replies == [
            {"type": "error", "version": 1, "turn_id": TURN, "code": "stt_failed"}
        ]
        assert session._cancelled

    asyncio.run(asyncio.wait_for(run(), 2))


def test_selected_provider_onset_partial_endpoint_and_unchanged_final():
    async def run():
        partial_seen = asyncio.Event()
        selected = []
        stages = []

        def factory(name):
            selected.append(name)
            return ParakeetSession(lambda _: "turn on lights")

        gateway = AudioGateway(
            factory, provider="configured", detector_factory=detector
        )

        async def record(stage, data):
            stages.append(stage)

        async def audio():
            assert selected == ["configured"]
            yield np.ones(12800, dtype="<i2").tobytes()
            await asyncio.wait_for(partial_seen.wait(), 2)
            yield bytes(32000)
            raise AssertionError("input continued after endpoint")

        replies = []
        async for reply in gateway.stt(TURN, audio(), record=record):
            replies.append(reply)
            if reply["type"] == "partial":
                partial_seen.set()
        assert selected == ["configured"]
        assert partial_seen.is_set(), "no partial after VAD onset"
        assert stages[0] == "speech_start"
        assert stages.count("speech_start") == 1
        assert "end_candidate" in stages
        assert stages[-1] == "end_decision"
        assert [r["text"] for r in replies if r["type"] == "final"] == [
            "turn on lights"
        ]
        assert [r["type"] for r in replies][-3:] == [
            "end_decision",
            "final",
            "speaker_result",
        ]

    asyncio.run(run())


def test_audio_bound_finishes_once_and_does_not_read_another_frame():
    async def run():
        finished = []

        class Session(ParakeetSession):
            async def finish(self):
                finished.append(True)
                return await super().finish()

        gateway = AudioGateway(
            lambda _: Session(lambda pcm: str(len(pcm))), detector_factory=detector
        )

        async def audio():
            for _ in range(15):
                yield np.ones(16000, dtype="<i2").tobytes()
            raise AssertionError("input continued after 15 seconds")

        replies = [r async for r in gateway.stt(TURN, audio())]
        assert finished == [True]
        assert [r["text"] for r in replies if r["type"] == "final"] == ["480000"]
        assert sum(r["type"] in {"final", "error"} for r in replies) == 1

    asyncio.run(run())


def test_wall_bound_calls_finish_with_no_frames(monkeypatch):
    import assist_live_server.audio_gateway as module

    monkeypatch.setattr(module, "TURN_TIMEOUT", 0.05)

    async def run():
        finished = []

        class Session(ParakeetSession):
            async def finish(self):
                finished.append(True)
                return ""

        gateway = AudioGateway(
            lambda _: Session(lambda _: ""), detector_factory=detector
        )

        async def audio():
            await asyncio.Event().wait()
            yield b""

        replies = [r async for r in gateway.stt(TURN, audio())]
        assert finished == [True]
        assert [r["type"] for r in replies] == [
            "end_decision",
            "final",
            "speaker_result",
        ]

    asyncio.run(run())


def test_stall_flushes_without_advancing_audio_clock():
    async def run():
        onset = asyncio.Event()
        d = detector()
        gateway = AudioGateway(
            lambda _: ParakeetSession(lambda _: "first"), detector_factory=lambda: d
        )

        async def record(stage, data):
            if stage == "speech_start":
                onset.set()

        async def audio():
            yield b"\x01\x00" * 10400
            await asyncio.wait_for(onset.wait(), 1)

        replies = [r async for r in gateway.stt(TURN, audio(), record=record)]
        assert d.samples == 10400
        assert next(r["text"] for r in replies if r["type"] == "final") == "first"

    asyncio.run(run())


def test_decoding_runs_while_detector_is_busy():
    async def run():
        loop = asyncio.get_running_loop()
        detection_entered = asyncio.Event()
        decode_entered = asyncio.Event()
        release = threading.Event()
        count = 0

        def vad(samples):
            nonlocal count
            count += 1
            if count == 22:
                loop.call_soon_threadsafe(detection_entered.set)
                assert release.wait(3)
            return 1.0

        def decode(pcm):
            loop.call_soon_threadsafe(decode_entered.set)
            return "complete"

        d = TurnDetector(vad_probability=vad, turn_probability=lambda _: 0)
        gateway = AudioGateway(
            lambda _: ParakeetSession(decode), detector_factory=lambda: d
        )

        async def audio():
            yield b"\x01\x00" * 12000

        async def consume():
            return [r async for r in gateway.stt(TURN, audio())]

        task = asyncio.create_task(consume())
        try:
            await asyncio.wait_for(detection_entered.wait(), 2)
            await asyncio.wait_for(decode_entered.wait(), 2)
            assert not task.done()
        finally:
            release.set()
            replies = await task
        assert next(r["text"] for r in replies if r["type"] == "final") == "complete"

    asyncio.run(run())


def test_second_turn_onset_comes_only_from_its_own_audio():
    async def run():
        detectors = []
        onsets = []

        def new_detector():
            d = detector()
            detectors.append(d)
            return d

        gateway = AudioGateway(
            lambda _: ParakeetSession(lambda _: "result"), detector_factory=new_detector
        )

        async def first():
            yield b"\x01\x00" * 10752

        async def record_first(stage, data):
            if stage == "speech_start":
                onsets.append("first")

        _ = [r async for r in gateway.stt(TURN, first(), record=record_first)]
        assert onsets == ["first"]

        async def second():
            yield bytes(13312)
            assert onsets == ["first"]
            yield b"\x01\x00" * 4096

        async def record_second(stage, data):
            if stage == "speech_start":
                onsets.append("second")

        _ = [
            r
            async for r in gateway.stt(
                "01900000-0000-7000-8000-000000000002", second(), record=record_second
            )
        ]
        assert onsets == ["first", "second"]
        assert detectors[0] is not detectors[1]
        assert detectors[0].speech_started_at_ms == 0.0
        assert detectors[1].speech_started_at_ms == 416.0

    asyncio.run(run())


def test_concurrent_turns_keep_sessions_and_results_separate():
    async def run():
        entered = 0
        both = asyncio.Event()
        selected = []

        def factory(name):
            selected.append(name)
            return ParakeetSession(lambda pcm: str(int.from_bytes(pcm[:2], "little")))

        gateway = AudioGateway(factory, detector_factory=detector)

        async def source(value):
            nonlocal entered
            entered += 1
            if entered == 2:
                both.set()
            await asyncio.wait_for(both.wait(), 2)
            yield value.to_bytes(2, "little") * 7000

        async def consume(turn, value):
            return [r async for r in gateway.stt(turn, source(value))]

        first, second = await asyncio.gather(
            consume(TURN, 1), consume("01900000-0000-7000-8000-000000000002", 2)
        )
        assert selected == ["parakeet", "parakeet"]
        assert [r["text"] for r in first if r["type"] == "final"] == ["1"]
        assert [r["text"] for r in second if r["type"] == "final"] == ["2"]
        assert all(r["turn_id"] == TURN for r in first)
        assert all(r["turn_id"] != TURN for r in second)

    asyncio.run(run())

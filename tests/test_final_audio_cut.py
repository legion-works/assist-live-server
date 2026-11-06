import asyncio
import threading

import numpy as np
import pytest
from assist_live_server import audio_gateway
from assist_live_server.audio_gateway import AudioGateway
from assist_live_server.providers.parakeet import ParakeetSession
from assist_live_server.turn.detector import TurnDetector

TURN = "01900000-0000-7000-8000-000000000001"


@pytest.mark.parametrize("chunk", [320, 640, 1024, 2048])
@pytest.mark.parametrize("end", ["detector", "client", "no_command"])
def test_final_decode_uses_exact_turn_prefix(chunk, end):
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
        return [reply async for reply in gateway.stt(TURN, audio())]

    replies = asyncio.run(asyncio.wait_for(run(), 3))
    finals = [r["text"] for r in replies if r["type"] == "final"]
    if end == "no_command":
        assert final_audio == [], "no command must not run a final decode"
        assert finals == [""]
    else:
        assert detectors[0].samples == expected_samples
        assert final_audio == [pcm[: expected_samples * 2]], (
            "final decode must receive the exact turn prefix, "
            "without a lost or extra sample"
        )
        assert finals == [f"turn samples {expected_samples}"]


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


@pytest.mark.parametrize("end", ["wall_timeout", "audio_cap"])
def test_hard_end_flushes_received_tail(monkeypatch, gateway_deadline, end):
    pcm = np.ones(8192, dtype="<i2").tobytes()
    tail = np.arange(900, dtype="<i2").tobytes()
    expected = pcm + tail
    final_audio = []
    notes = []
    deadlines, _ = gateway_deadline
    if end == "audio_cap":
        monkeypatch.setattr(audio_gateway, "MAX_AUDIO_BYTES", len(expected))

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

        async def record(stage, data):
            notes.append((stage, data))

        gateway = AudioGateway(
            lambda _: ParakeetSession(lambda _: "", final),
            detector_factory=lambda: TurnDetector(
                vad_probability=lambda a: float(np.any(a)),
                turn_probability=lambda _: 0,
            ),
        )
        return [r async for r in gateway.stt(TURN, audio(), record=record)]

    replies = asyncio.run(asyncio.wait_for(run(), 3))
    assert final_audio == [expected], "hard end lost received detector audio"
    assert [r["text"] for r in replies if r["type"] == "final"] == ["turn samples 9092"]
    assert [d["reason"] for s, d in notes if s == "end_decision"] == ["hard"]


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

    asyncio.run(asyncio.wait_for(run(), 5))


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

    asyncio.run(asyncio.wait_for(run(), 5))

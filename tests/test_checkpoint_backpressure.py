import asyncio
import threading

import numpy as np
import pytest
from assist_live_server import audio_gateway
from assist_live_server.audio_gateway import AudioGateway
from assist_live_server.providers.parakeet import ParakeetSession
from assist_live_server.turn.detector import TurnDetector

TURN = "01900000-0000-7000-8000-000000000001"
PCM = np.arange(30003, dtype="<i2").tobytes()


@pytest.mark.parametrize("bound", ["checkpoints", "audio"])
def test_backpressure_pauses_reader_without_loss_or_skipped_checkpoints(bound):
    async def run():
        ready = asyncio.Event()
        release = asyncio.Event()
        thread_release = threading.Event()
        checkpoints = []
        frames = []
        final_audio = []
        loop = asyncio.get_running_loop()
        first = True

        class Session(ParakeetSession):
            async def checkpoint(self, prefix):
                checkpoints.append(len(prefix) // 2)
                if bound == "checkpoints":
                    if len(checkpoints) == 2:
                        ready.set()
                    await release.wait()
                return "complete checkpoint result"

        def vad(_):
            nonlocal first
            if bound == "audio" and first:
                first = False
                assert thread_release.wait(3)
            return 1

        def final(prefix):
            final_audio.append(prefix)
            return "complete checkpoint result"

        detector = TurnDetector(vad_probability=vad, turn_probability=lambda _: 0)
        gateway = AudioGateway(
            lambda _: Session(lambda _: "", final),
            detector_factory=lambda: detector,
            end_rule="checkpoint",
        )

        async def audio():
            for offset in range(0, len(PCM), 1024):
                frames.append(offset)
                if bound == "audio" and len(frames) == 18:
                    loop.call_soon(ready.set)
                yield PCM[offset : offset + 1024]

        async def consume():
            return [r async for r in gateway.stt(TURN, audio())]

        task = asyncio.create_task(consume())
        try:
            await asyncio.wait_for(ready.wait(), 1)
            paused = len(frames)
            await asyncio.sleep(0.03)
            assert len(frames) == paused
            assert paused == (25 if bound == "checkpoints" else 18)
            assert checkpoints == ([6400, 12800] if bound == "checkpoints" else [6400])
        finally:
            release.set()
            thread_release.set()
            replies = await asyncio.wait_for(task, 2)
        assert final_audio == [PCM]
        assert detector.samples == len(PCM) // 2
        assert checkpoints == [6400, 12800, 19200, 25600]
        assert (
            next(r["text"] for r in replies if r["type"] == "final")
            == "complete checkpoint result"
        )
        assert gateway.checkpoint_deadline_failures_total == 0
        assert gateway.checkpoint_wall_limit_failures_total == 0

    asyncio.run(asyncio.wait_for(run(), 4))


@pytest.mark.parametrize("cause", ["deadline", "wall_limit"])
def test_checkpoint_backpressure_expires_without_alternate_end(monkeypatch, cause):
    if cause == "wall_limit":
        monkeypatch.setattr(audio_gateway, "TURN_TIMEOUT", 0.08)

    async def run():
        checkpoints = []
        frames = []

        class Session(ParakeetSession):
            async def checkpoint(self, prefix):
                checkpoints.append(len(prefix) // 2)
                await asyncio.Event().wait()

        gateway = AudioGateway(
            lambda _: Session(lambda _: ""),
            detector_factory=lambda: TurnDetector(
                vad_probability=lambda _: 1, turn_probability=lambda _: 1
            ),
            end_rule="checkpoint",
        )

        async def audio():
            for offset in range(0, len(PCM), 1024):
                frames.append(offset)
                yield PCM[offset : offset + 1024]

        replies = [r async for r in gateway.stt(TURN, audio())]
        assert checkpoints == [6400, 12800]
        assert len(frames) == 25
        assert [r["type"] for r in replies] == ["error"]
        assert replies[0]["code"] == "stt_failed"
        assert gateway.checkpoint_deadline_failures_total == (cause == "deadline")
        assert gateway.checkpoint_wall_limit_failures_total == (cause == "wall_limit")

    asyncio.run(asyncio.wait_for(run(), 2))

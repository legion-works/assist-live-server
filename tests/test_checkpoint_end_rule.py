import asyncio
import time

import numpy as np
import pytest
from assist_live_server import audio_gateway
from assist_live_server.audio_gateway import AudioGateway
from assist_live_server.providers.parakeet import ParakeetSession
from assist_live_server.turn.detector import TurnDetector

TURN = "01900000-0000-7000-8000-000000000001"
PCM = np.ones(8192, dtype="<i2").tobytes() + bytes(60000)


async def replay(
    pcm=PCM, chunk=1024, delay=0, *, final="exact final checkpoint", stall=False
):
    checkpoints = []
    detectors = []
    final_audio = []
    windows = []

    class Session(ParakeetSession):
        async def checkpoint(self, prefix):
            checkpoints.append(len(prefix) // 2)
            await asyncio.sleep(delay if len(prefix) == 25600 else 0)
            return "turn on the" if len(prefix) < 25600 else "checkpoint chosen words"

    def detector():
        def vad(samples):
            windows.append(samples.copy())
            return float(np.any(samples))

        instance = TurnDetector(vad_probability=vad, turn_probability=lambda _: 1)
        detectors.append(instance)
        return instance

    def finish(prefix):
        final_audio.append(prefix)
        return final

    gateway = AudioGateway(
        lambda _: Session(lambda _: "unrelated partial words", finish),
        detector_factory=detector,
        end_rule="checkpoint",
    )

    async def audio():
        for offset in range(0, len(pcm), chunk):
            if stall and offset == chunk:
                await asyncio.sleep(0.15)
            yield pcm[offset : offset + chunk]

    replies = [reply async for reply in gateway.stt(TURN, audio())]
    return gateway, detectors[0], replies, checkpoints, final_audio, windows


@pytest.mark.parametrize("chunk", [320, 640, 1024, 2048])
@pytest.mark.parametrize("delay", [0, 0.15])
def test_checkpoint_layout_and_decode_delay_do_not_move_end(chunk, delay):
    gateway, detector, replies, checkpoints, final_audio, _ = asyncio.run(
        asyncio.wait_for(replay(chunk=chunk, delay=delay), 3)
    )
    assert detector.samples == 14848
    assert final_audio == [PCM[:29696]]
    assert [r["text"] for r in replies if r["type"] == "final"] == [
        "exact final checkpoint"
    ]
    assert checkpoints[:2] == [6400, 12800]
    assert gateway.checkpoint_deadline_failures_total == 0


def test_checkpoint_replacement_uses_end_checkpoint_not_sent_partial():
    gateway, detector, replies, _, _, _ = asyncio.run(replay(delay=0.15, final="Yeah."))
    assert detector.samples == 14848
    assert next(r["text"] for r in replies if r["type"] == "final") == (
        "checkpoint chosen words"
    )
    assert gateway.stt_final_replaced_total == 1


def test_checkpoint_stall_does_not_split_vad_window():
    normal = asyncio.run(replay(chunk=320))
    stalled = asyncio.run(replay(chunk=320, stall=True))
    assert normal[1].samples == stalled[1].samples == 14848
    assert len(normal[-1]) == len(stalled[-1])
    for before, after in zip(normal[-1], stalled[-1], strict=True):
        np.testing.assert_array_equal(before, after)


def test_checkpoint_never_returns_fails_within_one_second():
    async def run():
        requested = asyncio.Event()
        times = []

        class Session(ParakeetSession):
            async def checkpoint(self, prefix):
                times.append(time.monotonic())
                requested.set()
                await asyncio.Event().wait()

        gateway = AudioGateway(
            lambda _: Session(lambda _: ""),
            detector_factory=lambda: TurnDetector(
                vad_probability=lambda a: float(np.any(a)),
                turn_probability=lambda _: 1,
            ),
            end_rule="checkpoint",
        )

        async def audio():
            yield PCM
            await requested.wait()
            await asyncio.Event().wait()

        replies = [r async for r in gateway.stt(TURN, audio())]
        assert time.monotonic() - times[0] <= 1.15
        assert [r["type"] for r in replies] == ["error"]
        assert replies[0]["code"] == "stt_failed"
        assert gateway.checkpoint_deadline_failures_total == 1
        assert gateway.checkpoint_wall_limit_failures_total == 0

    asyncio.run(asyncio.wait_for(run(), 2))


def test_checkpoint_wall_limit_is_failure_not_different_endpoint(monkeypatch):
    monkeypatch.setattr(audio_gateway, "TURN_TIMEOUT", 0.08)

    async def run():
        gateway = AudioGateway(
            lambda _: ParakeetSession(lambda _: "complete checkpoint"),
            detector_factory=lambda: TurnDetector(
                vad_probability=lambda _: 1, turn_probability=lambda _: 1
            ),
            end_rule="checkpoint",
        )

        async def audio():
            yield PCM[:16384]
            await asyncio.Event().wait()

        replies = [r async for r in gateway.stt(TURN, audio())]
        assert [r["type"] for r in replies if r["type"] != "partial"] == ["error"]
        assert replies[-1]["code"] == "stt_failed"
        assert gateway.checkpoint_wall_limit_failures_total == 1
        assert gateway.checkpoint_deadline_failures_total == 0

    asyncio.run(asyncio.wait_for(run(), 2))


@pytest.mark.parametrize(
    "pcm", [bytes(18006), PCM[:18006]], ids=["no_command", "command"]
)
def test_checkpoint_eof_exact_cut_and_no_command(pcm):
    _, detector, replies, checkpoints, final_audio, _ = asyncio.run(replay(pcm=pcm))
    onset = bool(any(pcm))
    assert detector.samples == len(pcm) // 2
    assert final_audio == ([pcm] if onset else [])
    assert checkpoints == [6400]
    assert [r["text"] for r in replies if r["type"] == "final"] == (
        ["exact final checkpoint"] if onset else [""]
    )


def test_checkpoint_provider_decodes_exact_prefix_without_onset():
    seen = []

    async def run():
        session = ParakeetSession(lambda pcm: seen.append(pcm) or "checkpoint text")
        assert await session.checkpoint(PCM[:12800]) == "checkpoint text"
        await session.push(PCM[:18006])
        assert await session.finish() == "checkpoint text"

    asyncio.run(run())
    assert seen == [PCM[:12800], PCM[:18006]]


@pytest.mark.parametrize("chunk", [320, 640, 1024, 2048])
def test_sent_partial_publication_cannot_reset_checkpoint_cadence(chunk):
    async def run():
        loop = asyncio.get_running_loop()
        release = asyncio.Event()
        tests = []

        class Session(ParakeetSession):
            async def checkpoint(self, prefix):
                if len(prefix) >= 38400:
                    await release.wait()
                    return "newer complete checkpoint"
                return "complete checkpoint"

        detector = None

        def score(_):
            tests.append(detector.samples)
            loop.call_soon_threadsafe(release.set)
            return float(detector.samples == 15360)

        detector = TurnDetector(
            vad_probability=lambda a: float(np.any(a)), turn_probability=score
        )
        gateway = AudioGateway(
            lambda _: Session(lambda _: "", lambda _: "complete checkpoint"),
            detector_factory=lambda: detector,
            end_rule="checkpoint",
        )

        async def audio():
            for offset in range(0, len(PCM), chunk):
                yield PCM[offset : offset + chunk]

        replies = [r async for r in gateway.stt(TURN, audio())]
        assert detector.samples == 32256
        assert tests[0] == 14848
        assert 15360 not in tests
        assert replies[-2]["type"] == "final"

    asyncio.run(asyncio.wait_for(run(), 3))


@pytest.mark.parametrize("end_rule", ["partial", "checkpoint"])
def test_end_decision_wait_histogram_observes_end_sample(end_rule):
    async def run():
        gateway = AudioGateway(
            lambda _: ParakeetSession(lambda _: "complete checkpoint"),
            detector_factory=lambda: TurnDetector(
                vad_probability=lambda a: float(np.any(a)),
                turn_probability=lambda _: 0,
            ),
            end_rule=end_rule,
        )

        async def audio():
            yield PCM[:18006]

        replies = [r async for r in gateway.stt(TURN, audio())]
        assert any(r["type"] == "final" for r in replies)
        assert gateway.end_decision_wait.count == 1
        assert gateway.end_decision_wait.total >= 0
        rendered = gateway.end_decision_wait.render(end_rule)
        assert f'{{end_rule="{end_rule}",le="+Inf"}} 1' in rendered

    asyncio.run(run())

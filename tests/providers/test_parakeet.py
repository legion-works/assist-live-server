import asyncio
import hashlib
import json
import struct
import threading
from pathlib import Path

import pytest
from assist_live_server.providers.parakeet import ParakeetProvider, ParakeetSession
from assist_live_server.providers.stt import (
    AudioLimitError,
    STTBusyError,
    STTClosedError,
    run_decode,
)


def test_chunked_input_keeps_first_word(local_stt, first_word_clip):
    async def run():
        for chunk in first_word_clip:
            await local_stt.push(chunk)
        assert (await local_stt.finish()).split()[0].lower() == "turn"
        assert await local_stt.finish() == "turn on lights"

    asyncio.run(run())


def test_input_order_and_complete_final_buffer(local_stt, first_word_clip):
    async def run():
        local_stt.speech_started()
        for chunk, text in zip(
            first_word_clip, ("turn", "turn on", "turn on lights"), strict=True
        ):
            await local_stt.push(chunk)
            assert await local_stt.partial() == text
        assert await local_stt.finish() == "turn on lights"
        with pytest.raises(STTClosedError):
            await local_stt.push(b"\0\0")

    asyncio.run(run())


def test_onset_gate_keeps_silence_empty_and_preserves_prefix():
    seen = []

    def decode(pcm):
        seen.append(pcm)
        return "Okay." if pcm == bytes(12800) else "turn on lights"

    async def run():
        session = ParakeetSession(decode)
        await session.push(bytes(12800))
        assert await session.partial() == ""
        assert seen == []
        session.speech_started()
        await session.push(struct.pack("<h", 100) * 6400)
        assert await session.partial() == "turn on lights"
        assert await session.finish() == "turn on lights"
        assert seen[-1] == bytes(12800) + struct.pack("<h", 100) * 6400

    asyncio.run(run())


def test_partial_interval_and_finish_decodes_short_tail():
    seen = []

    def decode(pcm):
        seen.append(pcm)
        return str(len(pcm))

    async def run():
        session = ParakeetSession(decode)
        session.speech_started()
        await session.push(bytes(12798))
        assert await session.partial() == ""
        await session.push(bytes(2))
        assert await session.partial() == "12800"
        assert await session.partial() == "12800"
        await session.push(bytes(2))
        assert await session.partial() == "12800"
        assert await session.finish() == "12802"
        assert [len(pcm) for pcm in seen] == [12800, 12802]

    asyncio.run(run())


def test_buffer_bound_rejects_without_dropping_start():
    async def run():
        session = ParakeetSession(lambda pcm: str(len(pcm)))
        await session.push(bytes(480000))
        with pytest.raises(AudioLimitError, match="15 s"):
            await session.push(bytes(2))
        assert await session.finish() == "480000"

    asyncio.run(run())


def test_empty_finish_needs_no_model_and_pcm_alignment():
    async def run():
        session = ParakeetSession(lambda pcm: pytest.fail("empty decode"))
        with pytest.raises(ValueError, match="int16"):
            await session.push(bytes(1))
        assert await session.finish() == ""

    asyncio.run(run())


def test_cancel_before_decode(local_stt):
    async def run():
        local_stt.cancel()
        for operation in (local_stt.partial, local_stt.finish):
            with pytest.raises(STTClosedError, match="cancel"):
                await operation()
        with pytest.raises(STTClosedError):
            await local_stt.push(bytes(2))

    asyncio.run(run())


def test_running_decode_cancel_keeps_event_loop_live():
    entered, release = threading.Event(), threading.Event()

    def decode(pcm):
        entered.set()
        assert release.wait(5)
        return "late result"

    async def run():
        session = ParakeetSession(decode)
        session.speech_started()
        await session.push(bytes(12800))
        task = asyncio.create_task(session.partial())
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            session.cancel()
            release.set()
            with pytest.raises(STTClosedError, match="cancel"):
                await asyncio.wait_for(task, 5)
        finally:
            release.set()

    asyncio.run(run())


def test_executor_admission_is_bounded_and_cancel_does_not_free_running_slot():
    entered, release = threading.Event(), threading.Event()

    def decode():
        entered.set()
        assert release.wait(5)
        return "done"

    async def run():
        tasks = [asyncio.create_task(run_decode(decode))]
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            tasks.extend(
                asyncio.create_task(run_decode(lambda: "queued")) for _ in range(3)
            )
            await asyncio.sleep(0)
            with pytest.raises(STTBusyError):
                await run_decode(lambda: "overflow")
            tasks[0].cancel()
            with pytest.raises(asyncio.CancelledError):
                await tasks[0]
            with pytest.raises(STTBusyError):
                await run_decode(lambda: "overflow")
        finally:
            release.set()
            await asyncio.gather(*tasks, return_exceptions=True)
        assert await run_decode(lambda: "recovered") == "recovered"

    asyncio.run(run())


def test_provider_verifies_every_file_before_runtime_load(tmp_path, monkeypatch):
    from assist_live_server.providers import parakeet

    root = Path(__file__).resolve().parents[2]
    manifest = json.loads((root / "assets/manifest.json").read_text())
    files = manifest["stt_benchmark"]["parakeet"]["files"]
    for row in files:
        data = row["file"].encode()
        (tmp_path / row["file"]).write_bytes(data)
        row["sha256"] = hashlib.sha256(data).hexdigest()
        entry = next(
            e for e in manifest["download_plan"] if Path(e["path"]).name == row["file"]
        )
        entry["sha256"] = row["sha256"]
    manifest_path = tmp_path / "pins.json"
    manifest_path.write_text(json.dumps(manifest))
    loaded = []

    class Model:
        def recognize(self, samples, sample_rate):
            assert sample_rate == 16000
            return "turn" if samples[0] > 0 else "silence"

    def load(*args, **kwargs):
        loaded.append((args, kwargs))
        return Model()

    monkeypatch.setattr(parakeet, "_load_onnx", load)

    async def run():
        provider = await ParakeetProvider.load(tmp_path, manifest_path)
        session = provider.session()
        await session.push(struct.pack("<h", 100) * 6400)
        assert await session.finish() == "turn"
        (tmp_path / files[-1]["file"]).write_bytes(b"corrupt")
        with pytest.raises(ValueError, match="SHA-256"):
            await ParakeetProvider.load(tmp_path, manifest_path)
        assert len(loaded) == 1
        assert loaded[0][1]["quantization"] == "int8"
        assert loaded[0][1]["preprocessor_config"] == {
            "use_numpy_preprocessors": True,
            "max_concurrent_workers": 1,
        }

    asyncio.run(run())


def test_slow_partial_coalesces_ticks_and_uses_newest_audio():
    first_entered, first_release = threading.Event(), threading.Event()
    second_entered, second_release = threading.Event(), threading.Event()
    seen = []

    def decode(pcm):
        seen.append(pcm)
        if len(seen) == 1:
            first_entered.set()
            assert first_release.wait(5)
        elif len(seen) == 2:
            second_entered.set()
            assert second_release.wait(5)
        else:
            pytest.fail("more than one pending decode")
        return str(len(pcm))

    async def run():
        session = ParakeetSession(decode)
        session.speech_started()
        expected = struct.pack("<h", 1) * 6400
        await session.push(expected)
        tasks = [asyncio.create_task(session.partial())]
        try:
            assert await asyncio.to_thread(first_entered.wait, 5)
            for value in range(2, 6):
                chunk = struct.pack("<h", value) * 6400
                expected += chunk
                await asyncio.wait_for(session.push(chunk), 1)
                tasks.extend(asyncio.create_task(session.partial()) for _ in range(3))
                await asyncio.sleep(0)
            first_release.set()
            assert await asyncio.to_thread(second_entered.wait, 5)
            assert len(seen) == 2
            second_release.set()
            assert await asyncio.gather(*tasks) == ["64000"] * 13
            assert seen == [struct.pack("<h", 1) * 6400, expected]
            assert await session.partial() == "64000"
        finally:
            first_release.set()
            second_release.set()
            await asyncio.gather(*tasks, return_exceptions=True)

    asyncio.run(run())


def test_finish_runs_before_queued_partials_from_other_sessions():
    entered, release = threading.Event(), threading.Event()
    order = []

    def decode(pcm):
        name = str(struct.unpack("<h", pcm[:2])[0])
        order.append(name)
        if name == "1":
            entered.set()
            assert release.wait(5)
        return name

    async def run():
        sessions = [ParakeetSession(decode) for _ in range(4)]
        for value, session in enumerate(sessions, 1):
            session.speech_started()
            await session.push(struct.pack("<h", value) * 6400)
        tasks = [asyncio.create_task(sessions[0].partial())]
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            tasks.extend(asyncio.create_task(s.partial()) for s in sessions[1:3])
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            tasks.append(asyncio.create_task(sessions[3].finish()))
            await asyncio.sleep(0)
            release.set()
            assert await asyncio.gather(*tasks) == ["1", "2", "3", "4"]
            assert order == ["1", "4", "2", "3"]
        finally:
            release.set()
            await asyncio.gather(*tasks, return_exceptions=True)

    asyncio.run(run())


def test_completion_releases_capacity_before_result_reaches_caller(monkeypatch):
    from assist_live_server.providers import stt

    entered, release, work_release = (
        threading.Event(),
        threading.Event(),
        threading.Event(),
    )
    original = stt._completed

    def completed(future):
        entered.set()
        assert release.wait(5)
        original(future)

    monkeypatch.setattr(stt, "_completed", completed)

    def work():
        assert work_release.wait(5)
        return "done"

    async def run():
        first = asyncio.create_task(run_decode(work))
        tasks = []
        try:
            await asyncio.sleep(0)
            work_release.set()
            assert await asyncio.to_thread(entered.wait, 5)
            assert await first == "done"
            tasks = [asyncio.create_task(run_decode(lambda: "next")) for _ in range(4)]
            await asyncio.sleep(0)
            assert not any(
                task.done() and isinstance(task.exception(), STTBusyError)
                for task in tasks
            ), "completed work still consumes an admission slot"
        finally:
            work_release.set()
            release.set()
            await asyncio.gather(first, *tasks, return_exceptions=True)

    asyncio.run(run())

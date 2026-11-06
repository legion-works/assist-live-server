import asyncio
import os
import subprocess
import sys
import threading

import pytest


@pytest.mark.parametrize("end_rule", ["partial", "checkpoint"])
@pytest.mark.parametrize("scenario", ["cancelled", "executor_closed"])
def test_app_cleanup_with_cancelled_decode_does_not_submit_at_interpreter_exit(
    tmp_path, end_rule, scenario
):
    source = (
        r"""
import asyncio
import threading
from pathlib import Path
from aiohttp import ClientSession, UnixConnector, web
from assist_live_client.protocol import encode_audio
from assist_live_server.audio_gateway import AudioGateway
from assist_live_server.providers.parakeet import ParakeetSession
from assist_live_server.providers import stt
from assist_live_server.turn.detector import TurnDetector
from tests.contract.test_streaming_stt import service, connect

MODE = __MODE__
SCENARIO = __SCENARIO__
release = threading.Event()
entered = threading.Event()

def decode(pcm):
    entered.set()
    assert release.wait(5)
    return "complete checkpoint"

async def main():
    requested = asyncio.Event()
    calls = 0
    closed = False
    submit = stt._EXECUTOR.submit

    def guarded(*args, **kwargs):
        if closed:
            raise RuntimeError("synthetic executor shutdown")
        return submit(*args, **kwargs)

    stt._EXECUTOR.submit = guarded
    blocker = None
    if SCENARIO == "executor_closed":
        blocker = asyncio.create_task(stt.run_decode(lambda: decode(b"")))
        assert await asyncio.to_thread(entered.wait, 2)

    class Session(ParakeetSession):
        async def checkpoint(self, prefix):
            nonlocal calls
            calls += 1
            if calls == 2:
                requested.set()
            return await super().checkpoint(prefix)

    gateway = AudioGateway(
        lambda _: Session(decode),
        detector_factory=lambda: TurnDetector(
            vad_probability=lambda _: 1, turn_probability=lambda _: 0
        ),
        end_rule=MODE,
    )
    try:
        async with service(Path(__DIRECTORY__), gateway=gateway) as bundle:
            _, session, turn, _ = bundle
            ws = await connect(session, turn)
            await ws.send_bytes(encode_audio(turn.turn_id, 0, b"\x01\x00"*16000))
            if SCENARIO == "executor_closed":
                async with asyncio.timeout(2):
                    while not stt._QUEUE:
                        await asyncio.sleep(0.001)
                closed = True
                release.set()
                async with asyncio.timeout(2):
                    replies = [message.json() async for message in ws]
                terminal = [r["type"] for r in replies if r["type"] != "heartbeat"]
                assert terminal == ["error", "end"]
                code = next(r["code"] for r in replies if r["type"] == "error")
                assert code == "stt_failed"
                assert not stt._QUEUE
                acquired = 0
                try:
                    for _ in range(4):
                        assert stt._SLOTS.acquire(blocking=False)
                        acquired += 1
                finally:
                    for _ in range(acquired):
                        stt._SLOTS.release()
            else:
                assert await asyncio.to_thread(entered.wait, 2)
                if MODE == "checkpoint":
                    await asyncio.wait_for(requested.wait(), 2)
                await ws.close()
    finally:
        timer = threading.Timer(0.2, release.set)
        timer.daemon = True
        timer.start()
    if blocker is not None:
        await blocker
    print("PASS normal app lifecycle", MODE, SCENARIO, flush=True)

asyncio.run(main())
""".replace("__MODE__", repr(end_rule))
        .replace("__SCENARIO__", repr(scenario))
        .replace("__DIRECTORY__", repr(str(tmp_path)))
    )
    result = subprocess.run(
        [sys.executable, "-c", source],
        capture_output=True,
        text=True,
        timeout=10,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS normal app lifecycle " + end_rule in result.stdout
    assert result.stderr == "", result.stderr


def test_cancelled_queued_job_keeps_slot_and_needs_no_executor_submission(monkeypatch):
    from assist_live_server.providers import stt

    entered = threading.Event()
    release = threading.Event()
    submitted = []
    executed = []
    submit = stt._EXECUTOR.submit

    def tracked(*args, **kwargs):
        submitted.append(args)
        return submit(*args, **kwargs)

    monkeypatch.setattr(stt._EXECUTOR, "submit", tracked)

    def held():
        entered.set()
        assert release.wait(3)

    async def run():
        tasks = [asyncio.create_task(stt.run_decode(held))]
        try:
            assert await asyncio.to_thread(entered.wait, 1)
            tasks.extend(
                [
                    asyncio.create_task(
                        stt.run_decode(lambda: executed.append("cancelled"))
                    ),
                    asyncio.create_task(
                        stt.run_decode(lambda: executed.append("partial"))
                    ),
                    asyncio.create_task(
                        stt.run_decode(lambda: executed.append("final"), final=True)
                    ),
                ]
            )
            await asyncio.sleep(0)
            tasks[1].cancel()
            await asyncio.gather(tasks[1], return_exceptions=True)
            with pytest.raises(stt.STTBusyError):
                await stt.run_decode(lambda: None)
        finally:
            release.set()
            await asyncio.gather(*tasks, return_exceptions=True)
        assert executed == ["final", "partial"]
        assert len(submitted) == 3

    asyncio.run(run())

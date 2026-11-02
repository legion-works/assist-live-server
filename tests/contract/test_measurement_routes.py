import asyncio
import json
import os
import subprocess
import sys
import tarfile
from contextlib import asynccontextmanager
from dataclasses import asdict
from io import BytesIO
from pathlib import Path

import pytest
from aiohttp import ClientSession, UnixConnector, web
from assist_live_client import AssistLiveClient, TimingEvent, TTSFormat, TurnOpen
from assist_live_client.protocol import ProtocolError, encode_control
from assist_live_server.app import create_app
from assist_live_server.auth import Registry
from assist_live_server.store import SQLiteStore

TURN = "01900000-0000-7000-8000-000000000001"


@asynccontextmanager
async def service(tmp_path, *, sentences=1, transcripts=False, conversation_id=None):
    store = SQLiteStore(tmp_path / "timing.db", record_transcripts=transcripts)
    registry = Registry()
    registry.bind("satellite-a", "satellite-a", b"synthetic-device-key")
    registry.bind("satellite-b", "satellite-b", b"synthetic-other-key")
    registry.add_token("synthetic-token", ["satellite-a"])

    class Backend:
        audio_format = TTSFormat("pcm_s16le", 16000, 1, 2)

        async def tts(self, turn_id, text, voice):
            [part async for part in text]
            for index in range(sentences):
                yield bytes(320)
                yield encode_control("sentence_end", turn_id, sentence=index)

    runner = web.AppRunner(
        create_app(registry, store, Backend(), record_transcripts=transcripts),
        access_log=None,
    )
    await runner.setup()
    socket = tmp_path / "service.sock"
    await web.UnixSite(runner, socket).start()
    try:
        async with ClientSession(connector=UnixConnector(path=socket)) as session:
            client = AssistLiveClient(
                "http://service.invalid", "synthetic-token", session=session
            )
            await client.open_turn(
                TurnOpen(
                    TURN, "satellite-a", "tts_only", None, conversation_id, transcripts
                )
            )
            yield client, store, session, socket
    finally:
        await runner.cleanup()
        store.close()


def event(stage, source="server", data=None, stamp=1):
    return TimingEvent(
        TURN, "satellite-a", source, stage, stamp, "measured", None, data=data or {}
    )


def test_http_wrong_owner_is_named_counted_and_client_refused(tmp_path):
    async def run():
        async with service(tmp_path) as (client, store, session, _):
            bad = event("stt_final", "integration")
            async with session.post(
                client.url + "/v1/event", headers=client._headers, json=asdict(bad)
            ) as response:
                assert response.status == 403, "wrong_owner_not_refused"
                assert await response.json() == {"error": "wrong_owner"}
            with pytest.raises(ProtocolError) as error:
                await client.event(bad)
            assert error.value.kind == "refused"
            await client.event(event("tts_first_text", "integration"))
            async with session.get(
                client.url + "/metrics", headers=client._headers
            ) as response:
                assert "assist_live_wrong_owner_total 2\n" in await response.text()
            assert len((await client.turn(TURN))["events"]) == 1

    asyncio.run(run())


def test_tts_request_numbers_across_two_streams(tmp_path):
    async def run():
        async with service(tmp_path) as (client, _, _, _):

            async def text():
                yield "Test. "

            for _ in range(2):
                [r async for r in client.tts_stream(TURN, text(), "voice")]
            rows = (await client.turn(TURN))["events"]
            last = [r for r in rows if r["stage"] == "tts_last_byte"]
            assert [r["data"].get("request") for r in last] == [0, 1], (
                "request_numbering_wrong"
            )
            assert all("request" in r["data"] for r in rows), "tts_request_dropped"

    asyncio.run(run())


def test_sentence_cap_is_per_turn_across_streams(tmp_path):
    async def run():
        async with service(tmp_path, sentences=130) as (client, store, _, _):

            async def text():
                yield "Test. " * 130

            for _ in range(2):
                [r async for r in client.tts_stream(TURN, text(), "voice")]
            rows = (await store.get_turn(TURN))["events"]
            assert sum(r["stage"] == "tts_sentence" for r in rows) == 256, (
                "turn_sentence_cap_broken"
            )
            last = [r["data"] for r in rows if r["stage"] == "tts_last_byte"]
            assert [r["sentences"] for r in last] == [130, 130]
            assert [r["audio_ms"] for r in last] == [1300, 1300]

    asyncio.run(run())


def test_turn_event_limit_includes_all_stages_and_streams(tmp_path):
    async def run():
        async with service(tmp_path) as (client, store, session, _):
            for index in range(1020):
                stage = "tts_first_text" if index % 2 else "tts_audio_received"
                await store.append(event(stage, "integration"))

            async def text():
                yield "Test. "

            [r async for r in client.tts_stream(TURN, text(), "voice")]
            assert len((await store.get_turn(TURN))["events"]) == 1024, (
                "event_1024_not_stored"
            )
            async with session.post(
                client.url + "/v1/event",
                headers=client._headers,
                json=asdict(event("satellite_state", "integration")),
            ) as response:
                assert response.status == 403, "event_1025_not_refused"
                assert await response.json() == {"error": "turn_event_limit"}
            with pytest.raises(ProtocolError) as error:
                await client.event(event("tts_first_text", "integration"))
            assert error.value.kind == "refused"
            assert (await store.diagnostics())["turn_event_limit_total"] == 2
            assert len((await client.turn(TURN))["events"]) == 1024, (
                "event_1024_not_readable"
            )

    asyncio.run(run())


def test_turn_byte_limit_and_maximum_size_read(tmp_path):
    async def run():
        async with service(
            tmp_path, transcripts=True, conversation_id="\U00010000" * 256
        ) as (client, store, session, _):
            await client.event(event("fallback_used", "integration", {"part": "stt"}))

            def sized(size):
                row = event("stt_final", "integration", {"transcript": ""})
                overhead = len(json.dumps(asdict(row), separators=(",", ":")).encode())
                row.data["transcript"] = "x" * (size - overhead)
                assert (
                    len(json.dumps(asdict(row), separators=(",", ":")).encode()) == size
                )
                return row

            for _ in range(64):
                await client.event(sized(16_256))
            refused = False
            try:
                await client.event(sized(16_256))
            except ProtocolError as error:
                assert error.kind == "refused"
                refused = True
            assert refused, "oversize_turn_not_refused"
            rows = (await store.get_turn(TURN))["events"]
            used = sum(
                len(json.dumps(row, separators=(",", ":")).encode()) for row in rows
            )
            remaining = 1_048_576 - used
            assert 0 < remaining < 16_256
            await client.event(sized(remaining))
            async with session.post(
                client.url + "/v1/event",
                headers=client._headers,
                json=asdict(event("stt_final", "integration")),
            ) as response:
                assert response.status == 403, "turn_byte_limit_not_refused"
                assert await response.json() == {"error": "turn_size_limit"}
            record = await client.turn(TURN)
            assert len(record["events"]) == 66, "maximum_turn_not_readable"
            assert (
                sum(
                    len(json.dumps(row, separators=(",", ":")).encode())
                    for row in record["events"]
                )
                == 1_048_576
            )
            assert record["conversation_id"] == "\U00010000" * 256
            assert (await store.diagnostics())["turn_size_limit_total"] == 2

    asyncio.run(run())


def test_subscription_opt_in_receives_new_stages(tmp_path):
    async def run():
        async with service(tmp_path) as (client, store, session, _):
            async with session.ws_connect(
                client.url + "/v1/events", headers=client._headers
            ) as ws:
                await ws.send_json(
                    {
                        "type": "subscribe",
                        "version": 1,
                        "satellite": "satellite-a",
                        "after_event_id": 0,
                        "stages": ["tts_text_received"],
                    }
                )
                await store.append(event("tts_text_received"))
                value = await ws.receive_json(timeout=2)
                assert value["event"]["stage"] == "tts_text_received", (
                    "opt_in_stage_missing"
                )
            stream = client.events("satellite-a")
            try:
                row = await asyncio.wait_for(anext(stream), 2)
                assert row["event"]["stage"] == "tts_text_received", (
                    "client_did_not_opt_in"
                )
            finally:
                await stream.aclose()

    asyncio.run(run())


def test_health_loss_snapshot_is_token_scoped_and_has_counter_epoch(tmp_path):
    import time

    async def run():
        started = time.time_ns()
        async with service(tmp_path) as (client, store, session, _):
            async with session.get(
                client.url + "/health", headers=client._headers
            ) as response:
                health = await response.json()
            assert health.get("sequence_gaps_by_satellite") == {
                "satellite-a": {"boot_id": None, "missing": 0}
            }, "satellite_loss_map_missing"
            assert (
                started <= health.get("counters_since_utc_ns", 0) <= time.time_ns()
            ), "counter_epoch_missing"
            async with session.get(
                client.url + "/metrics", headers=client._headers
            ) as response:
                metrics = await response.text()
            assert (
                f"assist_live_counters_since_utc_ns {health['counters_since_utc_ns']}\n"
                in metrics
            )
            assert "assist_live_sequence_gaps 0\n" in metrics
            assert "satellite-a" not in metrics
            from assist_live_server.auth import sign_event

            async with session.ws_connect(client.url + "/v1/device") as ws:
                for boot, seq, missing in (
                    (2**64 - 1, 3, 3),
                    (2**64 - 1, 1, 2),
                    (18, 0, 0),
                ):
                    packet = {
                        "version": 1,
                        "device": "satellite-a",
                        "boot": boot,
                        "seq": seq,
                        "device_ms": seq * 32,
                        "stage": "mic_open",
                        "data": {},
                    }
                    store.changed.clear()
                    await ws.send_json(
                        {**packet, "tag": sign_event(packet, b"synthetic-device-key")}
                    )
                    await asyncio.wait_for(store.changed.wait(), 2)
                    async with session.get(
                        client.url + "/health", headers=client._headers
                    ) as response:
                        snapshot = await response.json()
                    assert snapshot["sequence_gaps_by_satellite"] == {
                        "satellite-a": {"boot_id": boot, "missing": missing}
                    }, "gap_scope_or_boot_wrong"
                    assert (
                        snapshot["counters_since_utc_ns"]
                        == health["counters_since_utc_ns"]
                    )

    asyncio.run(run())


@pytest.mark.parametrize("sha", ["b3b1e16", "c3aadd3", "5ac300b"])
def test_archived_clients_read_records_health_legacy_stream_and_export(tmp_path, sha):
    root = Path(__file__).resolve().parents[2]
    archive = subprocess.run(
        ["git", "archive", sha, "client"],
        cwd=root,
        capture_output=True,
        check=True,
        timeout=10,
    ).stdout
    with tarfile.open(fileobj=BytesIO(archive)) as source:
        source.extractall(tmp_path / "archived", filter="data")

    async def run():
        async with service(tmp_path) as (_, store, _, socket):
            for stage in (
                "tts_sentence",
                "tts_last_byte",
                "tts_text_received",
                "stt_final_received",
                "tts_audio_received",
            ):
                data = {"request": 0} if stage.startswith("tts_") else {}
                if stage == "tts_sentence":
                    data.update(
                        index=0, text_end=None, text_ms=0, first_byte_ms=10, audio_ms=20
                    )
                if stage == "tts_last_byte":
                    data.update(sentences=1, audio_ms=20)
                await store.append(
                    event(
                        stage,
                        "integration"
                        if stage in {"stt_final_received", "tts_audio_received"}
                        else "server",
                        data=data,
                    )
                )
            await store.append(
                event("speaker_result", data={"destination": "local"}, stamp=2)
            )
            code = """
import asyncio, sys
from aiohttp import ClientSession, UnixConnector
from assist_live_client import AssistLiveClient
async def run():
    async with ClientSession(connector=UnixConnector(path=sys.argv[1])) as session:
        client = AssistLiveClient(
            "http://service.invalid", "synthetic-token", session=session
        )
        health = await client.health_capabilities(timeout=2)
        assert health.healthy, "archived_health_failed"
        turn = await client.turn(sys.argv[2])
        assert len(turn["events"]) == 6, "archived_record_read_failed"
        assert turn["events"][0]["data"] == {
            "request": 0, "index": 0, "text_end": None, "text_ms": 0,
            "first_byte_ms": 10, "audio_ms": 20,
        }, "archived_new_fields_lost"
        stream = client.events("satellite-a")
        try:
            row = await asyncio.wait_for(anext(stream), 2)
            assert row["event"]["stage"] == "speaker_result", "legacy_stream_new_stage"
        finally:
            await stream.aclose()
        rows = [r async for r in client.export("satellite-a", 2)]
        assert len(rows) == 1, "archived_export_failed"
        assert rows[0]["event"]["stage"] == "speaker_result", "archived_export_failed"
asyncio.run(run())
"""
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                code,
                str(socket),
                TURN,
                env={**os.environ, "PYTHONPATH": str(tmp_path / "archived/client")},
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(process.communicate(), 10)
            assert process.returncode == 0, stdout.decode() + stderr.decode()

    asyncio.run(run())

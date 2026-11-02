import asyncio
import json
import os
import sqlite3
import subprocess
import sys
import tarfile
from contextlib import asynccontextmanager, closing
from dataclasses import asdict
from io import BytesIO
from pathlib import Path
from uuid import uuid7

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


@pytest.mark.parametrize("fallback", [False, True])
def test_current_integration_ha_state_event_is_stored(tmp_path, fallback):
    import time

    async def run():
        async with service(tmp_path) as (client, _, session, _):
            if fallback:
                await client.event(
                    event("fallback_used", "integration", {"part": "stt"})
                )
            state = TimingEvent(
                TURN,
                "satellite-a",
                "ha_state",
                "satellite_state",
                time.time_ns(),
                "proxy",
                None,
                data={"state": "listening"},
            )
            async with session.post(
                client.url + "/v1/event", headers=client._headers, json=asdict(state)
            ) as response:
                assert response.status == 200, "ha_state_owner_not_stored"
            stored = (await client.turn(TURN))["events"][-1]
            assert stored == asdict(state), "ha_state_event_changed"

    asyncio.run(run())


def test_integration_source_does_not_own_satellite_state(tmp_path):
    async def run():
        async with service(tmp_path) as (client, _, session, _):
            async with session.post(
                client.url + "/v1/event",
                headers=client._headers,
                json=asdict(event("satellite_state", "integration", {"state": "idle"})),
            ) as response:
                assert response.status == 403, "integration_state_owner_not_refused"
                assert await response.json() == {"error": "wrong_owner"}

    asyncio.run(run())


@pytest.mark.parametrize("value", [-1, 2**63, 2**64 - 1])
@pytest.mark.parametrize("field", ["t_utc_ns", "opened_utc_ns", "since_utc_ns"])
def test_sqlite_integer_http_range_is_named_refusal(tmp_path, field, value):
    async def run():
        async with service(tmp_path) as (client, store, session, _):
            if field == "since_utc_ns":
                request = session.get(
                    client.url + "/v1/export",
                    headers=client._headers,
                    params={"satellite": "satellite-a", field: value},
                )
            elif field == "opened_utc_ns":
                payload = asdict(
                    TurnOpen(str(uuid7()), "satellite-a", "voice", None, None)
                )
                payload[field] = value
                request = session.post(
                    client.url + "/v1/turns", headers=client._headers, json=payload
                )
            else:
                payload = asdict(event("tts_first_text", "integration"))
                payload[field] = value
                request = session.post(
                    client.url + "/v1/event", headers=client._headers, json=payload
                )
            async with request as response:
                assert response.status == 400, "sqlite_integer_http_refusal_missing"
                assert await response.text() == json.dumps(
                    {"error": "integer_out_of_range"}
                ), "sqlite_integer_named_refusal_missing"
            assert (await store.get_turn(TURN))["events"] == []

    asyncio.run(run())


@pytest.mark.parametrize("value", [-1, 2**63, 2**64 - 1])
def test_sqlite_cursor_range_has_named_subscription_refusal(tmp_path, value):
    async def run():
        async with service(tmp_path) as (client, _, session, _):
            async with session.ws_connect(
                client.url + "/v1/events", headers=client._headers
            ) as ws:
                await ws.send_json(
                    {
                        "type": "subscribe",
                        "version": 1,
                        "satellite": "satellite-a",
                        "after_event_id": value,
                    }
                )
                message = await ws.receive(timeout=2)
                assert (
                    message.data == 1008 and message.extra == "integer_out_of_range"
                ), "sqlite_cursor_named_refusal_missing"

    asyncio.run(run())


def test_sqlite_signed_max_and_json_unsigned_max_remain_valid(tmp_path):
    async def run():
        async with service(tmp_path) as (client, store, session, _):
            payload = event(
                "tts_first_text", "integration", {"request": 2**64 - 1}, stamp=2**63 - 1
            )
            await client.event(payload)
            stored = (await client.turn(TURN))["events"][0]
            assert stored["t_utc_ns"] == 2**63 - 1
            assert stored["data"]["request"] == 2**64 - 1
            async with session.get(
                client.url + "/v1/export",
                headers=client._headers,
                params={"satellite": "satellite-a", "since_utc_ns": 2**63 - 1},
            ) as response:
                assert response.status == 200
                assert json.loads(await response.text())["event"] == stored
            assert (
                await store._call(store._next_event, "satellite-a", 2**63 - 1, 0)
                is None
            )

    asyncio.run(run())


@pytest.mark.parametrize(
    "source,stage", [("integration", "tts_first_text"), ("ha_state", "satellite_state")]
)
def test_integration_token_requires_turn_id(tmp_path, source, stage):
    async def run():
        async with service(tmp_path) as (client, store, session, _):
            payload = asdict(event(stage, source))
            payload["turn_id"] = None
            async with session.post(
                client.url + "/v1/event", headers=client._headers, json=payload
            ) as response:
                assert response.status == 403, "null_integration_turn_not_refused"
                assert await response.json() == {"error": "turn_required"}, (
                    "null_integration_turn_wrong_error"
                )
            assert [row async for row in store.export("satellite-a", 0)] == []

    asyncio.run(run())


@pytest.mark.parametrize(
    "value", ["", "x" * 65, "two words", "unicode-\u00e9", "path/value", ".", 7, False]
)
def test_conversation_identifier_has_named_refusal(tmp_path, value):
    async def run():
        async with service(tmp_path) as (client, _, session, _):
            payload = asdict(TurnOpen(str(uuid7()), "satellite-a", "voice", None, None))
            payload["conversation_id"] = value
            async with session.post(
                client.url + "/v1/turns", headers=client._headers, json=payload
            ) as response:
                assert response.status == 400, "conversation_identifier_not_refused"
                assert await response.text() == json.dumps(
                    {"error": "invalid_conversation_id"}
                ), "conversation_identifier_wrong_error"

    asyncio.run(run())


@pytest.mark.parametrize("value", [None, "a", "AZaz09_-", "x" * 64])
def test_conversation_identifier_valid_shapes_are_stored(tmp_path, value):
    async def run():
        async with service(tmp_path, conversation_id=value) as (client, _, _, _):
            assert (await client.turn(TURN))["conversation_id"] == value

    asyncio.run(run())


@pytest.mark.parametrize(
    "legacy_id",
    ["\U00010000" * 256, "", "x" * 65, "with space"],
    ids=["unicode", "empty", "overlength", "shape"],
)
def test_legacy_conversation_id_is_cleared_without_dropping_turn(
    tmp_path, caplog, legacy_id
):
    async def run():
        async with service(tmp_path) as (client, store, session, _):
            await client.event(event("tts_first_text", "integration"))
            await store.append(event("tts_first_byte"))
            await store.append(event("playback_start", "device"))
            await store.append(event("playback_end", "device"))
            with closing(sqlite3.connect(store.path)) as connection, connection:
                payload = json.loads(
                    connection.execute(
                        "SELECT payload FROM turns WHERE turn_id=?", (TURN,)
                    ).fetchone()[0]
                )
                payload["conversation_id"] = legacy_id
                connection.execute(
                    "UPDATE turns SET payload=?,status='open' WHERE turn_id=?",
                    (json.dumps(payload), TURN),
                )

            for expected_count in (1, 2):
                async with session.get(
                    client.url + "/v1/turns/" + TURN, headers=client._headers
                ) as response:
                    assert response.status == 200, "legacy_turn_read_dropped"
                    record = await response.json()
                assert record["conversation_id"] is None, (
                    "legacy_conversation_id_not_cleared"
                )
                assert len(record["events"]) == 4, "legacy_turn_events_dropped"
                diagnostics = await store.diagnostics()
                assert (
                    diagnostics.get("conversation_id_removed_total") == expected_count
                ), "legacy_conversation_id_not_counted_per_read"
                assert (await store.diagnostics(frozenset({"satellite-b"})))[
                    "conversation_id_removed_total"
                ] == 0
                assert diagnostics["invalid_turn_rows_total"] == 0

            exported = [row async for row in client.export("satellite-a", 0)]
            assert len(exported) == 4, "legacy_turn_export_broken"
            stream = client.events("satellite-a")
            try:
                subscribed = [
                    await asyncio.wait_for(anext(stream), 2) for _ in range(4)
                ]
                assert subscribed == exported, "legacy_turn_subscription_broken"
                assert "conversation_id" not in json.dumps(subscribed), (
                    "legacy_conversation_id_leaked"
                )
            finally:
                await stream.aclose()

            with closing(sqlite3.connect(store.path)) as connection, connection:
                connection.execute(
                    "UPDATE turns SET status='closing',closed_ns=? WHERE turn_id=?",
                    (store.now_ns(), TURN),
                )
            await store.maintain()
            with closing(sqlite3.connect(store.path)) as connection:
                raw, status = connection.execute(
                    "SELECT payload,status FROM turns WHERE turn_id=?", (TURN,)
                ).fetchone()
            assert status == "complete", "legacy_turn_maintenance_dropped"
            assert json.loads(raw)["conversation_id"] == legacy_id, (
                "legacy_read_rewrote_storage"
            )
            assert (await store.diagnostics())["conversation_id_removed_total"] == 3
            async with session.get(
                client.url + "/metrics", headers=client._headers
            ) as response:
                assert (
                    "assist_live_conversation_id_removed_total 3\n"
                    in await response.text()
                )
            assert "invalid stored row kind=turn" not in caplog.text
            if legacy_id:
                assert legacy_id not in caplog.text
                assert json.dumps(legacy_id) not in caplog.text

    asyncio.run(run())


@pytest.mark.parametrize("value", [None, "x" * 64])
def test_conforming_conversation_id_reads_do_not_count_removals(tmp_path, value):
    async def run():
        async with service(tmp_path, conversation_id=value) as (client, store, _, _):
            assert (await client.turn(TURN))["conversation_id"] == value
            assert (await store.diagnostics()).get(
                "conversation_id_removed_total"
            ) == 0, "valid_conversation_id_counted_as_removed"

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
                json=asdict(event("satellite_state", "ha_state")),
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
        async with service(tmp_path, transcripts=True, conversation_id="x" * 64) as (
            client,
            store,
            session,
            _,
        ):
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
            assert record["conversation_id"] == "x" * 64
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


@pytest.mark.parametrize("sha", ["b3b1e16", "c3aadd3", "5ac300b", "0f5241f"])
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
from uuid import uuid7
from aiohttp import ClientSession, UnixConnector
from assist_live_client import AssistLiveClient, TurnOpen
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
        empty_id_turn = str(uuid7())
        await client.open_turn(
            TurnOpen(empty_id_turn, "satellite-a", "tts_only", None, None)
        )
        empty_id_record = await client.turn(empty_id_turn)
        assert empty_id_record["conversation_id"] is None, "archived_absent_id_changed"
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

import asyncio
import hashlib
import hmac
import json
import sqlite3
from contextlib import closing
from dataclasses import asdict
from pathlib import Path
from uuid import uuid7

import pytest
from assist_live_client.models import TimingEvent, TurnOpen
from assist_live_server.auth import Registry, canonical_event, sign_event, verify_event
from assist_live_server.event_gateway import EventGateway
from assist_live_server.protocol import TimingIngress
from assist_live_server.store import SQLiteStore

ROOT = Path(__file__).resolve().parents[1]
KEY = b"test-key"


def device_event(stage="mic_open", data=None):
    return {
        "version": 1,
        "device": "satellite-a",
        "boot": 1,
        "seq": 0,
        "device_ms": 100,
        "stage": stage,
        "data": {} if data is None else data,
    }


@pytest.mark.parametrize(
    "data", [{}, {"pre_roll_ms": 0}, {"pre_roll_ms": 500}, {"pre_roll_ms": 1000}]
)
def test_pre_roll_survives_device_storage_and_turn_query(tmp_path, data):
    async def run():
        registry = Registry()
        registry.bind("satellite-a", "satellite-a", KEY)
        ingress = TimingIngress(registry)
        value = device_event(data=data)
        assert ingress.accept(
            json.dumps({**value, "tag": sign_event(value, KEY)}).encode()
        )
        store = SQLiteStore(tmp_path / "timing.db")
        try:
            turn = TurnOpen(str(uuid7()), "satellite-a", "voice", None, None)
            await store.open_turn(turn)
            pending = await ingress.queue.get()
            assert await store.accept_device(
                registry, pending, EventGateway(store).convert
            )
            rows = [row async for row in store.export("satellite-a", 0)]
            assert rows[0]["event"]["data"] == data, "stored pre_roll_ms was dropped"
            record = await store.get_turn(turn.turn_id)
            assert record["events"][0]["data"] == data, "turn query lost pre_roll_ms"
        finally:
            store.close()

    asyncio.run(run())


@pytest.mark.parametrize("value", [-1, 1001, True, False, 1.0, "100", None, [], {}])
def test_pre_roll_refuses_invalid_type_or_range(value):
    with pytest.raises(ValueError, match="invalid event data"):
        TimingEvent(
            None,
            "satellite-a",
            "device",
            "mic_open",
            1,
            "proxy",
            0,
            data={"pre_roll_ms": value},
        )


@pytest.mark.parametrize(
    "stage",
    ["wake_detected", "playback_start", "playback_end", "barge_in", "stt_final"],
)
def test_pre_roll_refuses_wrong_stage(stage):
    with pytest.raises(ValueError, match="invalid event data"):
        TimingEvent(
            None,
            "satellite-a",
            "device",
            stage,
            1,
            "proxy",
            0,
            data={"pre_roll_ms": 100},
        )


@pytest.mark.parametrize(
    "stage,value",
    [
        ("mic_open", -1),
        ("mic_open", 1001),
        ("mic_open", True),
        ("mic_open", 1.0),
        ("mic_open", "100"),
        ("mic_open", None),
        ("wake_detected", 100),
    ],
)
def test_pre_roll_invalid_signed_packet_uses_refused_path(stage, value):
    event = device_event(stage, {"pre_roll_ms": value})
    prefix = canonical_event(device_event(stage)).removesuffix(b"\x00\x00\x00\x02{}")
    data = json.dumps(event["data"], sort_keys=True, separators=(",", ":")).encode()
    payload = prefix + len(data).to_bytes(4, "big") + data
    tag = hmac.new(KEY, payload, hashlib.sha256).hexdigest()
    registry = Registry()
    registry.bind("satellite-a", "satellite-a", KEY)
    ingress = TimingIngress(registry)
    assert not ingress.accept(json.dumps({**event, "tag": tag}).encode()), (
        "invalid pre_roll_ms accepted"
    )
    assert ingress.refused == 1
    assert ingress.queue.empty()


def test_legacy_mic_open_canonical_fixture_unchanged():
    fixture = json.loads((ROOT / "protocol/device-vectors.json").read_text())[
        "vectors"
    ][0]
    assert fixture["event"] == device_event()
    assert canonical_event(fixture["event"]).hex() == fixture["canonical_hex"]
    assert sign_event(fixture["event"], KEY) == fixture["tag"]
    updated = device_event(data={"pre_roll_ms": 0})
    assert not verify_event(updated, fixture["tag"], KEY)


def test_pre_roll_survives_http_turn_query(tmp_path):
    from tests.contract.test_event_store import service

    async def run():
        async with service(tmp_path) as (client, _, _, ingress, _):
            turn = TurnOpen(str(uuid7()), "satellite-a", "voice", None, None)
            await client.open_turn(turn)
            value = device_event(data={"pre_roll_ms": 1000})
            result = asyncio.get_running_loop().create_future()
            assert ingress.accept(
                json.dumps(
                    {**value, "tag": sign_event(value, b"synthetic-device-key")}
                ).encode(),
                result=result,
            )
            assert await asyncio.wait_for(result, 2)
            record = await client.turn(turn.turn_id)
            assert record["events"][0]["data"] == {"pre_roll_ms": 1000}
            rows = [row async for row in client.export("satellite-a", 0)]
            assert rows[0]["event"]["data"] == {"pre_roll_ms": 1000}

    asyncio.run(run())


@pytest.mark.parametrize("restart", [False, True])
@pytest.mark.parametrize("site", ["pending", "queued"])
def test_expired_pre_roll_becomes_unmatched_without_poison(tmp_path, restart, site):
    async def run():
        now = [1_000_000_000]
        path = tmp_path / "timing.db"
        store = SQLiteStore(path, now_ns=lambda: now[0])
        event = TimingEvent(
            None,
            "satellite-a",
            "device",
            "mic_open",
            now[0],
            "proxy",
            0,
            data={"pre_roll_ms": 500},
        )
        try:
            if site == "pending":
                await store.append_device(event, now[0])
                assert (await store.diagnostics())["pending_events"] == 1
            if restart:
                store.close()
                store = SQLiteStore(path, now_ns=lambda: now[0])
            now[0] += 5_000_000_001
            try:
                if site == "queued":
                    await store.append_device(event, event.t_utc_ns)
                await store.maintain()
            except ValueError as error:
                pytest.fail(
                    f"{site} unmatched conversion rejected history: "
                    f"{type(error).__name__}"
                )
            assert (await store.diagnostics())["pending_events"] == 0
            assert (await store.diagnostics())["unmatched_events"] == 1
            assert (await store.diagnostics())["invalid_pending_rows_total"] == 0
            rows = [row async for row in store.export("satellite-a", 0)]
            assert len(rows) == 1
            assert rows[0]["event"]["stage"] == "unmatched"
            assert rows[0]["event"]["data"] == {"original_stage": "mic_open"}
            turn = TurnOpen(str(uuid7()), "satellite-a", "voice", None, None)
            await store.open_turn(turn)
            assert (await store.get_turn(turn.turn_id))["events"] == []
            store.close()
            store = SQLiteStore(path, now_ns=lambda: now[0])
            await store.maintain()
            assert (await store.get_turn(turn.turn_id))["events"] == []
            assert [row async for row in store.export("satellite-a", 0)] == rows
            fresh = TurnOpen(str(uuid7()), "satellite-a", "voice", None, None)
            await store.open_turn(fresh)
            assert await store.get_turn(fresh.turn_id) is not None
        finally:
            store.close()

    asyncio.run(run())


@pytest.mark.parametrize("expire", [False, True])
@pytest.mark.parametrize(
    "payload", ["{", "[]", '{"stage":"mic_open"}', "invalid_history"]
)
def test_bad_pending_row_does_not_block_store(tmp_path, expire, payload, caplog):
    async def run():
        now = [1_000_000_000]
        path = tmp_path / "timing.db"
        store = SQLiteStore(path, now_ns=lambda: now[0])
        bad = asdict(
            TimingEvent(None, "satellite-a", "device", "mic_open", now[0], "proxy", 0)
        )
        bad["data"] = {"pre_roll_ms": 1001}
        raw = json.dumps(bad) if payload == "invalid_history" else payload
        try:
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.execute(
                    "INSERT INTO pending(satellite,arrival_ns,payload) VALUES(?,?,?)",
                    ("satellite-a", now[0], raw),
                )
            store.close()
            store = SQLiteStore(path, now_ns=lambda: now[0])
            if expire:
                now[0] += 5_000_000_001
            await store.maintain()
            turn = TurnOpen(str(uuid7()), "satellite-a", "voice", None, None)
            await store.open_turn(turn)
            assert (await store.get_turn(turn.turn_id))["events"] == []
            assert [row async for row in store.export("satellite-a", 0)] == []
            assert (await store.diagnostics())["pending_events"] == 0
            assert (await store.diagnostics())["invalid_pending_rows_total"] == 1
            assert "invalid stored row kind=pending error=" in caplog.text
            assert (await store.diagnostics(frozenset({"satellite-b"})))[
                "invalid_pending_rows_total"
            ] == 0
            for record in caplog.records:
                if record.name == "assist_live_server.store":
                    assert record.getMessage() in {
                        "invalid stored row kind=pending error=JSONDecodeError",
                        "invalid stored row kind=pending error=TypeError",
                        "invalid stored row kind=pending error=ValueError",
                    }
        finally:
            store.close()

    asyncio.run(run())


@pytest.mark.parametrize("payload", ["[]", '{"stage":"mic_open"}', "invalid_history"])
def test_bad_event_row_does_not_block_status_query_or_export(tmp_path, payload):
    async def run():
        now = [1_000_000_000]
        path = tmp_path / "timing.db"
        store = SQLiteStore(path, now_ns=lambda: now[0])
        turn = TurnOpen(str(uuid7()), "satellite-a", "voice", None, None)
        event = TimingEvent(
            turn.turn_id,
            "satellite-a",
            "integration",
            "mic_open",
            now[0],
            "proxy",
            0,
            data={"pre_roll_ms": 1000},
        )
        bad = asdict(event)
        bad["stage"] = "unmatched"
        raw = json.dumps(bad) if payload == "invalid_history" else payload
        try:
            await store.open_turn(turn)
            await store.append(event)
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.execute(
                    "INSERT INTO events(satellite,turn_id,utc_ns,payload,stored_ns) "
                    "VALUES(?,?,?,?,?)",
                    (turn.satellite, turn.turn_id, now[0], raw, now[0]),
                )
            await store.append(
                TimingEvent(
                    turn.turn_id,
                    "satellite-a",
                    "integration",
                    "playback_end",
                    now[0],
                    "proxy",
                    0,
                )
            )
            assert (await store.diagnostics())["invalid_event_rows_total"] >= 1
            store.close()
            store = SQLiteStore(path, now_ns=lambda: now[0])
            now[0] += 5_000_000_001
            await store.maintain()
            record = await store.get_turn(turn.turn_id)
            assert record["status"] == "incomplete"
            assert [item["stage"] for item in record["events"]] == [
                "mic_open",
                "playback_end",
            ]
            assert record["events"][0]["data"] == {"pre_roll_ms": 1000}
            queried = (await store.diagnostics())["invalid_event_rows_total"]
            assert queried >= 1
            rows = [row async for row in store.export(turn.satellite, 0)]
            assert len(rows) == 2
            exported = (await store.diagnostics())["invalid_event_rows_total"]
            assert exported > queried
            stream = store.events(turn.satellite, 0)
            assert await asyncio.wait_for(anext(stream), 2) == rows[0]
            assert await asyncio.wait_for(anext(stream), 2) == rows[1]
            assert (await store.diagnostics())["invalid_event_rows_total"] > exported
            await stream.aclose()
            await store.open_turn(
                TurnOpen(str(uuid7()), "satellite-a", "voice", None, None)
            )
            with closing(sqlite3.connect(path)) as connection, connection:
                assert (
                    connection.execute("SELECT count(*) FROM events").fetchone()[0] == 3
                )
        finally:
            store.close()

    asyncio.run(run())


@pytest.mark.parametrize("payload", ["{", "[]", '{"turn_type":"voice"}'])
def test_bad_turn_row_does_not_block_satellite(tmp_path, payload):
    async def run():
        path = tmp_path / "timing.db"
        store = SQLiteStore(path, now_ns=lambda: 1_000_000_000)
        bad_id = str(uuid7())
        try:
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.execute(
                    "INSERT INTO turns VALUES(?,?,?,?, 'closing',?)",
                    (bad_id, "satellite-a", payload, 1, 1),
                )
            await store.maintain()
            assert (await store.diagnostics())["invalid_turn_rows_total"] == 1
            assert await store.get_turn(bad_id) is None
            assert (await store.diagnostics())["invalid_turn_rows_total"] == 2
            with pytest.raises(ValueError, match="invalid stored turn"):
                await store.append(
                    TimingEvent(
                        bad_id, "satellite-a", "integration", "mic_open", 1, "proxy", 0
                    )
                )
            assert (await store.diagnostics())["invalid_turn_rows_total"] == 3
            assert [row async for row in store.export("satellite-a", 0)] == []
            turn = TurnOpen(str(uuid7()), "satellite-a", "voice", None, None)
            await store.open_turn(turn)
            assert await store.get_turn(turn.turn_id) is not None
            with closing(sqlite3.connect(path)) as connection, connection:
                assert (
                    connection.execute("SELECT count(*) FROM turns").fetchone()[0] == 2
                )
        finally:
            store.close()

    asyncio.run(run())


def test_invalid_row_metrics_follow_satellite_access(tmp_path):
    from tests.contract.test_event_store import service

    async def run():
        async with service(tmp_path) as (client, session, store, _, _):
            with (
                closing(sqlite3.connect(tmp_path / "timing.db")) as connection,
                connection,
            ):
                for satellite in ("satellite-a", "satellite-b"):
                    connection.execute(
                        "INSERT INTO pending(satellite,arrival_ns,payload) "
                        "VALUES(?,?,?)",
                        (satellite, 1, "{"),
                    )
            await store.maintain()
            assert (await store.diagnostics())["invalid_pending_rows_total"] == 2
            async with session.get(
                "http://service.invalid/metrics", headers=client._headers
            ) as response:
                assert response.status == 200
                text = await response.text()
                assert "assist_live_invalid_pending_rows_total 1\n" in text
                assert "assist_live_invalid_event_rows_total 0\n" in text
                assert "assist_live_invalid_turn_rows_total 0\n" in text

    asyncio.run(run())

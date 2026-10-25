import asyncio
import json
from dataclasses import replace
from uuid import uuid7

import pytest
from assist_live_client import TimingEvent, TurnOpen


def test_retention_and_status_queries_use_indexes_and_acceptance_has_no_scans(tmp_path):
    from assist_live_server.auth import Registry
    from assist_live_server.event_gateway import EventGateway
    from assist_live_server.protocol import DeviceEvent
    from assist_live_server.store import SQLiteStore

    async def run():
        store = SQLiteStore(tmp_path / "timing.db")
        registry = Registry()
        registry.bind("device-a", "satellite-a", b"synthetic-key")
        await store.configure_registry(registry)
        statements = []

        def plans():
            queries = [
                "DELETE FROM events WHERE stored_ns<0",
                "DELETE FROM turns WHERE opened_ns<0",
                "DELETE FROM device_events WHERE stored_ns<0",
                "SELECT payload FROM events WHERE turn_id='test' ORDER BY event_id",
                "SELECT count(*) FROM events "
                "WHERE json_extract(payload, '$.stage')='unmatched' "
                "AND satellite='satellite-a'",
                "SELECT turn_id FROM turns WHERE status='closing'",
                "SELECT pending_id,payload FROM pending WHERE arrival_ns<=0 "
                "ORDER BY arrival_ns,pending_id",
            ]
            for query in queries:
                plan = " ".join(
                    str(row)
                    for row in store.connection.execute("EXPLAIN QUERY PLAN " + query)
                )
                assert "SEARCH" in plan, (query, plan)
                if "json_extract" in query:
                    assert "<expr>=?" in plan, (query, plan)
            store.connection.set_trace_callback(statements.append)

        try:
            await store._call(plans)
            gateway = EventGateway(store)
            event = DeviceEvent(
                "satellite-a",
                {
                    "device": "device-a",
                    "boot": 1,
                    "seq": 0,
                    "device_ms": 0,
                    "stage": "mic_open",
                    "data": {},
                },
                False,
                store.now_ns(),
            )
            assert await store.accept_device(registry, event, gateway.convert)
            assert not any(
                "DELETE FROM events" in query
                or "DELETE FROM turns" in query
                or "FROM pending WHERE arrival_ns" in query
                for query in statements
            )
            statements.clear()
            await store.maintain()
            assert any("DELETE FROM events" in query for query in statements)
            statements.clear()
            await store.maintain()
            assert not any("DELETE FROM events" in query for query in statements)
        finally:
            store.close()

    asyncio.run(run())


def test_direct_registry_rotation_cannot_clear_replay(tmp_path):
    from assist_live_server.auth import Registry
    from assist_live_server.replay_store import SQLiteReplayStore

    replay = SQLiteReplayStore(tmp_path / "timing.db")
    registry = Registry(tmp_path / "credentials", replay_store=replay)
    registry.bind("device-a", "satellite-a", b"synthetic-old-key")
    assert registry.accept_sequence("device-a", 1, 0)
    before = (tmp_path / "credentials").read_bytes()
    try:
        with pytest.raises(ValueError, match="SQLiteStore.rotate"):
            registry.rotate("device-a", b"synthetic-new-key")
        assert (tmp_path / "credentials").read_bytes() == before
        assert replay.load_replay("device-a").high == 0
        assert not registry.accept_sequence("device-a", 1, 0)
    finally:
        replay.close()


def test_rotation_refuses_a_registry_bound_to_a_different_store(tmp_path):
    from assist_live_server.auth import Registry
    from assist_live_server.store import SQLiteStore

    async def run():
        registry = Registry(tmp_path / "credentials")
        registry.bind("device-a", "satellite-a", b"synthetic-old-key")
        first = SQLiteStore(tmp_path / "first.db")
        second = SQLiteStore(tmp_path / "second.db")
        try:
            await first.configure_registry(registry)
            before = (tmp_path / "credentials").read_bytes()
            with pytest.raises(ValueError, match="registry must use this store"):
                await second.rotate(registry, "device-a", b"synthetic-new-key")
            assert (tmp_path / "credentials").read_bytes() == before
            assert registry.device("device-a") == ("satellite-a", b"synthetic-old-key")
        finally:
            first.close()
            second.close()

    asyncio.run(run())


def test_store_restart_retention_cursor_and_isolation(tmp_path):
    from assist_live_server.store import SQLiteStore

    async def run():
        now = [100_000_000_000]
        path = tmp_path / "timing.db"
        turn = TurnOpen(str(uuid7()), "satellite-a", "voice", None, None)
        store = SQLiteStore(path, now_ns=lambda: now[0], retention_days=1)
        await store.open_turn(turn)
        event = TimingEvent(
            turn.turn_id,
            turn.satellite,
            "integration",
            "mic_open",
            now[0],
            "proxy",
            2.5,
        )
        await store.append(event)
        first = [r async for r in store.export("satellite-a", 0)]
        assert first[0]["event"]["quality"] == "proxy"
        assert first[0]["event"]["uncertainty_ms"] == 2.5
        assert [r async for r in store.export("satellite-b", 0)] == []
        assert [r async for r in store.export("satellite-a", now[0] + 1)] == []
        with pytest.raises(ValueError, match="satellite"):
            await store.append(replace(event, satellite="satellite-b"))
        store.close()
        store = SQLiteStore(path, now_ns=lambda: now[0], retention_days=1)
        assert (await store.get_turn(turn.turn_id))["satellite"] == "satellite-a"
        assert (await store.diagnostics())["incomplete_turns"] == 1
        await store.append(replace(event, stage="playback_end", t_utc_ns=now[0] + 1))
        resumed = store.events("satellite-a", first[0]["event_id"])
        assert (await anext(resumed))["event"]["stage"] == "playback_end"
        await resumed.aclose()
        now[0] += 86_400_000_000_001
        await store.maintain()
        assert [r async for r in store.export("satellite-a", 0)] == []
        assert (await store.diagnostics())["events_total"] == 2
        assert await store.get_turn(turn.turn_id) is None
        fresh = TurnOpen(str(uuid7()), "satellite-a", "voice", None, None)
        await store.open_turn(fresh)
        await store.append(replace(event, turn_id=fresh.turn_id, t_utc_ns=now[0]))
        assert [r async for r in store.export("satellite-a", 0)][0]["event_id"] > 2
        store.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "policy,opt_in", [(False, False), (False, True), (True, False), (True, True)]
)
def test_store_transcript_policy_and_all_written_bytes(
    tmp_path, caplog, capsys, policy, opt_in
):
    from assist_live_server.store import SQLiteStore

    async def run():
        marker = "PCM-MARKER-NOT-FOR-STORAGE"
        transcript = "selected synthetic transcript"
        store = SQLiteStore(tmp_path / "timing.db", record_transcripts=policy)
        turn = TurnOpen(str(uuid7()), "satellite-a", "voice", None, None, opt_in)
        await store.open_turn(turn)
        await store.append(
            TimingEvent(
                turn.turn_id,
                turn.satellite,
                "integration",
                "stt_final",
                100,
                "measured",
                None,
                data={
                    "transcript": transcript,
                    "text": transcript,
                    "audio": marker,
                    "nested": {"pcm": marker},
                    "buffer": marker.encode(),
                },
            )
        )
        rows = [r async for r in store.export(turn.satellite, 0)]
        assert rows[0]["event"]["data"] == (
            {"transcript": transcript} if policy and opt_in else {}
        )
        assert marker not in json.dumps(rows)
        assert transcript not in json.dumps(await store.diagnostics())
        written = b"".join(p.read_bytes() for p in tmp_path.iterdir() if p.is_file())
        assert marker.encode() not in written
        assert (transcript.encode() in written) == (policy and opt_in)
        assert marker not in caplog.text and transcript not in caplog.text
        store.close()

    asyncio.run(run())
    with capsys.disabled():
        print("\nPASS transcript opt-in")


def test_durable_replay_rejects_event_and_previous_boot_after_restart(tmp_path):
    from assist_live_server.auth import Registry
    from assist_live_server.replay_store import SQLiteReplayStore

    credentials = tmp_path / "credentials"
    path = tmp_path / "timing.db"
    replay = SQLiteReplayStore(path)
    registry = Registry(credentials, replay_store=replay)
    registry.bind("satellite-a", "satellite-a", b"synthetic-device-key")
    assert registry.accept_sequence("satellite-a", 2**64 - 1, 0)
    assert registry.accept_sequence("satellite-a", 2, 0)
    assert registry.accept_sequence("satellite-a", 2, 2)
    assert registry.missing("satellite-a") == 1
    replay.close()
    replay = SQLiteReplayStore(path)
    registry = Registry.load(credentials, replay_store=replay)
    assert not registry.accept_sequence("satellite-a", 2, 0)
    assert not registry.accept_sequence("satellite-a", 2**64 - 1, 1)
    assert registry.accept_sequence("satellite-a", 2, 1)
    assert registry.missing("satellite-a") == 0
    replay.close()

    async def rotate():
        from assist_live_server.store import SQLiteStore

        store = SQLiteStore(path)
        try:
            await store.configure_registry(registry)
            await store.rotate(registry, "satellite-a", b"new-synthetic-key")
            assert await store._call(store.replay.load_replay, "satellite-a") is None
            assert await store._call(
                registry.accept_sequence, "satellite-a", 2**64 - 1, 1
            )
        finally:
            store.close()

    asyncio.run(rotate())


def test_worker_keeps_replay_and_event_atomic_without_blocking_loop(
    tmp_path, monkeypatch
):
    import threading

    from assist_live_server.auth import Registry
    from assist_live_server.event_gateway import EventGateway
    from assist_live_server.protocol import DeviceEvent
    from assist_live_server.replay_store import SQLiteReplayStore
    from assist_live_server.store import SQLiteStore

    async def run():
        store = SQLiteStore(tmp_path / "timing.db", now_ns=lambda: 1_000_000_000)
        registry = Registry()
        registry.bind("satellite-a", "satellite-a", b"synthetic-key")
        entered = asyncio.Event()
        release = threading.Event()
        loop = asyncio.get_running_loop()
        original = SQLiteReplayStore.save_replay

        def slow(self, device, state):
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(2)
            return original(self, device, state)

        monkeypatch.setattr(SQLiteReplayStore, "save_replay", slow)
        device = DeviceEvent(
            "satellite-a",
            {
                "device": "satellite-a",
                "boot": 1,
                "seq": 0,
                "device_ms": 1000,
                "stage": "wake_detected",
                "data": {},
            },
            True,
            1_000_000_000,
        )
        gateway = EventGateway(store)
        try:
            await store.configure_registry(registry)
            pending = asyncio.create_task(
                store.accept_device(registry, device, gateway.convert)
            )
            try:
                async with asyncio.timeout(1):
                    await entered.wait()
                    pulse = loop.create_future()
                    loop.call_later(0.01, pulse.set_result, None)
                    await pulse
                assert not pending.done()
            finally:
                release.set()
            assert await pending
            assert not await store.accept_device(registry, device, gateway.convert)
            assert registry.replay["satellite-a"].received == 1
        finally:
            store.close()

    asyncio.run(run())


@pytest.mark.parametrize("turn_type", ["voice", "tts_only"])
def test_turn_completeness_and_reorder_deadline(tmp_path, turn_type):
    from assist_live_server.store import SQLiteStore

    async def run():
        now = [1_000_000_000]
        store = SQLiteStore(tmp_path / "timing.db", now_ns=lambda: now[0])
        stages = ["tts_first_text", "tts_first_byte", "playback_start", "playback_end"]
        if turn_type == "voice":
            stages += [
                "wake_detected",
                "mic_open",
                "speech_start",
                "end_decision",
                "stt_final",
            ]
        try:
            for missing in [False, True]:
                turn = TurnOpen(str(uuid7()), "satellite-a", turn_type, None, None)
                await store.open_turn(turn)
                assert (await store.get_turn(turn.turn_id))["status"] == "open"
                for stage in [
                    s for s in stages if not missing or s != "tts_first_byte"
                ]:
                    await store.append(
                        TimingEvent(
                            turn.turn_id,
                            turn.satellite,
                            "integration",
                            stage,
                            now[0],
                            "proxy",
                            7,
                        )
                    )
                record = await store.get_turn(turn.turn_id)
                assert record["status"] == ("open" if missing else "complete")
                assert all(
                    e["quality"] == "proxy" and e["uncertainty_ms"] == 7
                    for e in record["events"]
                )
                now[0] += 5_000_000_001
                assert (await store.get_turn(turn.turn_id))["status"] == (
                    "incomplete" if missing else "complete"
                )
        finally:
            store.close()

    asyncio.run(run())


@pytest.mark.parametrize("expired", ["open", "closing"])
def test_append_cannot_complete_a_turn_after_its_deadline(tmp_path, expired):
    from assist_live_server.store import SQLiteStore

    async def run():
        now = [1_000_000_000]
        store = SQLiteStore(tmp_path / "timing.db", now_ns=lambda: now[0])
        turn = TurnOpen(str(uuid7()), "satellite-a", "tts_only", None, None)

        async def append(stage):
            await store.append(
                TimingEvent(
                    turn.turn_id,
                    turn.satellite,
                    "integration",
                    stage,
                    now[0],
                    "proxy",
                    0,
                )
            )

        try:
            await store.open_turn(turn)
            if expired == "closing":
                for stage in ("tts_first_text", "playback_start", "playback_end"):
                    await append(stage)
                now[0] += 5_000_000_000
                await append("tts_first_byte")
            else:
                now[0] += 120_000_000_000
                for stage in (
                    "tts_first_text",
                    "tts_first_byte",
                    "playback_start",
                    "playback_end",
                ):
                    await append(stage)
            assert (await store.get_turn(turn.turn_id))["status"] == "incomplete"
        finally:
            store.close()

    asyncio.run(run())


def test_one_writer_prevents_overlapping_acceptance(tmp_path, monkeypatch):
    import threading

    from assist_live_server.auth import Registry
    from assist_live_server.event_gateway import EventGateway
    from assist_live_server.protocol import DeviceEvent
    from assist_live_server.store import SQLiteStore

    async def run():
        store = SQLiteStore(tmp_path / "timing.db", now_ns=lambda: 1_000_000_000)
        registry = Registry()
        registry.bind("satellite-a", "satellite-a", b"synthetic-key")
        await store.configure_registry(registry)
        loop = asyncio.get_running_loop()
        entered = asyncio.Event()
        release = threading.Event()
        attempts = []
        original = store._accept_device

        def blocked(*args):
            attempts.append(threading.get_ident())
            if len(attempts) == 1:
                loop.call_soon_threadsafe(entered.set)
                assert release.wait(2)
            return original(*args)

        monkeypatch.setattr(store, "_accept_device", blocked)
        device = DeviceEvent(
            "satellite-a",
            {
                "device": "satellite-a",
                "boot": 1,
                "seq": 0,
                "device_ms": 1000,
                "stage": "wake_detected",
                "data": {},
            },
            True,
            1_000_000_000,
        )
        gateway = EventGateway(store)
        first = asyncio.create_task(
            store.accept_device(registry, device, gateway.convert)
        )
        second = asyncio.create_task(
            store.accept_device(registry, device, gateway.convert)
        )
        try:
            async with asyncio.timeout(1):
                await entered.wait()
                pulse = loop.create_future()
                loop.call_later(0.01, pulse.set_result, None)
                await pulse
            assert len(attempts) == 1
        finally:
            release.set()
            results = await asyncio.gather(first, second)
            store.close()
        assert results == [True, False]
        assert len(set(attempts)) == 1

    asyncio.run(run())


def test_device_transaction_failure_and_unique_guard(tmp_path):
    from assist_live_server.auth import Registry, ReplayState
    from assist_live_server.event_gateway import EventGateway
    from assist_live_server.protocol import DeviceEvent
    from assist_live_server.store import SQLiteStore

    async def run():
        store = SQLiteStore(tmp_path / "timing.db", now_ns=lambda: 1_000_000_000)
        registry = Registry()
        registry.bind("satellite-a", "satellite-a", b"synthetic-key")
        await store.configure_registry(registry)
        device = DeviceEvent(
            "satellite-a",
            {
                "device": "satellite-a",
                "boot": 1,
                "seq": 0,
                "device_ms": 1000,
                "stage": "wake_detected",
                "data": {},
            },
            True,
            1_000_000_000,
        )
        gateway = EventGateway(store)

        def fail(_):
            raise ValueError("synthetic conversion failure")

        try:
            assert not await store.accept_device(registry, device, fail)
            assert registry.replay["satellite-a"].high == -1
            assert await store.accept_device(registry, device, gateway.convert)
            registry.replay["satellite-a"] = ReplayState()
            samples = list(gateway.clocks["satellite-a"].samples)
            sequences = gateway.sequences.copy()
            assert not await store.accept_device(registry, device, gateway.convert)
            assert list(gateway.clocks["satellite-a"].samples) == samples
            assert gateway.sequences == sequences
            assert (await store.diagnostics())["pending_events"] == 1
        finally:
            store.close()

    asyncio.run(run())


def test_credential_rotation_refuses_already_queued_old_key(tmp_path):
    from assist_live_server.auth import Registry, sign_event
    from assist_live_server.event_gateway import EventGateway
    from assist_live_server.protocol import TimingIngress
    from assist_live_server.store import SQLiteStore

    async def run():
        registry = Registry()
        registry.bind("satellite-a", "satellite-a", b"synthetic-old-key")
        store = SQLiteStore(tmp_path / "timing.db")
        await store.configure_registry(registry)
        ingress = TimingIngress(registry)
        gateway = EventGateway(store)
        value = {
            "version": 1,
            "device": "satellite-a",
            "boot": 1,
            "seq": 0,
            "device_ms": 1000,
            "stage": "wake_detected",
            "data": {},
        }
        try:
            assert ingress.accept(
                json.dumps(
                    {**value, "tag": sign_event(value, b"synthetic-old-key")}
                ).encode()
            )
            old = await ingress.queue.get()
            ingress.queue.task_done()
            await store.rotate(registry, "satellite-a", b"synthetic-new-key")
            assert not await store.accept_device(registry, old, gateway.convert)
            assert ingress.accept(
                json.dumps(
                    {**value, "tag": sign_event(value, b"synthetic-new-key")}
                ).encode()
            )
            new = await ingress.queue.get()
            ingress.queue.task_done()
            assert await store.accept_device(registry, new, gateway.convert)
        finally:
            store.close()

    asyncio.run(run())


def test_rotation_credential_write_failure_keeps_replay_and_key(tmp_path, monkeypatch):
    from assist_live_server.auth import Registry
    from assist_live_server.event_gateway import EventGateway
    from assist_live_server.protocol import DeviceEvent
    from assist_live_server.store import SQLiteStore

    async def run():
        path = tmp_path / "credentials"
        registry = Registry(path)
        registry.bind("satellite-a", "satellite-a", b"old-key")
        store = SQLiteStore(tmp_path / "timing.db")
        await store.configure_registry(registry)
        gateway = EventGateway(store)
        device = DeviceEvent(
            "satellite-a",
            {
                "device": "satellite-a",
                "boot": 1,
                "seq": 0,
                "device_ms": 1000,
                "stage": "wake_detected",
                "data": {},
            },
            True,
            store.now_ns(),
        )

        def fail():
            raise OSError("synthetic credential-write failure")

        try:
            assert await store.accept_device(registry, device, gateway.convert)
            original = path.read_bytes()
            monkeypatch.setattr(registry, "save", fail)
            with pytest.raises(OSError, match="credential-write"):
                await store.rotate(registry, "satellite-a", b"new-key")
            assert path.read_bytes() == original
            assert registry.device("satellite-a") == ("satellite-a", b"old-key")
            assert (
                bytes.fromhex(registry.devices["satellite-a"]["secret"]) == b"old-key"
            )
            assert (
                await store._call(store.replay.load_replay, "satellite-a")
            ).high == 0
            assert not await store.accept_device(registry, device, gateway.convert)
        finally:
            store.close()

    asyncio.run(run())

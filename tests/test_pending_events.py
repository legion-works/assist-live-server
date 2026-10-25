import asyncio
from uuid import uuid7

from assist_live_client import TurnOpen


def test_pending_identity_expiry_quality_and_ingress_arrival(tmp_path):
    from assist_live_server.event_gateway import EventGateway
    from assist_live_server.protocol import DeviceEvent
    from assist_live_server.store import SQLiteStore

    async def run():
        now = [1_010_000_000]
        store = SQLiteStore(tmp_path / "timing.db", now_ns=lambda: now[0])
        gateway = EventGateway(store)

        def device(seq, arrival, stage="wake_detected", boot=1, device_ms=1000):
            return DeviceEvent(
                "satellite-a",
                {
                    "boot": boot,
                    "device_ms": device_ms,
                    "seq": seq,
                    "stage": stage,
                    "data": {"quality": "proxy"},
                },
                seq == 0,
                arrival,
            )

        now[0] = 3_000_000_000
        await gateway.accept(device(0, 1_010_000_000))
        other = TurnOpen(str(uuid7()), "satellite-b", "voice", None, None)
        await store.open_turn(other)
        assert [r async for r in store.export("satellite-b", 0)] == []
        turn = TurnOpen(str(uuid7()), "satellite-a", "voice", None, None)
        await store.open_turn(turn)
        rows = [r async for r in store.export("satellite-a", 0)]
        assert rows[0]["event"]["turn_id"] == turn.turn_id
        assert rows[0]["event"]["t_utc_ns"] == 1_010_000_000
        assert rows[0]["event"]["quality"] == "proxy"
        await gateway.accept(device(2, 3_030_000_000, "playback_end", device_ms=1100))
        rows = [r async for r in store.export("satellite-a", 0)]
        assert rows[-1]["event"]["quality"] == "proxy"
        assert rows[-1]["event"]["uncertainty_ms"] == 960
        assert gateway.metrics.sequence_gaps == 1
        await gateway.accept(device(1, 3_040_000_000, device_ms=1050))
        assert gateway.metrics.sequence_gaps == 0
        now[0] = 9_000_000_000
        await store.maintain()
        rows = [r async for r in store.export("satellite-a", 0)]
        assert rows[-1]["event"]["stage"] == "wake_detected"
        assert rows[-1]["event"]["turn_id"] == turn.turn_id
        assert (await store.get_turn(turn.turn_id))["status"] == "incomplete"
        await gateway.accept(device(0, 9_000_000_000, boot=2, device_ms=1))
        now[0] = 14_000_000_001
        late = TurnOpen(str(uuid7()), "satellite-a", "voice", None, None)
        await store.open_turn(late)
        rows = [r async for r in store.export("satellite-a", 0)]
        assert rows[-1]["event"]["stage"] == "unmatched"
        assert rows[-1]["event"]["t_utc_ns"] == 9_000_000_000
        assert rows[-1]["event"]["uncertainty_ms"] == 0
        store.close()

    asyncio.run(run())


def test_pending_survives_store_restart_and_turn_timeout(tmp_path):
    from assist_live_client import TimingEvent
    from assist_live_server.store import SQLiteStore

    async def run():
        now = [1_000_000_000]
        path = tmp_path / "timing.db"
        store = SQLiteStore(path, now_ns=lambda: now[0])
        await store.append_device(
            TimingEvent(
                None, "satellite-a", "device", "wake_detected", now[0], "proxy", 1
            ),
            now[0],
        )
        store.close()
        store = SQLiteStore(path, now_ns=lambda: now[0])
        now[0] = 6_000_000_001
        assert [r async for r in store.export("satellite-a", 0)][0]["event"][
            "stage"
        ] == "unmatched"
        turn = TurnOpen(str(uuid7()), "satellite-a", "voice", None, None)
        await store.open_turn(turn)
        now[0] += 120_000_000_001
        await store.append_device(
            TimingEvent(None, "satellite-a", "device", "mic_open", now[0], "proxy", 0),
            now[0],
        )
        now[0] += 5_000_000_001
        assert [r async for r in store.export("satellite-a", 0)][-1]["event"][
            "stage"
        ] == "unmatched"
        assert (await store.diagnostics())["incomplete_turns"] == 1
        store.close()

    asyncio.run(run())


def test_device_quality_cannot_break_ingress_consumer(tmp_path):
    from assist_live_server.event_gateway import EventGateway
    from assist_live_server.protocol import DeviceEvent
    from assist_live_server.store import SQLiteStore

    async def run():
        store = SQLiteStore(tmp_path / "timing.db", now_ns=lambda: 1_000_000_000)
        try:
            gateway = EventGateway(store)
            await gateway.accept(
                DeviceEvent(
                    "satellite-a",
                    {
                        "boot": 1,
                        "seq": 0,
                        "device_ms": 1000,
                        "stage": "wake_detected",
                        "data": {"quality": ["measured"]},
                    },
                    True,
                    1_000_000_000,
                )
            )
            turn = TurnOpen(str(uuid7()), "satellite-a", "voice", None, None)
            await store.open_turn(turn)
            assert [r async for r in store.export(turn.satellite, 0)][0]["event"][
                "quality"
            ] == "proxy"
        finally:
            store.close()

    asyncio.run(run())


def test_queued_expired_event_does_not_attach_to_late_turn(tmp_path):
    from assist_live_client import TimingEvent
    from assist_live_server.store import SQLiteStore

    async def run():
        store = SQLiteStore(tmp_path / "timing.db", now_ns=lambda: 7_000_000_000)
        try:
            turn = TurnOpen(str(uuid7()), "satellite-a", "voice", None, None)
            await store.open_turn(turn)
            await store.append_device(
                TimingEvent(
                    None,
                    turn.satellite,
                    "device",
                    "wake_detected",
                    1_000_000_000,
                    "proxy",
                    0,
                ),
                1_000_000_000,
            )
            rows = [r async for r in store.export(turn.satellite, 0)]
            assert rows[0]["event"]["stage"] == "unmatched"
            assert rows[0]["event"]["turn_id"] is None
        finally:
            store.close()

    asyncio.run(run())

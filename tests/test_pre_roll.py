import asyncio
import hashlib
import hmac
import json
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

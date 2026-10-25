import asyncio
import json
import logging
import stat

import pytest


def event(**changes):
    return {
        "version": 1,
        "device": "satellite-a",
        "boot": 1,
        "seq": 0,
        "device_ms": 100,
        "stage": "mic_open",
        "data": {},
        **changes,
    }


def test_stage_tamper_is_rejected():
    from assist_live_server.auth import sign_event, verify_event

    original = event()
    tag = sign_event(original, b"test-key")
    assert not verify_event({**original, "stage": "playback_end"}, tag, b"test-key")


def test_all_signed_fields_and_vectors():
    from pathlib import Path

    from assist_live_server.auth import canonical_event, sign_event, verify_event

    root = Path(__file__).resolve().parents[2]
    vectors = json.loads((root / "protocol/device-vectors.json").read_text())
    for vector in vectors["vectors"]:
        key = bytes.fromhex(vector["key_hex"])
        value = vector["event"]
        assert canonical_event(value).hex() == vector["canonical_hex"]
        assert sign_event(value, key) == vector["tag"]
        for field, changed in event(
            version=2,
            device="satellite-b",
            boot=9,
            seq=9,
            device_ms=999,
            stage="playback_end",
            data={"quality": "proxy"},
        ).items():
            assert not verify_event({**value, field: changed}, vector["tag"], key)


@pytest.mark.parametrize("adapter", ["udp", "websocket"])
def test_shared_timing_contract(adapter, tmp_path, replay_store):
    from assist_live_server.auth import Registry, sign_event
    from assist_live_server.protocol import (
        TimingIngress,
        UDPTransport,
        WebSocketTransport,
    )

    async def run():
        registry = Registry(tmp_path / "credentials", replay_store=replay_store)
        registry.bind("satellite-a", "satellite-a", b"test-key")
        ingress = TimingIngress(registry, queue_size=2)
        receiver = (
            UDPTransport(ingress) if adapter == "udp" else WebSocketTransport(ingress)
        )

        def send(value, key=b"test-key"):
            packet = json.dumps({**value, "tag": sign_event(value, key)}).encode()
            return receiver.accept(packet)

        assert send(event(seq=0))
        assert not send(event(seq=0))
        assert send(event(seq=2))
        assert ingress.queue.qsize() == 2
        assert not send(event(seq=3))
        assert (await ingress.queue.get()).satellite == "satellite-a"
        await ingress.queue.get()
        assert ingress.missing("satellite-a") == 1
        assert send(event(seq=1))
        assert ingress.missing("satellite-a") == 0
        await ingress.queue.get()
        assert send(event(boot=2, seq=0, device_ms=1))
        accepted = await ingress.queue.get()
        assert accepted.new_boot and accepted.event["device_ms"] == 1
        assert accepted.arrival_ns > 0
        assert not send(event(boot=1, seq=4))
        registry = Registry.load(tmp_path / "credentials", replay_store=replay_store)
        restarted = TimingIngress(registry)
        receiver = (
            UDPTransport(restarted)
            if adapter == "udp"
            else WebSocketTransport(restarted)
        )
        assert not send(event(boot=1, seq=0))
        assert not send(event(boot=2, seq=0))
        assert not send(event(device="unknown"))
        assert not send(event(data={"satellite": "satellite-b"}, seq=1, boot=2))
        changed = event(boot=2, seq=1)
        packet = {
            **changed,
            "tag": sign_event(changed, b"test-key"),
            "stage": "playback_end",
        }
        assert not receiver.accept(json.dumps(packet).encode())
        assert not receiver.accept(b"x" * 4097)
        assert not send(event(boot=2, seq=1), b"integration-test-token")
        registry.rotate("satellite-a", b"replacement-key")
        assert not send(event(boot=2, seq=2))
        assert send(event(boot=2, seq=0), b"replacement-key")

    asyncio.run(run())


def test_registry_isolation_and_provisioning(tmp_path, caplog, capsys):
    from assist_live_server.auth import Registry
    from assist_live_server.provision import provision

    caplog.set_level(logging.DEBUG)
    path = tmp_path / "credentials"
    provision(path, "satellite-a")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    raw = json.loads(path.read_text())
    registry = Registry.load(path)
    device = bytes.fromhex(raw["devices"]["satellite-a"]["secret"])
    token = next(iter(raw["tokens"]))
    assert registry.authorize(token) == frozenset({"satellite-a"})
    assert registry.authorize(device.decode(errors="replace")) is None
    with pytest.raises(ValueError):
        registry.bind("another-device", "satellite-a", b"other-key")
    with pytest.raises(ValueError):
        registry.bind("satellite-a", "satellite-b", device)
    assert not capsys.readouterr().out
    assert token not in caplog.text
    assert device.hex() not in caplog.text


def test_replay_window_accepts_long_run_with_bounded_memory():
    from assist_live_server.auth import REPLAY_WINDOW, Registry

    registry = Registry()
    registry.bind("satellite-a", "satellite-a", b"test-key")
    assert registry.accept_sequence("satellite-a", 1, 0)
    for seq in range(200_000):
        assert registry.accept_sequence("satellite-a", 2, seq)
        assert registry.replay["satellite-a"].bitmap.bit_length() <= REPLAY_WINDOW
    state = registry.replay["satellite-a"]
    assert state.high == 199_999
    assert state.seen_boots == frozenset({1, 2})
    assert registry.missing("satellite-a") == 0
    assert not registry.accept_sequence("satellite-a", 1, 0)
    assert not registry.accept_sequence("satellite-a", 1, 200_000)
    assert registry.accept_sequence("satellite-a", 3, 0)


def test_stale_and_duplicate_are_different_classes():
    from assist_live_server.auth import REPLAY_WINDOW, Registry

    registry = Registry()
    registry.bind("satellite-a", "satellite-a", b"test-key")
    assert registry.accept_sequence("satellite-a", 1, 0)
    assert registry.accept_sequence("satellite-a", 1, REPLAY_WINDOW)
    assert not registry.accept_sequence("satellite-a", 1, 0)
    assert registry.replay["satellite-a"].stale == 1
    assert registry.replay["satellite-a"].duplicates == 0
    assert registry.accept_sequence("satellite-a", 1, 1)
    assert not registry.accept_sequence("satellite-a", 1, 1)
    assert registry.replay["satellite-a"].stale == 1
    assert registry.replay["satellite-a"].duplicates == 1
    assert registry.accept_sequence("satellite-a", 1, (1 << 64) - 1)
    assert registry.replay["satellite-a"].bitmap == 1


def test_boot_capacity_has_operator_visible_error(caplog):
    from assist_live_server.auth import Registry

    registry = Registry(boot_limit=2)
    registry.bind("satellite-a", "satellite-a", b"test-key")
    assert registry.accept_sequence("satellite-a", 1, 0)
    assert registry.accept_sequence("satellite-a", 2, 0)
    assert not registry.accept_sequence("satellite-a", 3, 0)
    assert "device reboot limit reached" in caplog.text
    assert "test-key" not in caplog.text


@pytest.mark.parametrize("adapter", ["udp", "websocket"])
def test_adapter_receive_boundary(adapter, service):
    async def run():
        from aiohttp import WSMsgType
        from assist_live_server.auth import sign_event
        from assist_live_server.protocol import UDPTransport

        async with service() as (_, state):

            def packet(**changes):
                value = event(**changes)
                return json.dumps(
                    {**value, "tag": sign_event(value, b"test-device-secret")}
                ).encode()

            async def send(raw, expected, close_code=1008):
                before = state.ingress.queue.qsize()
                if adapter == "udp":
                    UDPTransport(state.ingress).datagram_received(
                        raw, ("192.0.2.1", 1234)
                    )
                else:
                    async with state.session.ws_connect(state.url + "/v1/device") as ws:
                        await ws.send_bytes(raw)
                        if expected:
                            await ws.send_bytes(b"invalid")
                        reply = await asyncio.wait_for(ws.receive(), 1)
                        assert reply.type == WSMsgType.CLOSE
                        assert reply.data == close_code
                assert state.ingress.queue.qsize() == before + int(expected)

            await send(packet(), True)
            await state.ingress.queue.get()
            await send(packet(), False)
            await send(packet(seq=2), True)
            await state.ingress.queue.get()
            await send(packet(seq=1), True)
            await state.ingress.queue.get()
            await send(packet(boot=2), True)
            await state.ingress.queue.get()
            await send(packet(boot=1, seq=3), False)
            await send(packet(device="unknown"), False)
            changed = json.loads(packet(boot=2, seq=1))
            changed["stage"] = "playback_end"
            await send(json.dumps(changed).encode(), False)
            await send(b"x" * 4097, False, close_code=1009)

    asyncio.run(run())


def test_device_secret_cannot_open_audio(service):
    async def run():
        from aiohttp import WSServerHandshakeError

        async with service() as (_, state):
            with pytest.raises(WSServerHandshakeError) as refused:
                async with state.session.ws_connect(
                    state.url + "/v1/audio",
                    headers={"Authorization": "Bearer test-device-secret"},
                ):
                    raise AssertionError("device secret opened audio")
            assert refused.value.status == 401

    asyncio.run(run())


def test_credentials_remain_disjoint_in_each_provisioning_order():
    from assist_live_server.auth import Registry

    registry = Registry()
    registry.bind("satellite-a", "satellite-a", b"device-test-key")
    registry.add_token("integration-test-token", ["satellite-a"])
    with pytest.raises(ValueError):
        registry.bind("satellite-b", "satellite-b", b"integration-test-token")
    with pytest.raises(ValueError):
        registry.add_token("device-test-key", ["satellite-a"])
    with pytest.raises(ValueError):
        registry.rotate("satellite-a", b"device-test-key")


def test_replay_persistence_failure_does_not_advance_window(replay_store, monkeypatch):
    from assist_live_server.auth import Registry

    registry = Registry(replay_store=replay_store)
    registry.bind("satellite-a", "satellite-a", b"test-key")
    original = replay_store.save_replay

    def broken(device, state):
        raise OSError("test storage failure")

    monkeypatch.setattr(replay_store, "save_replay", broken)
    assert not registry.accept_sequence("satellite-a", 1, 0)
    assert registry.replay["satellite-a"].high == -1
    monkeypatch.setattr(replay_store, "save_replay", original)
    assert registry.accept_sequence("satellite-a", 1, 0)


@pytest.mark.parametrize("adapter", ["udp", "websocket"])
def test_adapter_stale_is_not_duplicate(adapter):
    from assist_live_server.auth import REPLAY_WINDOW, Registry, sign_event
    from assist_live_server.protocol import (
        TimingIngress,
        UDPTransport,
        WebSocketTransport,
    )

    async def run():
        registry = Registry()
        registry.bind("satellite-a", "satellite-a", b"test-key")
        ingress = TimingIngress(registry)
        receiver = (
            UDPTransport(ingress) if adapter == "udp" else WebSocketTransport(ingress)
        )

        def send(seq):
            value = event(seq=seq)
            return receiver.accept(
                json.dumps({**value, "tag": sign_event(value, b"test-key")}).encode()
            )

        assert send(0)
        assert send(REPLAY_WINDOW)
        assert not send(0)
        assert registry.replay["satellite-a"].last_result == "stale"
        assert registry.replay["satellite-a"].duplicates == 0
        assert not send(REPLAY_WINDOW)
        assert registry.replay["satellite-a"].last_result == "duplicate"
        assert registry.replay["satellite-a"].stale == 1
        assert send(1)

    asyncio.run(run())

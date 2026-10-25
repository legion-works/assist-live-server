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
        from assist_live_server.event_gateway import EventGateway
        from assist_live_server.store import SQLiteStore

        registry = Registry(tmp_path / "credentials")
        registry.bind("satellite-a", "satellite-a", b"test-key")
        store = SQLiteStore(tmp_path / "timing.db")
        gateway = EventGateway(store)
        ingress = TimingIngress(registry, queue_size=2)
        receiver = (
            UDPTransport(ingress) if adapter == "udp" else WebSocketTransport(ingress)
        )

        def enqueue(value, key=b"test-key"):
            return receiver.accept(
                json.dumps({**value, "tag": sign_event(value, key)}).encode()
            )

        async def consume():
            pending = await ingress.queue.get()
            assert pending.satellite == "satellite-a" and pending.arrival_ns > 0
            try:
                return await store.accept_device(registry, pending, gateway.convert)
            finally:
                ingress.queue.task_done()

        async def send(value, key=b"test-key"):
            return await consume() if enqueue(value, key) else False

        try:
            await store.configure_registry(registry)
            assert enqueue(event(seq=0))
            assert enqueue(event(seq=2))
            assert ingress.queue.qsize() == 2
            assert not enqueue(event(seq=3))
            assert ingress.dropped == 1
            assert await consume() and await consume()
            assert not await send(event(seq=0))
            assert ingress.missing("satellite-a") == 1
            assert await send(event(seq=1))
            assert ingress.missing("satellite-a") == 0
            assert await send(event(boot=2, seq=0, device_ms=1))
            assert gateway.clocks["satellite-a"].boot == 2
            assert not await send(event(boot=1, seq=4))
            registry = Registry.load(tmp_path / "credentials")
            await store.configure_registry(registry)
            ingress = TimingIngress(registry)
            receiver = (
                UDPTransport(ingress)
                if adapter == "udp"
                else WebSocketTransport(ingress)
            )
            assert not await send(event(boot=1, seq=0))
            assert not await send(event(boot=2, seq=0))
            assert not await send(event(device="unknown"))
            assert not await send(
                event(data={"satellite": "satellite-b"}, seq=1, boot=2)
            )
            changed = event(boot=2, seq=1)
            packet = {
                **changed,
                "tag": sign_event(changed, b"test-key"),
                "stage": "playback_end",
            }
            assert not receiver.accept(json.dumps(packet).encode())
            assert not receiver.accept(b"x" * 4097)
            assert not await send(event(boot=2, seq=1), b"integration-test-token")
            await store.rotate(registry, "satellite-a", b"replacement-key")
            assert not await send(event(boot=2, seq=2))
            assert await send(event(boot=2, seq=0), b"replacement-key")
        finally:
            store.close()

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
                before = len(state.store.rows)
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
                async with asyncio.timeout(1):
                    await state.ingress.queue.join()
                assert len(state.store.rows) == before + int(expected)
                if expected:
                    stored = state.store.rows[-1]["event"]
                    accepted = json.loads(raw)
                    assert stored["satellite"] == accepted["device"]
                    assert stored["stage"] == accepted["stage"]
                    assert stored["seq"] == accepted["seq"]

            await send(packet(), True)
            await send(packet(), False)
            await send(packet(seq=2), True)
            await send(packet(seq=1), True)
            await send(packet(boot=2), True)
            await send(packet(boot=1, seq=3), False)
            await send(packet(device="unknown"), False)
            changed = json.loads(packet(boot=2, seq=1))
            changed["stage"] = "playback_end"
            await send(json.dumps(changed).encode(), False)
            await send(b"x" * 4097, False, close_code=1009)
            await send(
                packet(boot=2, seq=1, data={"padding": "x" * 4200}),
                False,
                close_code=1009,
            )
            await send(packet(boot=2, seq=1), True)

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
def test_adapter_stale_is_not_duplicate(adapter, tmp_path):
    from assist_live_server.auth import REPLAY_WINDOW, Registry, sign_event
    from assist_live_server.protocol import (
        TimingIngress,
        UDPTransport,
        WebSocketTransport,
    )

    async def run():
        from assist_live_server.event_gateway import EventGateway
        from assist_live_server.store import SQLiteStore

        registry = Registry()
        registry.bind("satellite-a", "satellite-a", b"test-key")
        ingress = TimingIngress(registry)
        receiver = (
            UDPTransport(ingress) if adapter == "udp" else WebSocketTransport(ingress)
        )

        store = SQLiteStore(tmp_path / "timing.db")
        await store.configure_registry(registry)
        gateway = EventGateway(store)

        async def send(seq):
            value = event(seq=seq)
            assert receiver.accept(
                json.dumps({**value, "tag": sign_event(value, b"test-key")}).encode()
            )
            pending = await ingress.queue.get()
            try:
                return await store.accept_device(registry, pending, gateway.convert)
            finally:
                ingress.queue.task_done()

        try:
            assert await send(0)
            assert await send(REPLAY_WINDOW)
            assert not await send(0)
            assert registry.replay["satellite-a"].last_result == "stale"
            assert registry.replay["satellite-a"].duplicates == 0
            assert not await send(REPLAY_WINDOW)
            assert registry.replay["satellite-a"].last_result == "duplicate"
            assert registry.replay["satellite-a"].stale == 1
            assert await send(1)
        finally:
            store.close()

    asyncio.run(run())


@pytest.mark.parametrize("adapter", ["udp", "websocket"])
def test_valid_signed_oversized_device_event_is_refused(adapter):
    from assist_live_server.auth import Registry, sign_event, verify_event
    from assist_live_server.protocol import (
        TimingIngress,
        UDPTransport,
        WebSocketTransport,
    )

    registry = Registry()
    registry.bind("satellite-a", "satellite-a", b"test-key")
    ingress = TimingIngress(registry)
    receiver = (
        UDPTransport(ingress) if adapter == "udp" else WebSocketTransport(ingress)
    )
    value = event(data={"padding": "x" * 4200})
    tag = sign_event(value, b"test-key")
    raw = json.dumps({**value, "tag": tag}).encode()
    assert len(raw) > 4096
    assert json.loads(raw)["data"]["padding"] == "x" * 4200
    assert verify_event(value, tag, b"test-key")
    assert not receiver.accept(raw)
    assert ingress.queue.empty()
    if adapter == "udp":
        good = event(seq=1)
        assert receiver.accept(
            json.dumps({**good, "tag": sign_event(good, b"test-key")}).encode()
        )
        assert ingress.queue.qsize() == 1


def test_unknown_device_and_wrong_tag_use_hmac_and_constant_compare(monkeypatch):
    from assist_live_server import auth, protocol

    registry = auth.Registry()
    registry.bind("satellite-a", "satellite-a", b"test-key")
    ingress = protocol.TimingIngress(registry)
    verified, comparisons = [], []
    original_verify = protocol.verify_event
    original_compare = auth.hmac.compare_digest

    def verify(value, tag, secret):
        verified.append(value["device"])
        return original_verify(value, tag, secret)

    def compare(left, right):
        comparisons.append((len(left), len(right)))
        return original_compare(left, right)

    monkeypatch.setattr(protocol, "verify_event", verify)
    monkeypatch.setattr(auth.hmac, "compare_digest", compare)
    for device in ["satellite-a", "satellite-x"]:
        value = event(device=device)
        raw = json.dumps(
            {**value, "tag": auth.sign_event(value, b"wrong-key")}
        ).encode()
        assert not ingress.accept(raw)
    assert verified == ["satellite-a", "satellite-x"]
    assert comparisons == [(64, 64), (64, 64)]
    assert ingress.queue.empty()
    unknown = event(device="satellite-x")
    assert not ingress.accept(
        json.dumps({**unknown, "tag": auth.sign_event(unknown, bytes(32))}).encode()
    )
    known = event()
    assert ingress.accept(
        json.dumps({**known, "tag": auth.sign_event(known, b"test-key")}).encode()
    )


def test_device_identity_and_credentials_plan_binding(tmp_path, store, replay_store):
    from aiohttp import (
        ClientSession,
        UnixConnector,
        WSMsgType,
        WSServerHandshakeError,
        web,
    )
    from assist_live_server.app import create_app
    from assist_live_server.auth import Registry, sign_event
    from assist_live_server.protocol import TimingIngress

    async def run():
        registry = Registry(replay_store=replay_store)
        secret = b"device-test-key"
        token = "integration-test-token"
        registry.bind("device-a", "satellite-a", secret)
        registry.add_token(token, ["satellite-a"])
        ingress = TimingIngress(registry)
        runner = web.AppRunner(
            create_app(registry, store, ingress=ingress), access_log=None
        )
        await runner.setup()
        path = tmp_path / "mapping.sock"
        await web.UnixSite(runner, path).start()
        try:
            async with ClientSession(connector=UnixConnector(path=path)) as session:
                async with session.get(
                    "http://service.invalid/v1/bindings",
                    headers={"Authorization": "Bearer " + token},
                ) as response:
                    assert response.status == 200
                    assert await response.json() == [{"satellite": "satellite-a"}]
                for value, key in [
                    (
                        event(device="device-a", data={"satellite": "satellite-b"}),
                        secret,
                    ),
                    (event(device="device-a"), token.encode()),
                ]:
                    async with session.ws_connect(
                        "http://service.invalid/v1/device"
                    ) as ws:
                        await ws.send_json({**value, "tag": sign_event(value, key)})
                        result = await ws.receive(timeout=1)
                        assert result.type == WSMsgType.CLOSE and result.data == 1008
                with pytest.raises(WSServerHandshakeError) as refused:
                    await session.ws_connect(
                        "http://service.invalid/v1/audio",
                        headers={"Authorization": "Bearer " + secret.decode()},
                    )
                assert refused.value.status == 401
                value = event(device="device-a", seq=1)
                async with session.ws_connect("http://service.invalid/v1/device") as ws:
                    await ws.send_json({**value, "tag": sign_event(value, secret)})
                    async with asyncio.timeout(1):
                        await store.changed.wait()
                        await ingress.queue.join()
                assert store.rows[-1]["event"]["satellite"] == "satellite-a"
                assert store.rows[-1]["event"]["seq"] == 1
        finally:
            await runner.cleanup()

    asyncio.run(run())


def test_rotation_failure_does_not_publish_new_credential(tmp_path, monkeypatch):
    from assist_live_server.auth import Registry
    from assist_live_server.replay_store import SQLiteReplayStore

    path = tmp_path / "credentials"
    replay = SQLiteReplayStore(tmp_path / "replay.db")
    registry = Registry(path, replay_store=replay)
    registry.bind("satellite-a", "satellite-a", b"old-key")
    assert registry.accept_sequence("satellite-a", 1, 0)
    original = path.read_bytes()

    def fail(_):
        raise OSError("synthetic replay-clear failure")

    monkeypatch.setattr(replay, "clear_replay", fail)
    try:
        with pytest.raises(OSError, match="replay-clear"):
            registry.rotate("satellite-a", b"new-key")
        assert path.read_bytes() == original
        assert registry.device("satellite-a") == ("satellite-a", b"old-key")
        assert replay.load_replay("satellite-a").high == 0
    finally:
        replay.close()


def test_rotation_refuses_another_devices_secret():
    from assist_live_server.auth import Registry

    registry = Registry()
    registry.bind("satellite-a", "satellite-a", b"key-a")
    registry.bind("satellite-b", "satellite-b", b"key-b")
    with pytest.raises(ValueError):
        registry.rotate("satellite-a", b"key-b")
    assert registry.device("satellite-a") == ("satellite-a", b"key-a")


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/health"),
        ("GET", "/metrics"),
        ("GET", "/v1/bindings"),
        ("POST", "/v1/turns"),
        ("POST", "/v1/event"),
        ("GET", "/v1/turns/{turn_id}"),
        ("GET", "/v1/export?satellite=satellite-a"),
        ("WS", "/v1/audio"),
        ("WS", "/v1/events"),
    ],
)
def test_device_secret_refused_on_every_integration_route(
    service, turn_id, caplog, method, path
):
    from dataclasses import asdict

    from aiohttp import WSServerHandshakeError
    from assist_live_client import TimingEvent, TurnOpen

    async def run():
        async with service() as (client, state):
            turn = TurnOpen(turn_id, "satellite-a", "voice", None, None)
            await client.open_turn(turn)
            headers = {"Authorization": "Bearer test-device-secret"}
            url = state.url + path.format(turn_id=turn_id)
            if method == "WS":
                with pytest.raises(WSServerHandshakeError) as refused:
                    await state.session.ws_connect(url, headers=headers)
                assert refused.value.status == 401
            else:
                payload = (
                    asdict(turn)
                    if path == "/v1/turns"
                    else asdict(
                        TimingEvent(
                            turn_id,
                            "satellite-a",
                            "integration",
                            "mic_open",
                            1,
                            "measured",
                            None,
                        )
                    )
                )
                async with state.session.request(
                    method,
                    url,
                    headers=headers,
                    json=payload if method == "POST" else None,
                ) as response:
                    assert response.status == 401
                    assert "test-device-secret" not in await response.text()
            assert "test-device-secret" not in caplog.text
            assert "integration-test-token" not in caplog.text

    asyncio.run(run())


@pytest.mark.parametrize("transport", ["websocket", "udp"])
def test_device_ingress_requires_replay_hooks(service, transport):
    from aiohttp import WSServerHandshakeError
    from assist_live_server.app import create_app

    async def run():
        async with service(replay=False) as (client, state):
            if transport == "websocket":
                with pytest.raises(WSServerHandshakeError) as refused:
                    await state.session.ws_connect(state.url + "/v1/device")
                assert refused.value.status == 503
            else:
                with pytest.raises(ValueError, match="replay"):
                    create_app(
                        state.registry, state.store, udp_address=("127.0.0.1", 0)
                    )
            assert not await client.health()
            assert state.ingress.queue.empty()

    asyncio.run(run())

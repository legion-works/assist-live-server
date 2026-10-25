import asyncio
import json
import logging
from contextlib import asynccontextmanager
from uuid import uuid7

import pytest
from aiohttp import ClientSession, UnixConnector, web
from assist_live_client import AssistLiveClient, TimingEvent, TurnOpen
from assist_live_client.protocol import ProtocolError, encode_control


@asynccontextmanager
async def service(tmp_path, policy=False, udp=False, malformed=False, queue_size=128):
    from assist_live_server.app import UDP_ADDRESS, create_app
    from assist_live_server.auth import Registry
    from assist_live_server.protocol import TimingIngress
    from assist_live_server.store import SQLiteStore

    path = tmp_path / "timing.db"
    store = SQLiteStore(path, record_transcripts=policy)
    registry = Registry()
    for satellite in ["satellite-a", "satellite-b"]:
        registry.bind(satellite, satellite, b"synthetic-device-key")
    registry.add_token("synthetic-integration-token", ["satellite-a"])
    ingress = TimingIngress(registry, queue_size=queue_size)

    class Backend:
        async def stt(self, turn_id, audio):
            async for _ in audio:
                pass
            yield encode_control(
                "final",
                str(uuid7()) if malformed else turn_id,
                text="synthetic final text",
            )

        async def tts(self, turn_id, text, voice):
            async for _ in text:
                pass
            yield b"\x00\x00"

    app = create_app(
        registry,
        store,
        Backend(),
        record_transcripts=policy,
        ingress=ingress,
        udp_address=("127.0.0.1", 0) if udp else None,
    )
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    socket = tmp_path / "service.sock"
    await web.UnixSite(runner, socket).start()
    try:
        async with ClientSession(
            connector=UnixConnector(path=socket, limit=0)
        ) as session:
            async with AssistLiveClient(
                "http://service.invalid", "synthetic-integration-token", session=session
            ) as client:
                yield client, session, store, ingress, app.get(UDP_ADDRESS)
    finally:
        await runner.cleanup()
        store.close()


def packet(seq, stage="wake_detected", satellite="satellite-a"):
    from assist_live_server.auth import sign_event

    value = {
        "version": 1,
        "device": satellite,
        "boot": 1,
        "seq": seq,
        "device_ms": 1000 + seq * 100,
        "stage": stage,
        "data": {"quality": "proxy", "audio": "PCM-DEVICE-MARKER"},
    }
    return json.dumps(
        {**value, "tag": sign_event(value, b"synthetic-device-key")}
    ).encode()


def test_worker_store_error_resolves_result_and_accepts_next_event(
    tmp_path, monkeypatch, caplog
):
    async def run():
        async with service(tmp_path) as (_, _, store, ingress, _):
            original = store.accept_device

            async def broken(registry, device, convert):
                if device.event["seq"] == 0:
                    raise RuntimeError("synthetic store failure")
                return await original(registry, device, convert)

            monkeypatch.setattr(store, "accept_device", broken)
            results = [asyncio.get_running_loop().create_future() for _ in range(2)]
            for seq, result in enumerate(results):
                assert ingress.accept(packet(seq), result=result)
            async with asyncio.timeout(2):
                assert await asyncio.gather(*results) == [False, True]
                await ingress.queue.join()
            assert ingress.worker_errors == 1
            assert ingress.refused == 1 and ingress.accepted == 1
            assert "timing acceptance failed" in caplog.text
            assert "synthetic store failure" not in caplog.text

    asyncio.run(run())


def test_non_sqlite_store_failure_refuses_acceptance_result(replay_store):
    from contextlib import suppress

    from assist_live_server.auth import Registry
    from assist_live_server.event_gateway import EventGateway
    from assist_live_server.protocol import TimingIngress

    from tests.contract.conftest import MemoryStore

    class FailingStore(MemoryStore):
        async def append(self, event):
            raise RuntimeError("synthetic append failure")

    async def run():
        registry = Registry(replay_store=replay_store)
        registry.bind("satellite-a", "satellite-a", b"synthetic-device-key")
        ingress = TimingIngress(registry)
        worker = asyncio.create_task(EventGateway(FailingStore()).run(ingress))
        try:
            result = asyncio.get_running_loop().create_future()
            assert ingress.accept(packet(0), result=result)
            assert await asyncio.wait_for(result, 2) is False
            await ingress.queue.join()
            assert ingress.accepted == 0
            assert ingress.refused == 1 and ingress.worker_errors == 1
        finally:
            worker.cancel()
            with suppress(asyncio.CancelledError):
                await worker

    asyncio.run(run())


def test_duplicate_turn_error_type_controls_conflict_not_message(tmp_path, monkeypatch):
    from dataclasses import asdict

    from assist_live_server.protocol import TurnExistsError

    async def run():
        async with service(tmp_path) as (client, session, store, _, _):

            async def duplicate(turn):
                raise TurnExistsError("different store wording")

            monkeypatch.setattr(store, "open_turn", duplicate)
            turn = TurnOpen(str(uuid7()), "satellite-a", "voice", None, None)
            async with session.post(
                "http://service.invalid/v1/turns",
                headers=client._headers,
                json=asdict(turn),
            ) as response:
                assert response.status == 409

            async def invalid(turn):
                raise ValueError("turn already exists")

            monkeypatch.setattr(store, "open_turn", invalid)
            async with session.post(
                "http://service.invalid/v1/turns",
                headers=client._headers,
                json=asdict(turn),
            ) as response:
                assert response.status == 400

    asyncio.run(run())


def test_maintenance_recovers_after_store_error(tmp_path, monkeypatch, caplog):
    from assist_live_server.store import SQLiteStore

    async def run():
        recovered = asyncio.Event()
        original = SQLiteStore.maintain
        calls = 0

        async def broken(self):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("synthetic maintenance failure")
            await original(self)
            recovered.set()

        monkeypatch.setattr(SQLiteStore, "maintain", broken)
        async with service(tmp_path) as (client, session, _, _, _):
            async with asyncio.timeout(2):
                await recovered.wait()
            async with session.get(
                "http://service.invalid/metrics", headers=client._headers
            ) as response:
                assert "assist_live_maintenance_errors_total 1" in await response.text()
            assert "timing maintenance failed" in caplog.text
            assert "synthetic maintenance failure" not in caplog.text

    asyncio.run(run())


def test_concurrent_open_turn_returns_conflict_not_invalid_request(
    tmp_path, monkeypatch
):
    from dataclasses import asdict

    async def run():
        async with service(tmp_path) as (client, session, store, _, _):
            original = store.get_turn
            release = asyncio.Event()
            reads = 0

            async def simultaneous(turn_id):
                nonlocal reads
                result = await original(turn_id)
                reads += 1
                if reads == 2:
                    release.set()
                await release.wait()
                return result

            monkeypatch.setattr(store, "get_turn", simultaneous)
            turn = TurnOpen(str(uuid7()), "satellite-a", "voice", None, None)

            async def post():
                async with session.post(
                    "http://service.invalid/v1/turns",
                    headers=client._headers,
                    json=asdict(turn),
                ) as response:
                    return response.status

            async with asyncio.timeout(2):
                assert sorted(await asyncio.gather(post(), post())) == [200, 409]

    asyncio.run(run())


def test_shutdown_counts_inflight_and_queued_drops_and_resolves_results(
    tmp_path, monkeypatch
):
    from assist_live_server.app import create_app
    from assist_live_server.auth import Registry
    from assist_live_server.protocol import TimingIngress
    from assist_live_server.store import SQLiteStore

    async def run():
        store = SQLiteStore(tmp_path / "timing.db")
        registry = Registry()
        registry.bind("satellite-a", "satellite-a", b"synthetic-device-key")
        ingress = TimingIngress(registry)
        entered = asyncio.Event()

        async def blocked(*args):
            entered.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(store, "accept_device", blocked)
        runner = web.AppRunner(create_app(registry, store, ingress=ingress))
        await runner.setup()
        results = [asyncio.get_running_loop().create_future() for _ in range(3)]
        try:
            assert ingress.accept(packet(0), result=results[0])
            async with asyncio.timeout(1):
                await entered.wait()
            for seq in (1, 2):
                assert ingress.accept(packet(seq), result=results[seq])
            await runner.cleanup()
            assert all(result.done() and result.result() is False for result in results)
            assert ingress.dropped == 3
            assert not ingress.accept(packet(3))
            assert ingress.dropped == 4
            async with asyncio.timeout(1):
                await ingress.queue.join()
        finally:
            await runner.cleanup()
            store.close()

    asyncio.run(run())


@pytest.mark.parametrize("adapter", ["udp", "websocket"])
def test_event_to_store_to_client(tmp_path, capsys, adapter):
    async def run():
        async with service(tmp_path, udp=adapter == "udp") as (
            client,
            session,
            store,
            ingress,
            address,
        ):
            from assist_live_server.app import UDP_ADDRESS

            if adapter == "udp":
                (
                    transport,
                    _,
                ) = await asyncio.get_running_loop().create_datagram_endpoint(
                    asyncio.DatagramProtocol, remote_addr=address
                )

                async def send(seq, stage):
                    transport.sendto(packet(seq, stage))
            else:
                assert address is None and UDP_ADDRESS is not None
                ws = await session.ws_connect("http://service.invalid/v1/device")

                async def send(seq, stage):
                    await ws.send_bytes(packet(seq, stage))

            try:
                await send(0, "wake_detected")
                async with asyncio.timeout(2):
                    await store.changed.wait()
                turn = TurnOpen(str(uuid7()), "satellite-a", "voice", None, None)
                await client.open_turn(turn)
                wake_stream = client.events(turn.satellite, 0)
                async with asyncio.timeout(2):
                    wake = await anext(wake_stream)
                await wake_stream.aclose()
                assert wake["event"]["stage"] == "wake_detected"
                assert wake["event"]["turn_id"] == turn.turn_id
                await send(1, "playback_end")
                stream = client.events(turn.satellite, wake["event_id"])
                async with asyncio.timeout(2):
                    end = await anext(stream)
                await stream.aclose()
                assert end["event"]["stage"] == "playback_end"
                assert end["event"]["quality"] == "proxy"
                assert end["event"]["uncertainty_ms"] >= 0
                queried = await client.turn(turn.turn_id)
                assert queried["satellite"] == turn.satellite
                assert queried["events"] == [wake["event"], end["event"]]
                assert queried["status"] == "open"
                exported = [r async for r in client.export(turn.satellite, 0)]
                assert exported == [wake, end]
                assert "PCM-DEVICE-MARKER" not in json.dumps(exported)
                written = b"".join(
                    p.read_bytes() for p in tmp_path.iterdir() if p.is_file()
                )
                assert b"PCM-DEVICE-MARKER" not in written
                assert ingress.queue.empty()
                async with session.get(
                    "http://service.invalid/metrics", headers=client._headers
                ) as response:
                    assert response.status == 200
                    metrics = await response.text()
                    assert "assist_live_events_total 2" in metrics
                    assert "assist_live_sequence_gaps 0" in metrics
                async with session.get("http://service.invalid/metrics") as response:
                    assert response.status == 401
            finally:
                if adapter == "udp":
                    transport.close()
                else:
                    await ws.close()

    asyncio.run(run())
    with capsys.disabled():
        print("\nPASS event-to-store-to-client")


def test_identity_isolation(tmp_path, capsys):
    async def run():
        async with service(tmp_path) as (client, session, store, ingress, _):
            assert ingress.accept(packet(0, satellite="satellite-b"))
            await ingress.queue.join()
            turn = TurnOpen(str(uuid7()), "satellite-a", "voice", None, None)
            await client.open_turn(turn)
            assert [r async for r in client.export(turn.satellite, 0)] == []
            async with session.get(
                "http://service.invalid/metrics", headers=client._headers
            ) as response:
                metrics = await response.text()
                assert "assist_live_pending_events 0" in metrics
            with pytest.raises(ProtocolError):
                await client.open_turn(
                    TurnOpen(str(uuid7()), "satellite-b", "voice", None, None)
                )
            value = json.loads(packet(1))
            value["data"]["satellite"] = "satellite-b"
            from assist_live_server.auth import sign_event

            value.pop("tag")
            value["tag"] = sign_event(value, b"synthetic-device-key")
            assert not ingress.accept(json.dumps(value).encode())
            async with session.get(
                "http://service.invalid/v1/export?satellite=satellite-b",
                headers=client._headers,
            ) as response:
                assert response.status == 403

    asyncio.run(run())
    with capsys.disabled():
        print("\nPASS identity isolation")


@pytest.mark.parametrize(
    "policy,opt_in", [(False, False), (False, True), (True, False), (True, True)]
)
def test_final_transcript_requires_both_opt_ins(
    tmp_path, caplog, capsys, policy, opt_in
):
    async def run():
        caplog.set_level(logging.DEBUG)
        async with service(tmp_path, policy) as (client, session, store, ingress, _):
            turn = TurnOpen(str(uuid7()), "satellite-a", "voice", None, None, opt_in)
            await client.open_turn(turn)

            async def audio():
                yield b"PCM-SYNTHETIC-MARKER"

            result = [r async for r in client.stt(turn.turn_id, audio())]
            assert any(r.get("text") == "synthetic final text" for r in result)
            await client.event(
                TimingEvent(
                    turn.turn_id,
                    turn.satellite,
                    "integration",
                    "stt_partial",
                    1,
                    "measured",
                    None,
                    data={
                        "text": "unselected partial text",
                        "audio": "PCM-SYNTHETIC-MARKER",
                    },
                )
            )
            rows = [r async for r in client.export(turn.satellite, 0)]
            final = [r["event"] for r in rows if r["event"]["stage"] == "stt_final"]
            assert len(final) == 1
            assert final[0]["data"] == (
                {"transcript": "synthetic final text"} if policy and opt_in else {}
            )
            written = b"".join(
                p.read_bytes() for p in tmp_path.iterdir() if p.is_file()
            )
            assert b"PCM-SYNTHETIC-MARKER" not in written
            assert b"unselected partial text" not in written
            assert (b"synthetic final text" in written) == (policy and opt_in)
            assert "synthetic final text" not in json.dumps(await store.diagnostics())
            assert "synthetic final text" not in caplog.text
            assert "PCM-SYNTHETIC-MARKER" not in caplog.text

    asyncio.run(run())
    with capsys.disabled():
        print("\nPASS transcript opt-in")


def test_application_restart_loads_durable_replay_before_ingress(tmp_path):
    from assist_live_server.app import create_app
    from assist_live_server.auth import Registry
    from assist_live_server.protocol import TimingIngress
    from assist_live_server.store import SQLiteStore

    async def run():
        credentials = tmp_path / "credentials"
        registry = Registry(credentials)
        registry.bind("satellite-a", "satellite-a", b"synthetic-device-key")
        path = tmp_path / "timing.db"
        for restart in [False, True]:
            if restart:
                registry = Registry.load(credentials)
            store = SQLiteStore(path)
            ingress = TimingIngress(registry)
            runner = web.AppRunner(create_app(registry, store, ingress=ingress))
            await runner.setup()
            try:
                first = packet(0)
                second = json.loads(packet(0))
                second.pop("tag")
                second["boot"] = 2
                from assist_live_server.auth import sign_event

                second["tag"] = sign_event(second, b"synthetic-device-key")
                for raw, expected in [
                    (first, not restart),
                    (json.dumps(second).encode(), not restart),
                    (packet(1), False),
                ]:
                    before = ingress.accepted
                    assert ingress.accept(raw)
                    async with asyncio.timeout(2):
                        await ingress.queue.join()
                    assert ingress.accepted - before == int(expected)
                async with asyncio.timeout(2):
                    await ingress.queue.join()
            finally:
                await runner.cleanup()
                store.close()

    asyncio.run(run())


def test_device_route_without_store_refuses_before_queueing(tmp_path):
    from assist_live_server.app import create_app
    from assist_live_server.auth import Registry

    async def run():
        app = create_app(Registry())
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        path = tmp_path / "empty.sock"
        await web.UnixSite(runner, path).start()
        try:
            async with ClientSession(connector=UnixConnector(path=path)) as session:
                async with session.get("http://service.invalid/v1/device") as response:
                    assert response.status == 503
        finally:
            await runner.cleanup()

    asyncio.run(run())


def test_invalid_backend_final_is_not_stored(tmp_path):
    async def run():
        async with service(tmp_path, policy=True, malformed=True) as (
            client,
            _,
            store,
            _,
            _,
        ):
            turn = TurnOpen(str(uuid7()), "satellite-a", "voice", None, None, True)
            await client.open_turn(turn)

            async def audio():
                yield b"\x00\x00"

            with pytest.raises(ProtocolError):
                _ = [r async for r in client.stt(turn.turn_id, audio())]
            assert [r async for r in client.export(turn.satellite, 0)] == []
            written = b"".join(
                p.read_bytes() for p in tmp_path.iterdir() if p.is_file()
            )
            assert b"synthetic final text" not in written

    asyncio.run(run())


def test_queue_full_drops_are_exposed_in_metrics(tmp_path):
    async def run():
        async with service(tmp_path, queue_size=2) as (client, session, _, ingress, _):
            assert ingress.accept(packet(0))
            assert ingress.accept(packet(1))
            assert not ingress.accept(packet(2))
            assert ingress.queue.qsize() == 2
            await ingress.queue.join()
            async with session.get(
                "http://service.invalid/metrics", headers=client._headers
            ) as response:
                assert response.status == 200
                text = await response.text()
                assert "assist_live_device_dropped_total 1\n" in text
                assert "assist_live_device_accepted_total 2\n" in text
                assert "assist_live_sequence_gaps 0\n" in text

    asyncio.run(run())


def test_unauthenticated_device_expires_without_event(tmp_path):
    from aiohttp import WSMsgType

    async def run():
        async with service(tmp_path) as (_, session, _, _, _):
            async with session.ws_connect("http://service.invalid/v1/device") as ws:
                closed = await ws.receive(timeout=2.8)
                assert closed.type == WSMsgType.CLOSE and closed.data == 1008

    asyncio.run(run())


def test_150_idle_device_connections_are_capped_and_memory_bounded(tmp_path, capsys):
    import tracemalloc

    from aiohttp import WSMsgType, WSServerHandshakeError

    async def run():
        async with service(tmp_path) as (_, session, store, ingress, _):
            baseline, _ = tracemalloc.get_traced_memory()
            clients = []

            async def connect():
                try:
                    ws = await session.ws_connect("http://service.invalid/v1/device")
                    clients.append(ws)
                    return 101
                except WSServerHandshakeError as error:
                    return error.status

            try:
                statuses = await asyncio.gather(*(connect() for _ in range(150)))
                assert 0 < statuses.count(101) <= 64
                assert statuses.count(503) >= 86
                _, peak = tracemalloc.get_traced_memory()
                assert peak - baseline < 32 * 1024 * 1024
                with capsys.disabled():
                    print(
                        f"\nPASS idle connection bound accepted={statuses.count(101)} "
                        f"refused={statuses.count(503)} peak_bytes={peak - baseline}"
                    )
                async with asyncio.timeout(3):
                    messages = await asyncio.gather(*(ws.receive() for ws in clients))
                assert all(
                    m.type == WSMsgType.CLOSE and m.data == 1008 for m in messages
                )
                async with session.ws_connect(
                    "http://service.invalid/v1/device"
                ) as replacement:
                    await replacement.send_bytes(packet(0))
                    async with asyncio.timeout(1):
                        await store.changed.wait()
                        await ingress.queue.join()
                    assert ingress.accepted == 1
            finally:
                await asyncio.gather(*(ws.close() for ws in clients))

    tracemalloc.start()
    try:
        asyncio.run(run())
    finally:
        tracemalloc.stop()


def test_failed_device_handshake_does_not_consume_slot(tmp_path, caplog):
    async def run():
        async with service(tmp_path) as (client, session, _, _, _):
            async with session.get("http://service.invalid/v1/device") as response:
                assert response.status == 400
            async with session.get(
                "http://service.invalid/metrics", headers=client._headers
            ) as response:
                assert "assist_live_device_connections 0\n" in await response.text()
            assert not [record for record in caplog.records if record.exc_info]

    asyncio.run(run())


def test_authenticated_device_outlives_first_event_deadline(tmp_path, monkeypatch):
    from assist_live_server import protocol

    monkeypatch.setattr(protocol, "DEVICE_AUTH_TIMEOUT", 0.05)

    async def run():
        async with service(tmp_path) as (_, session, store, ingress, _):
            async with session.ws_connect("http://service.invalid/v1/device") as ws:
                await ws.send_bytes(packet(0))
                async with asyncio.timeout(1):
                    await store.changed.wait()
                    await ingress.queue.join()
                store.changed.clear()
                loop = asyncio.get_running_loop()
                elapsed = loop.create_future()
                loop.call_later(0.1, elapsed.set_result, None)
                await elapsed
                await ws.send_bytes(packet(1))
                async with asyncio.timeout(1):
                    await store.changed.wait()
                    await ingress.queue.join()
                assert ingress.accepted == 2

    asyncio.run(run())


def test_device_deadline_releases_slot_without_peer_close_reply(tmp_path, monkeypatch):
    from assist_live_server import protocol

    monkeypatch.setattr(protocol, "DEVICE_AUTH_TIMEOUT", 0.05)

    async def run():
        async with service(tmp_path) as (client, session, _, _, _):
            ws = await session.ws_connect(
                "http://service.invalid/v1/device", autoclose=False
            )
            try:
                loop = asyncio.get_running_loop()
                elapsed = loop.create_future()
                loop.call_later(0.25, elapsed.set_result, None)
                await elapsed
                async with session.get(
                    "http://service.invalid/metrics", headers=client._headers
                ) as response:
                    assert "assist_live_device_connections 0\n" in await response.text()
            finally:
                await ws.close()

    asyncio.run(run())


def test_unauthenticated_pings_cannot_extend_first_event_deadline(
    tmp_path, monkeypatch
):
    from aiohttp import WSMsgType
    from assist_live_server import protocol

    monkeypatch.setattr(protocol, "DEVICE_AUTH_TIMEOUT", 0.3)

    async def run():
        async with service(tmp_path) as (_, session, _, _, _):
            async with session.ws_connect("http://service.invalid/v1/device") as ws:

                async def ping():
                    while True:
                        await asyncio.sleep(0.05)
                        await ws.ping()

                sender = asyncio.create_task(ping())
                try:
                    async with asyncio.timeout(0.45):
                        closed = await ws.receive()
                    assert closed.type == WSMsgType.CLOSE and closed.data == 1008
                finally:
                    sender.cancel()
                    await asyncio.gather(sender, return_exceptions=True)

    asyncio.run(run())


def test_connection_cap_refusal_closes_http_transport(tmp_path, monkeypatch):
    from aiohttp import WSServerHandshakeError
    from assist_live_server import protocol

    monkeypatch.setattr(protocol, "MAX_DEVICE_CONNECTIONS", 1)

    async def run():
        async with service(tmp_path) as (_, session, _, _, _):
            async with session.ws_connect("http://service.invalid/v1/device"):
                with pytest.raises(WSServerHandshakeError) as refused:
                    await session.ws_connect("http://service.invalid/v1/device")
                assert refused.value.status == 503
                assert refused.value.headers["Connection"].lower() == "close"

    asyncio.run(run())

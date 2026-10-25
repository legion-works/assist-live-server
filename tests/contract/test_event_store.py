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
async def service(tmp_path, policy=False, udp=False, malformed=False):
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
    ingress = TimingIngress(registry)

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
        async with ClientSession(connector=UnixConnector(path=socket)) as session:
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

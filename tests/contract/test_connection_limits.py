import asyncio
import json

import pytest
from aiohttp import WSMsgType, WSServerHandshakeError


def test_device_result_wait_is_bounded(service, monkeypatch):
    from assist_live_server import protocol
    from assist_live_server.auth import sign_event

    monkeypatch.setattr(protocol, "DEVICE_RESULT_TIMEOUT", 0.03, raising=False)

    async def run():
        async with service() as (_, state):
            entered = asyncio.Event()
            release = asyncio.Event()

            async def blocked(event):
                entered.set()
                await release.wait()

            monkeypatch.setattr(state.store, "append", blocked)
            value = {
                "version": 1,
                "device": "satellite-a",
                "boot": 1,
                "seq": 0,
                "device_ms": 0,
                "stage": "mic_open",
                "data": {},
            }
            async with state.session.ws_connect(state.url + "/v1/device") as ws:
                try:
                    await ws.send_json(
                        {**value, "tag": sign_event(value, b"test-device-secret")}
                    )
                    async with asyncio.timeout(1):
                        await entered.wait()
                    message = await asyncio.wait_for(ws.receive(), 0.5)
                    assert message.type == WSMsgType.CLOSE and message.data == 1008
                finally:
                    release.set()

    asyncio.run(run())


def test_authenticated_routes_share_global_and_per_token_caps(service, monkeypatch):
    from assist_live_server import app

    monkeypatch.setattr(app, "MAX_AUTH_CONNECTIONS", 4, raising=False)
    monkeypatch.setattr(app, "MAX_AUTH_PER_TOKEN", 2, raising=False)

    async def run():
        async with service() as (_, state):
            sockets = []
            for token in ("second-test-token", "third-test-token"):
                state.registry.add_token(token, ["satellite-a"])

            async def connect(route, token="integration-test-token"):
                return await state.session.ws_connect(
                    state.url + route, headers={"Authorization": "Bearer " + token}
                )

            async def refused(route, token):
                with pytest.raises(WSServerHandshakeError) as error:
                    ws = await connect(route, token)
                    await ws.close()
                assert error.value.status == 503

            try:
                sockets += [await connect("/v1/events"), await connect("/v1/audio")]
                await refused("/v1/events", "integration-test-token")
                await refused("/v1/audio", "integration-test-token")
                sockets += [
                    await connect("/v1/audio", "second-test-token"),
                    await connect("/v1/events", "second-test-token"),
                ]
                await refused("/v1/events", "third-test-token")
                await refused("/v1/audio", "third-test-token")
                await sockets[0].send_str("invalid")
                assert (await sockets[0].receive()).type == WSMsgType.CLOSE
                sockets.append(await connect("/v1/events"))
            finally:
                for ws in sockets:
                    await ws.close()

    asyncio.run(run())


def test_device_cap_reserves_slots_for_other_devices(service, monkeypatch):
    from assist_live_server import protocol
    from assist_live_server.auth import sign_event

    monkeypatch.setattr(protocol, "MAX_DEVICE_PER_DEVICE", 2, raising=False)

    async def run():
        async with service() as (_, state):
            state.registry.bind("device-b", "satellite-b", b"second-test-key")
            sockets = []

            async def connect(device, seq, key):
                ws = await state.session.ws_connect(state.url + "/v1/device")
                sockets.append(ws)
                event = {
                    "version": 1,
                    "device": device,
                    "boot": 1,
                    "seq": seq,
                    "device_ms": seq,
                    "stage": "mic_open",
                    "data": {},
                }
                await ws.send_str(json.dumps({**event, "tag": sign_event(event, key)}))
                return ws

            async def accepted(device, seq, key):
                state.store.changed.clear()
                ws = await connect(device, seq, key)
                async with asyncio.timeout(1):
                    await state.store.changed.wait()
                    await state.ingress.queue.join()
                assert not ws.closed

            try:
                for seq in (0, 1):
                    await accepted("satellite-a", seq, b"test-device-secret")
                ws = await connect("satellite-a", 2, b"test-device-secret")
                message = await asyncio.wait_for(ws.receive(), 0.5)
                assert message.type == WSMsgType.CLOSE and message.data == 1008
                await accepted("device-b", 0, b"second-test-key")
                await sockets[0].close()
                await accepted("satellite-a", 3, b"test-device-secret")
            finally:
                for ws in sockets:
                    await ws.close()

    asyncio.run(run())


def test_device_connection_cannot_change_its_authenticated_identity(service):
    from assist_live_server.auth import sign_event

    async def run():
        async with service() as (_, state):
            state.registry.bind("device-b", "satellite-b", b"second-test-key")
            async with state.session.ws_connect(state.url + "/v1/device") as ws:

                async def send(device, key):
                    event = {
                        "version": 1,
                        "device": device,
                        "boot": 1,
                        "seq": 0,
                        "device_ms": 0,
                        "stage": "mic_open",
                        "data": {},
                    }
                    await ws.send_json({**event, "tag": sign_event(event, key)})

                await send("satellite-a", b"test-device-secret")
                async with asyncio.timeout(1):
                    await state.store.changed.wait()
                    await state.ingress.queue.join()
                await send("device-b", b"second-test-key")
                message = await asyncio.wait_for(ws.receive(), 0.5)
                assert message.type == WSMsgType.CLOSE and message.data == 1008
                assert state.registry.replay["device-b"].high == -1

    asyncio.run(run())

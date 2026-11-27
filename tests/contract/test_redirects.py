import asyncio
import socket
from contextlib import aclosing
from dataclasses import asdict

import aiohttp
import pytest
from aiohttp import web
from aiohttp.abc import AbstractResolver, ResolveResult
from aiohttp.test_utils import TestServer
from assist_live_client import AssistLiveClient, TimingEvent, TurnOpen
from assist_live_client.protocol import ProtocolError


class LocalResolver(AbstractResolver):
    def __init__(self):
        self.hosts = []

    async def resolve(self, host, port=0, family=socket.AF_INET):
        self.hosts.append(host)
        if host != "service.invalid":
            raise OSError("unexpected host")
        return [
            ResolveResult(
                hostname=host,
                host="127.0.0.1",
                port=port,
                family=socket.AF_INET,
                proto=0,
                flags=socket.AI_NUMERICHOST,
            )
        ]

    async def close(self):
        pass


async def no_audio():
    pytest.fail("audio source started before the handshake completed")
    yield b""


@pytest.mark.parametrize("route", ["events", "audio"])
@pytest.mark.parametrize(
    "same_origin", [False, True], ids=["cross-origin", "same-origin"]
)
def test_websocket_refuses_redirect_before_following(turn_id, route, same_origin):
    asyncio.run(check_websocket_redirect(turn_id, route, same_origin))


async def check_websocket_redirect(turn_id, route, same_origin):
    paths = []
    location = "/other-path?value=example"

    async def handle(request):
        paths.append(request.path)
        if request.path == f"/v1/{route}":
            target = location if same_origin else "http://redirect.invalid" + location
            return web.Response(status=307, headers={"Location": target})
        return web.Response(status=404)

    app = web.Application()
    app.router.add_get("/{path:.*}", handle)
    resolver = LocalResolver()
    connector = aiohttp.TCPConnector(resolver=resolver)
    async with (
        TestServer(app, host="127.0.0.1") as server,
        aiohttp.ClientSession(connector=connector) as session,
        AssistLiveClient(
            f"http://service.invalid:{server.port}", "test-token", session=session
        ) as client,
    ):
        async with aclosing(
            client.events("satellite-a")
            if route == "events"
            else client.stt(turn_id, no_audio())
        ) as stream:
            with pytest.raises(ProtocolError) as error:
                await anext(stream)
        assert (paths, resolver.hosts, error.value.kind) == (
            [f"/v1/{route}"],
            ["service.invalid"],
            "refused",
        )
        assert str(error.value) == "redirect refused"
        assert not session.closed
        assert not connector.closed


@pytest.mark.parametrize("route", ["events", "audio"])
def test_websocket_on_closed_session_is_closed_error(turn_id, route):
    asyncio.run(check_closed_session(turn_id, route))


async def check_closed_session(turn_id, route):
    resolver = LocalResolver()
    session = aiohttp.ClientSession(connector=aiohttp.TCPConnector(resolver=resolver))
    await session.close()
    client = AssistLiveClient("http://service.invalid:9", "test-token", session=session)
    async with aclosing(
        client.events("satellite-a")
        if route == "events"
        else client.stt(turn_id, no_audio())
    ) as stream:
        with pytest.raises(ProtocolError) as error:
            await anext(stream)
    assert (error.value.kind, str(error.value), resolver.hosts) == (
        "closed",
        "session closed",
        [],
    )


def test_websocket_sends_supplied_default_headers(turn_id):
    asyncio.run(check_default_headers(turn_id))


async def check_default_headers(turn_id):
    seen = []

    async def events(request):
        seen.append(request.headers.get("User-Agent"))
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.receive_json()
        await ws.close()
        return ws

    app = web.Application()
    app.router.add_get("/v1/events", events)
    connector = aiohttp.TCPConnector(resolver=LocalResolver())
    async with (
        TestServer(app, host="127.0.0.1") as server,
        aiohttp.ClientSession(
            connector=connector, headers={"User-Agent": "caller-agent"}
        ) as session,
        AssistLiveClient(
            f"http://service.invalid:{server.port}", "test-token", session=session
        ) as client,
    ):
        async with aclosing(client.events("satellite-a")) as stream:
            with pytest.raises(ProtocolError) as error:
                await anext(stream)
            assert error.value.kind == "closed"
    assert seen == ["caller-agent"]


def test_direct_websocket_keeps_supplied_connector_open(turn_id):
    asyncio.run(check_direct_websocket(turn_id))


async def check_direct_websocket(turn_id):
    event = asdict(
        TimingEvent(
            turn_id, "satellite-a", "integration", "mic_open", 10, "measured", None
        )
    )

    async def events(request):
        assert request.headers["Authorization"] == "Bearer " + "test-token"
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        assert (await ws.receive_json())["type"] == "subscribe"
        await ws.send_json({"version": 1, "event_id": 1, "event": event})
        await ws.close()
        return ws

    app = web.Application()
    app.router.add_get("/v1/events", events)
    resolver = LocalResolver()
    connector = aiohttp.TCPConnector(resolver=resolver)
    async with (
        TestServer(app, host="127.0.0.1") as server,
        aiohttp.ClientSession(connector=connector) as session,
    ):
        async with AssistLiveClient(
            f"http://service.invalid:{server.port}", "test-token", session=session
        ) as client:
            async with aclosing(client.events("satellite-a")) as stream:
                assert (await anext(stream))["event"] == event
        assert resolver.hosts == ["service.invalid"]
        assert session.connector is connector
        assert not session.closed
        assert not connector.closed


@pytest.mark.parametrize("status", [307, 308])
def test_turn_open_refuses_redirect_without_resolving_target(turn_id, status):
    asyncio.run(check_turn_open_redirect(turn_id, status))


async def check_turn_open_redirect(turn_id, status):
    async def open_turn(request):
        assert (await request.json())["turn_id"] == turn_id
        return web.Response(
            status=status, headers={"Location": "http://redirect.invalid/other-path"}
        )

    app = web.Application()
    app.router.add_post("/v1/turns", open_turn)
    resolver = LocalResolver()
    async with (
        TestServer(app, host="127.0.0.1") as server,
        aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(resolver=resolver)
        ) as session,
        AssistLiveClient(
            f"http://service.invalid:{server.port}", "test-token", session=session
        ) as client,
    ):
        with pytest.raises(ProtocolError) as error:
            await client.open_turn(
                TurnOpen(turn_id, "satellite-a", "voice", None, None)
            )
        assert error.value.kind == "refused"
        assert resolver.hosts == ["service.invalid"]

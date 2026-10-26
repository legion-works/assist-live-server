import socket
import struct
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from aiohttp import web


@pytest.fixture
def first_word_clip():
    return [struct.pack("<h", value) * 6400 for value in (100, 200, 300)]


@pytest.fixture
def local_stt():
    from assist_live_server.providers.parakeet import ParakeetSession

    def decode(pcm):
        values = struct.unpack(f"<{len(pcm) // 2}h", pcm)
        return " ".join(
            {100: "turn", 200: "on", 300: "lights"}[v] for v in values[::6400]
        )

    return ParakeetSession(decode)


@pytest.fixture
def cloud_ws_server(monkeypatch):
    """Permit TCP only to the fake servers made by this fixture."""
    allowed = set()
    attempts = []
    refused = []
    connect = socket.socket.connect
    connect_ex = socket.socket.connect_ex
    resolve = socket.getaddrinfo

    def checked_resolve(host, port, *args, **kwargs):
        if (host, port) not in allowed:
            refused.append((host, port))
            raise AssertionError("external DNS resolution refused")
        return resolve(host, port, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", checked_resolve)

    def checked(original, sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            attempts.append(address)
            if address not in allowed:
                refused.append(address)
                raise AssertionError("external network connection refused")
        return original(sock, address)

    monkeypatch.setattr(
        socket.socket, "connect", lambda sock, addr: checked(connect, sock, addr)
    )
    monkeypatch.setattr(
        socket.socket, "connect_ex", lambda sock, addr: checked(connect_ex, sock, addr)
    )

    @asynccontextmanager
    async def serve(handler):
        app = web.Application()
        app.router.add_get("/{path:.*}", handler)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        address = listener.getsockname()
        allowed.add(address)
        site = web.SockSite(runner, listener)
        await site.start()
        try:
            yield SimpleNamespace(
                url=f"http://127.0.0.1:{address[1]}", attempts=attempts
            )
        finally:
            await runner.cleanup()
            allowed.remove(address)

    serve.refused = refused
    yield serve
    assert not refused, "adapter attempted a connection outside the fake server"

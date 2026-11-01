import asyncio
import json
import shutil
import socket
import ssl
import subprocess
from contextlib import asynccontextmanager
from dataclasses import asdict

import aiohttp
import pytest
from aiohttp import web
from aiohttp.abc import AbstractResolver
from aiohttp.test_utils import TestServer
from assist_live_client import AssistLiveClient, TimingEvent
from assist_live_client.protocol import ProtocolError, encode_control


class CachedNXDomain(AbstractResolver):
    def __init__(self, delay=0):
        self.calls = []
        self.delay = delay

    async def resolve(self, host, port=0, family=socket.AF_INET):
        self.calls.append((host, port, family))
        await asyncio.sleep(self.delay)
        # aiohttp's AsyncResolver translates aiodns NXDOMAIN to this OSError.
        raise OSError(None, "DNS lookup failed")

    async def close(self):
        pass


def system_address(monkeypatch, address="127.0.0.1", *, delay=0):
    loop = asyncio.get_running_loop()
    original = loop.getaddrinfo
    calls = []
    entered = asyncio.Event()
    stopped = asyncio.Event()

    async def resolve(host, port, **kwargs):
        calls.append((host, port, kwargs["family"]))
        entered.set()
        try:
            await asyncio.sleep(delay)
            return await original(address, port, **kwargs)
        finally:
            stopped.set()

    monkeypatch.setattr(loop, "getaddrinfo", resolve)
    return calls, entered, stopped


@asynccontextmanager
async def service(
    turn_id,
    *,
    address="127.0.0.1",
    ssl_context=None,
    delivery_delay=0,
    body_release=None,
):
    requests = []
    envelope = {
        "version": 1,
        "event_id": 1,
        "event": asdict(
            TimingEvent(
                turn_id, "satellite-a", "integration", "mic_open", 10, "measured", None
            )
        ),
    }

    async def handle(request):
        requests.append(
            (request.path, request.host, request.headers.get("Authorization"))
        )
        if request.path == "/health":
            if body_release is not None:
                response = web.StreamResponse()
                await response.prepare(request)
                await body_release.wait()
                try:
                    await response.write(b'{"version":1,"healthy":true}')
                except aiohttp.ClientConnectionResetError:
                    pass
                return response
            return web.json_response({"version": 1, "healthy": True})
        if request.path == "/v1/export":
            assert request.query == {"satellite": "satellite-a", "since_utc_ns": "0"}
            return web.Response(text=json.dumps(envelope) + "\n")
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        message = await ws.receive_json()
        if request.path == "/v1/events":
            assert message["type"] == "subscribe"
            await asyncio.sleep(delivery_delay)
            await ws.send_json(envelope)
        else:
            mode = message["mode"]
            await ws.send_json(
                encode_control("negotiated", turn_id, capabilities=[mode])
            )
            await ws.send_json(encode_control("end", turn_id))
        await ws.close()
        return ws

    app = web.Application()
    app.router.add_route("*", "/{path:.*}", handle)
    server = TestServer(app, host=address, scheme="https" if ssl_context else "http")
    await server.start_server(ssl=ssl_context)
    try:
        yield server, requests
    finally:
        await server.close()


@pytest.mark.parametrize(
    "entry", ["health", "capabilities", "events", "stt", "tts", "export"]
)
def test_cached_nxdomain_recovers_through_supplied_session(monkeypatch, turn_id, entry):
    async def run():
        async with service(turn_id) as (server, requests):
            calls, _, _ = system_address(monkeypatch)
            resolver = CachedNXDomain()
            connector = aiohttp.TCPConnector(resolver=resolver, family=socket.AF_INET)
            async with aiohttp.ClientSession(connector=connector) as session:
                client = AssistLiveClient(
                    f"http://service.invalid:{server.port}",
                    "test-token",
                    session=session,
                )
                try:
                    if entry == "health":
                        assert await client.health(), "DNS_RETRY_REQUIRED"
                    elif entry == "capabilities":
                        assert (await client.health_capabilities()).healthy
                    elif entry == "events":
                        stream = client.events("satellite-a")
                        try:
                            assert (await anext(stream))["event_id"] == 1
                        finally:
                            await stream.aclose()
                    elif entry == "export":
                        assert [
                            r["event_id"] async for r in client.export("satellite-a", 0)
                        ] == [1]
                    else:

                        async def source():
                            if False:
                                yield b"" if entry == "stt" else ""

                        stream = getattr(client, entry)(turn_id, source())
                        assert [item async for item in stream] == []
                except ProtocolError as error:
                    pytest.fail(f"DNS_RETRY_REQUIRED: {entry}: {error}")
                assert calls == [("service.invalid", server.port, socket.AF_INET)]
                assert resolver.calls == calls, "RETRY_MUST_BYPASS_SHARED_RESOLVER"
                assert len(requests) == 1
                assert requests[0][1] == f"service.invalid:{server.port}", (
                    "HOST_REQUIRED"
                )
                assert requests[0][2].split() == ["Bearer", "test-token"], (
                    "AUTH_REQUIRED"
                )
                await client.close()
                assert not session.closed, "SUPPLIED_SESSION_MUST_STAY_OPEN"

    asyncio.run(run())


def test_system_resolver_failure_is_the_usual_error(monkeypatch, turn_id):
    async def run():
        async def unavailable(*args, **kwargs):
            raise socket.gaierror(socket.EAI_NONAME, "not found")

        monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", unavailable)
        async with aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(resolver=CachedNXDomain())
        ) as session:
            client = AssistLiveClient(
                "http://service.invalid", "test-token", session=session
            )
            assert not await client.health()
            with pytest.raises(ProtocolError, match="request failed"):
                await client.health_capabilities()

    asyncio.run(run())


def test_connection_refused_does_not_retry(monkeypatch):
    async def run():
        calls, _, _ = system_address(monkeypatch)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]

        class AddressResolver(CachedNXDomain):
            async def resolve(self, host, port=0, family=socket.AF_INET):
                self.calls.append((host, port, family))
                return [
                    {
                        "hostname": host,
                        "host": "127.0.0.1",
                        "port": port,
                        "family": socket.AF_INET,
                        "proto": 0,
                        "flags": 0,
                    }
                ]

        resolver = AddressResolver()
        async with aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(resolver=resolver)
        ) as session:
            client = AssistLiveClient(
                f"http://service.invalid:{port}", "test-token", session=session
            )
            assert not await client.health()
            assert len(resolver.calls) == 1
            assert calls == [], "NON_DNS_MUST_NOT_RETRY"

    asyncio.run(run())


@pytest.mark.parametrize("entry", ["health", "events", "stt"])
@pytest.mark.parametrize("stage", ["resolve", "retry"])
def test_resolve_consumes_original_deadline(monkeypatch, turn_id, entry, stage):
    async def run():
        loop = asyncio.get_running_loop()
        real_time = loop.time
        clock_offset = 0
        calls, resolve_entered, stopped = system_address(
            monkeypatch, delay=3600 if stage == "resolve" else 0
        )
        retry_entered = asyncio.Event()
        retry_release = asyncio.Event()

        class ConsumingNXDomain(CachedNXDomain):
            async def resolve(self, host, port=0, family=socket.AF_INET):
                nonlocal clock_offset
                try:
                    return await super().resolve(host, port, family)
                finally:
                    clock_offset += 40

        monkeypatch.setattr(loop, "time", lambda: real_time() + clock_offset)
        async with aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(resolver=ConsumingNXDomain()),
            timeout=aiohttp.ClientTimeout(total=60),
        ) as session:
            method = "request" if entry == "health" else "ws_connect"
            original_connect = getattr(session, method)

            @asynccontextmanager
            async def connect(*args, **kwargs):
                if kwargs["url"].raw_host == "127.0.0.1":
                    retry_entered.set()
                    await retry_release.wait()
                async with original_connect(*args, **kwargs) as response:
                    yield response

            monkeypatch.setattr(session, method, connect)
            client = AssistLiveClient(
                "http://service.invalid", "test-token", session=session
            )
            stream = None
            if entry == "health":
                operation = client.health(timeout=60)
            else:

                async def source():
                    if False:
                        yield b""

                stream = (
                    client.events("satellite-a")
                    if entry == "events"
                    else client.stt(turn_id, source())
                )
                operation = anext(stream)
            task = asyncio.create_task(operation)
            try:
                entered = resolve_entered if stage == "resolve" else retry_entered
                await asyncio.wait_for(entered.wait(), 120)
                assert calls, "DNS_RESOLUTION_REQUIRED"
                assert not task.done(), "REMAINING_BUDGET_REQUIRED"
                clock_offset += 21
                try:
                    if entry == "health":
                        assert not await asyncio.wait_for(task, 5), (
                            "ORIGINAL_DEADLINE_REQUIRED"
                        )
                    else:
                        with pytest.raises(ProtocolError):
                            await asyncio.wait_for(task, 5)
                except TimeoutError:
                    pytest.fail("ORIGINAL_DEADLINE_REQUIRED")
                assert stopped.is_set(), "TIMED_OUT_RESOLVE_MUST_STOP"
                assert retry_entered.is_set() is (stage == "retry"), (
                    "NO_RETRY_AFTER_RESOLVE_DEADLINE"
                )
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                if stream is not None:
                    await stream.aclose()

    asyncio.run(run())


@pytest.mark.parametrize("entry", ["health", "events", "stt"])
def test_cancel_during_resolution_leaves_no_task(monkeypatch, turn_id, entry):
    async def run():
        calls, entered, stopped = system_address(monkeypatch, delay=10)
        async with aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(resolver=CachedNXDomain())
        ) as session:
            client = AssistLiveClient(
                "http://service.invalid", "test-token", session=session
            )
            before = asyncio.all_tasks()

            async def source():
                if False:
                    yield b""

            stream = None
            if entry == "health":
                operation = client.health(timeout=2)
            else:
                stream = (
                    client.events("satellite-a")
                    if entry == "events"
                    else client.stt(turn_id, source())
                )
                operation = anext(stream)
            task = asyncio.create_task(operation)
            try:
                await asyncio.wait_for(entered.wait(), 0.5)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                await asyncio.sleep(0)
                assert calls and stopped.is_set(), "CANCEL_MUST_STOP_RESOLVE"
                assert asyncio.all_tasks() <= before, "NO_ORPHAN_TASK"
                assert not connector_connections(session), "NO_ORPHAN_SOCKET"
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                if stream is not None:
                    await stream.aclose()

    asyncio.run(run())


def connector_connections(session):
    return session.connector._acquired or session.connector._conns


def test_retry_body_consumes_original_deadline(monkeypatch, turn_id):
    async def run():
        body_release = asyncio.Event()
        read_started = asyncio.Event()
        async with service(turn_id, body_release=body_release) as (server, requests):
            loop = asyncio.get_running_loop()
            real_time = loop.time
            clock_offset = 0
            calls, _, _ = system_address(monkeypatch)
            system_resolve = loop.getaddrinfo

            async def resolve(*args, **kwargs):
                nonlocal clock_offset
                addresses = await system_resolve(*args, **kwargs)
                clock_offset += 40
                return addresses

            monkeypatch.setattr(loop, "getaddrinfo", resolve)
            monkeypatch.setattr(loop, "time", lambda: real_time() + clock_offset)
            async with aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(resolver=CachedNXDomain())
            ) as session:
                original_request = session.request

                @asynccontextmanager
                async def request(*args, **kwargs):
                    async with original_request(*args, **kwargs) as response:
                        reader_type = type(response.content)
                        original_chunks = reader_type.iter_chunked

                        def chunks(reader, size):
                            if reader is response.content:
                                read_started.set()
                            return original_chunks(reader, size)

                        monkeypatch.setattr(reader_type, "iter_chunked", chunks)
                        yield response

                monkeypatch.setattr(session, "request", request)
                client = AssistLiveClient(
                    f"http://service.invalid:{server.port}",
                    "test-token",
                    session=session,
                )
                task = asyncio.create_task(client.health(timeout=60))
                try:
                    await asyncio.wait_for(read_started.wait(), 45)
                    assert calls and requests, "BODY_READ_MUST_START_AFTER_RETRY"
                    # Advance past the initial deadline only after the body read starts.
                    clock_offset += 21
                    try:
                        healthy = await asyncio.wait_for(task, 5)
                    except TimeoutError:
                        pytest.fail("BODY_DEADLINE_REQUIRED")
                    assert not healthy, "BODY_MUST_USE_REMAINING_TIME"
                finally:
                    body_release.set()
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())


def test_websocket_stream_outlives_connection_deadline(monkeypatch, turn_id):
    async def run():
        async with service(turn_id, delivery_delay=0.08) as (server, _):
            system_address(monkeypatch)
            async with aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(resolver=CachedNXDomain()),
                timeout=aiohttp.ClientTimeout(total=0.03),
            ) as session:
                client = AssistLiveClient(
                    f"http://service.invalid:{server.port}",
                    "test-token",
                    session=session,
                )
                stream = client.events("satellite-a")
                try:
                    assert (await anext(stream))["event_id"] == 1, (
                        "STREAM_DEADLINE_UNCHANGED"
                    )
                finally:
                    await stream.aclose()

    asyncio.run(run())


def test_first_system_address_uses_connector_family(monkeypatch, turn_id):
    async def run():
        async with service(turn_id) as (server, _):
            loop = asyncio.get_running_loop()
            original = loop.getaddrinfo
            calls = []

            async def resolve(host, port, **kwargs):
                calls.append(kwargs["family"])
                first = await original("127.0.0.1", port, **kwargs)
                last = await original("127.0.0.2", port, **kwargs)
                return first + last

            monkeypatch.setattr(loop, "getaddrinfo", resolve)
            async with aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(
                    resolver=CachedNXDomain(), family=socket.AF_UNSPEC
                )
            ) as session:
                client = AssistLiveClient(
                    f"http://service.invalid:{server.port}",
                    "test-token",
                    session=session,
                )
                assert await client.health(), "FIRST_SYSTEM_ADDRESS_REQUIRED"
                assert calls == [socket.AF_UNSPEC], "CONNECTOR_FAMILY_REQUIRED"

    asyncio.run(run())


@pytest.mark.parametrize("host", ["127.0.0.1", "[::1]", "service.invalid"])
def test_dns_retry_is_once_and_never_for_numeric_host(monkeypatch, host):
    async def run():
        calls, _, _ = system_address(monkeypatch)

        class FailingConnector(aiohttp.TCPConnector):
            async def _resolve_host(self, host, port, traces=None):
                raise OSError(None, "DNS lookup failed")

        async with aiohttp.ClientSession(connector=FailingConnector()) as session:
            client = AssistLiveClient(
                f"http://{host}:12345", "test-token", session=session
            )
            assert not await client.health(), "SECOND_FAILURE_MUST_USE_USUAL_ERROR"
            assert len(calls) == (1 if host == "service.invalid" else 0), (
                "ONE_RETRY_NON_NUMERIC_ONLY"
            )

    asyncio.run(run())


@pytest.mark.parametrize("numeric", [False, True])
def test_ipv6_family_and_literal_url(monkeypatch, turn_id, numeric):
    async def run():
        async with service(turn_id, address="::1") as (server, requests):
            calls, _, _ = system_address(monkeypatch, "::1")
            resolver = CachedNXDomain()
            async with aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(
                    resolver=resolver, family=socket.AF_INET6
                )
            ) as session:
                host = "[::1]" if numeric else "service.invalid"
                client = AssistLiveClient(
                    f"http://{host}:{server.port}", "test-token", session=session
                )
                assert await client.health(), "IPV6_RECOVERY_REQUIRED"
                assert requests[0][1] == f"{host}:{server.port}", "IPV6_HOST_REQUIRED"
                assert calls == (
                    []
                    if numeric
                    else [("service.invalid", server.port, socket.AF_INET6)]
                )
                assert resolver.calls == calls

    asyncio.run(run())


@pytest.mark.parametrize("entry", ["health", "events"])
@pytest.mark.parametrize("certificate_name", ["service.invalid", "wrong.invalid"])
@pytest.mark.parametrize("verify_tls", [True, False], ids=["verified", "ssl-false"])
def test_tls_checks_original_name_and_sends_sni(
    monkeypatch, tmp_path, turn_id, entry, certificate_name, verify_tls
):
    executable = shutil.which("openssl")
    assert executable, "OPENSSL_REQUIRED_FOR_TLS_TEST"
    key = tmp_path / "key.pem"
    certificate = tmp_path / "certificate.pem"
    subprocess.run(
        [
            executable,
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-subj",
            f"/CN={certificate_name}",
            "-addext",
            f"subjectAltName=DNS:{certificate_name}",
            "-keyout",
            str(key),
            "-out",
            str(certificate),
        ],
        check=True,
        capture_output=True,
        timeout=10,
    )
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(certificate, key)
    names = []
    server_context.set_servername_callback(
        lambda connection, name, context: names.append(name)
    )
    client_context = ssl.create_default_context(cafile=str(certificate))

    async def run():
        async with service(turn_id, ssl_context=server_context) as (server, requests):
            calls, _, _ = system_address(monkeypatch)
            async with aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(
                    resolver=CachedNXDomain(),
                    ssl=client_context if verify_tls else False,
                )
            ) as session:
                client = AssistLiveClient(
                    f"https://service.invalid:{server.port}",
                    "test-token",
                    session=session,
                )
                if entry == "health":
                    healthy = await client.health(timeout=1)
                else:
                    stream = client.events("satellite-a")
                    try:
                        try:
                            healthy = (await anext(stream))["event_id"] == 1
                        except ProtocolError:
                            healthy = False
                    finally:
                        await stream.aclose()
                assert calls, "TLS_DNS_RETRY_REQUIRED"
                assert names == ["service.invalid"], "TLS_SNI_REQUIRED"
                assert healthy is (
                    not verify_tls or certificate_name == "service.invalid"
                ), "TLS_VERIFICATION_MODE_REQUIRED"
                if healthy:
                    assert requests[0][1] == f"service.invalid:{server.port}", (
                        "TLS_HOST_REQUIRED"
                    )
                else:
                    assert requests == [], "WRONG_CERTIFICATE_MUST_NOT_SEND_AUTH"

    asyncio.run(run())

"""Fake wire protocol: Deepgram Live Audio and Close Stream, docs/providers/cloud.md."""

import asyncio
import logging
import traceback

import aiohttp
import pytest
from aiohttp import WSMsgType, web
from assist_live_server.providers.deepgram import DeepgramError, DeepgramProvider
from assist_live_server.providers.stt import STTClosedError

KEY = "cloud-test-credential"


def result(text, final=False):
    return {
        "type": "Results",
        "is_final": final,
        "speech_final": final,
        "channel": {"alternatives": [{"transcript": text}]},
    }


def test_options_on_each_connection_and_header_only_auth(cloud_ws_server):
    async def run():
        requests = []
        closed = asyncio.Queue()

        async def handler(request):
            requests.append(
                (dict(request.query), dict(request.headers), str(request.rel_url))
            )
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            await ws.close()
            await closed.put(None)
            return ws

        async with cloud_ws_server(handler) as fake:
            provider = DeepgramProvider(KEY, endpoint=fake.url + "/v1/listen")
            session = provider.session()
            try:
                await session.connect()
                await asyncio.wait_for(closed.get(), 2)
                await asyncio.wait_for(session._reader, 2)
                await session.connect()
                await asyncio.wait_for(closed.get(), 2)
                assert provider.destination == "cloud"
                assert len(requests) == 2
                for query, headers, url in requests:
                    assert query == {
                        "model": "nova-3",
                        "mip_opt_out": "true",
                        "encoding": "linear16",
                        "sample_rate": "16000",
                        "channels": "1",
                        "interim_results": "true",
                    }
                    assert headers["Authorization"] == f"Token {KEY}"
                    assert KEY not in url
                assert len(fake.attempts) == 2
            finally:
                await session.aclose()

    asyncio.run(run())


def test_partial_gate_first_audio_final_results_and_idempotent_finish(cloud_ws_server):
    async def run():
        seen = []
        ready = asyncio.Event()
        delivered = asyncio.Event()

        async def handler(request):
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            async for message in ws:
                if message.type == WSMsgType.BINARY:
                    seen.append(message.data)
                    await ws.send_json(result("turn"))
                    ready.set()
                elif message.json() == {"type": "CloseStream"}:
                    await ws.send_json(result("turn on", True))
                    await ws.send_json(result("lights", True))
                    await ws.send_json({"type": "Metadata", "duration": 0.1})
                    await ws.close()
            delivered.set()
            return ws

        async with cloud_ws_server(handler) as fake:
            session = DeepgramProvider(KEY, endpoint=fake.url + "/v1/listen").session()
            try:
                await session.push(b"\x01\x00")
                await asyncio.wait_for(ready.wait(), 2)
                assert await session.partial() == ""
                session.speech_started()
                assert await asyncio.wait_for(session.partial(), 2) == "turn"
                assert await session.finish() == "turn on lights"
                assert await session.finish() == "turn on lights"
                assert seen == [b"\x01\x00"]
                await asyncio.wait_for(delivered.wait(), 2)
                with pytest.raises(STTClosedError):
                    await session.push(b"\0\0")
            finally:
                await session.aclose()

    asyncio.run(run())


def test_cancel_closes_socket_and_rejects_further_input(cloud_ws_server):
    async def run():
        closed = asyncio.Event()

        async def handler(request):
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            async for _ in ws:
                pass
            closed.set()
            return ws

        async with cloud_ws_server(handler) as fake:
            session = DeepgramProvider(KEY, endpoint=fake.url).session()
            await session.push(b"\0\0")
            session.cancel()
            await session.aclose()
            await asyncio.wait_for(closed.wait(), 2)
            for operation in (
                session.partial(),
                session.finish(),
                session.push(b"\0\0"),
            ):
                with pytest.raises(STTClosedError):
                    await operation

    asyncio.run(run())


@pytest.mark.parametrize(
    "failure", ["handshake", "json", "error", "disconnect", "timeout"]
)
def test_remote_failures_redact_authentication(failure, cloud_ws_server, caplog):
    caplog.set_level(logging.DEBUG)

    async def run():
        async def handler(request):
            if failure == "handshake":
                return web.Response(status=401, reason=KEY, text=KEY)
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            if failure == "json":
                await ws.send_str(KEY)
            elif failure == "error":
                await ws.send_json({"type": "Error", "description": KEY})
            elif failure == "disconnect":
                await ws.close(message=KEY.encode())
            else:
                async for _ in ws:
                    pass
                return ws
            await ws.close()
            return ws

        async with cloud_ws_server(handler) as fake:
            session = DeepgramProvider(KEY, endpoint=fake.url, timeout=0.1).session()
            try:
                with pytest.raises(DeepgramError) as error:
                    await session.push(b"\0\0")
                    await session.finish()
                assert KEY not in "".join(traceback.format_exception(error.value))
                assert error.value.__context__ is None
                assert KEY not in repr(error.value)
            finally:
                await session.aclose()
        assert KEY not in caplog.text

    asyncio.run(run())


def test_default_server_does_not_construct_cloud_providers(
    monkeypatch, cloud_ws_server
):
    from assist_live_server.app import create_app
    from assist_live_server.auth import Registry
    from assist_live_server.providers import elevenlabs, stt, tts
    from assist_live_server.providers.parakeet import ParakeetProvider
    from assist_live_server.providers.piper import PiperProvider

    def refused(*args, **kwargs):
        pytest.fail("cloud provider constructed by default")

    monkeypatch.setenv("DEEPGRAM_API_KEY", KEY)
    monkeypatch.setenv("ELEVENLABS_API_KEY", KEY)
    monkeypatch.setattr(DeepgramProvider, "__init__", refused)
    monkeypatch.setattr(elevenlabs.ElevenLabsProvider, "__init__", refused)
    assert create_app(Registry()) is not None
    assert stt.provider_factory() == ParakeetProvider.load
    assert tts.provider_factory() == PiperProvider.load


@pytest.mark.parametrize(
    "operation", ["connect", "audio", "finish", "receive", "close"]
)
def test_transport_exceptions_do_not_escape(
    operation, cloud_ws_server, monkeypatch, caplog
):
    async def run():
        async def handler(request):
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            async for message in ws:
                if message.type == WSMsgType.TEXT:
                    await ws.send_json(result("done", True))
                    await ws.send_json({"type": "Metadata"})
                    await ws.close()
            return ws

        target = {
            "connect": (aiohttp.ClientSession, "ws_connect"),
            "audio": (aiohttp.ClientWebSocketResponse, "send_bytes"),
            "finish": (aiohttp.ClientWebSocketResponse, "send_json"),
            "receive": (aiohttp.ClientWebSocketResponse, "receive"),
            "close": (aiohttp.ClientWebSocketResponse, "close"),
        }[operation]
        original = getattr(*target)

        async def broken(*args, **kwargs):
            if operation == "close":
                await original(*args, **kwargs)
            raise RuntimeError(KEY)

        monkeypatch.setattr(*target, broken)
        async with cloud_ws_server(handler) as fake:
            session = DeepgramProvider(KEY, endpoint=fake.url, timeout=0.2).session()
            try:
                with pytest.raises(DeepgramError) as error:
                    await session.push(b"\0\0")
                    await session.finish()
                assert KEY not in "".join(traceback.format_exception(error.value))
                assert error.value.__context__ is None
            finally:
                await session.aclose()
        assert KEY not in caplog.text
        from assist_live_server.metrics import Metrics

        assert KEY not in Metrics().render({})

    asyncio.run(run())


def test_redirect_never_forwards_credentials(cloud_ws_server):
    async def run():
        leaked = []

        async def destination(request):
            leaked.append(dict(request.headers))
            return web.Response(status=401)

        async with cloud_ws_server(destination) as other:

            async def redirect(request):
                return web.Response(status=307, headers={"Location": other.url})

            async with cloud_ws_server(redirect) as fake:
                session = DeepgramProvider(KEY, endpoint=fake.url).session()
                try:
                    with pytest.raises(DeepgramError):
                        await session.connect()
                    assert leaked == []
                finally:
                    await session.aclose()

    asyncio.run(run())


def test_no_silent_audio_loss_on_disconnect(cloud_ws_server):
    async def run():
        async def handler(request):
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            await ws.receive_bytes()
            await ws.close()
            return ws

        async with cloud_ws_server(handler) as fake:
            session = DeepgramProvider(KEY, endpoint=fake.url).session()
            try:
                await session.push(b"\0\0")
                await session._reader
                with pytest.raises(DeepgramError):
                    await session.push(b"\0\0")
                assert len(fake.attempts) == 1
            finally:
                await session.aclose()

    asyncio.run(run())


@pytest.mark.parametrize("provider_name", ["deepgram", "elevenlabs"])
@pytest.mark.parametrize(
    "endpoint",
    [
        "http://service.example",
        "ws://service.example",
        "https://user:pass@service.example",
        "https://service.example?key=cloud-test-credential",
        "https://[cloud-test-credential]",
    ],
)
def test_invalid_endpoints_are_rejected_without_credential_context(
    provider_name, endpoint
):
    from assist_live_server.providers.elevenlabs import ElevenLabsProvider

    provider = DeepgramProvider if provider_name == "deepgram" else ElevenLabsProvider
    with pytest.raises(ValueError) as error:
        provider(KEY, endpoint=endpoint)
    assert KEY not in "".join(traceback.format_exception(error.value))
    assert error.value.__context__ is None


def test_network_guard_refuses_nonfake_destination(cloud_ws_server):
    import socket

    with socket.socket() as connection:
        with pytest.raises(AssertionError, match="external network connection refused"):
            connection.connect(("192.0.2.1", 443))
    assert cloud_ws_server.refused == [("192.0.2.1", 443)]
    cloud_ws_server.refused.clear()


def test_cancel_before_connect_needs_no_event_loop():
    session = DeepgramProvider(KEY).session()
    session.cancel()

    async def run():
        with pytest.raises(STTClosedError):
            await session.push(b"\0\0")
        await session.aclose()

    asyncio.run(run())


def test_input_validation_empty_finish_and_cancellation_cleanup(cloud_ws_server):
    async def run():
        provider = DeepgramProvider(KEY)
        session = provider.session()
        with pytest.raises(ValueError, match="int16"):
            await session.push(b"\0")
        with pytest.raises(ValueError, match="15 s"):
            await session.push(bytes(480002))
        assert await session.finish() == ""
        assert session._client is None

        entered = asyncio.Event()

        async def handler(request):
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            async for message in ws:
                if message.type == WSMsgType.TEXT:
                    entered.set()
            return ws

        async with cloud_ws_server(handler) as fake:
            session = DeepgramProvider(KEY, endpoint=fake.url).session()
            await session.push(b"\0\0")
            finishing = asyncio.create_task(session.finish())
            await asyncio.wait_for(entered.wait(), 2)
            finishing.cancel()
            with pytest.raises(asyncio.CancelledError):
                await finishing
            await session.aclose()
            assert session._reader.done()
            assert session._closing.done()
            assert session._client.closed
            with pytest.raises(STTClosedError):
                await session.push(b"\0\0")

    asyncio.run(run())

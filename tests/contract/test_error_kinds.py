import ast
import asyncio
import inspect
import json
from contextlib import aclosing, asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid7

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from assist_live_client import AssistLiveClient, TimingEvent, TurnOpen
from assist_live_client.protocol import ProtocolError, encode_audio, encode_control


def test_protocol_error_keeps_args_and_defaults_to_unclassified():
    error = ProtocolError("message", 7)
    assert error.args == ("message", 7)
    assert error.kind is None
    error = ProtocolError("message", kind="closed")
    assert str(error) == "message"
    assert error.kind == "closed"


def test_every_protocol_error_raise_has_an_explicit_valid_kind():
    root = Path(__file__).resolve().parents[2] / "client/assist_live_client"
    sites = []
    for path in root.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call):
                if (
                    isinstance(node.exc.func, ast.Name)
                    and node.exc.func.id == "ProtocolError"
                ):
                    kinds = [kw.value for kw in node.exc.keywords if kw.arg == "kind"]
                    assert len(kinds) == 1, (path.name, node.lineno)
                    assert isinstance(kinds[0], ast.Constant)
                    assert kinds[0].value in {
                        "unauthorized",
                        "refused",
                        "closed",
                        "timeout",
                        "malformed",
                    }
                    sites.append((path.name, node.lineno))
    assert len(sites) >= 36


def test_client_public_signatures_stay_unchanged():
    expected = json.loads(
        (Path(__file__).with_name("client_signatures.json")).read_text()
    )
    actual = {
        name: str(inspect.signature(value))
        for name, value in vars(AssistLiveClient).items()
        if callable(value)
        and (
            not name.startswith("_") or name in {"__init__", "__aenter__", "__aexit__"}
        )
    }
    assert len(actual) == 16
    assert actual == expected


class Wire:
    def __init__(self, messages=(), body=b"{}", status=200, error=None):
        self.messages = iter(messages)
        self.body = body
        self.status = status
        self.error = error
        self.content = self
        self.sent = asyncio.Event()

    async def iter_chunked(self, size):
        yield self.body

    async def send_json(self, value):
        if value["type"] == "end":
            self.sent.set()

    async def send_str(self, value):
        pass

    async def send_bytes(self, value):
        pass

    async def receive(self, **kwargs):
        if self.error:
            raise self.error
        try:
            return next(self.messages)
        except StopIteration:
            await self.sent.wait()
            return SimpleNamespace(type=aiohttp.WSMsgType.CLOSE, data=None)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.error:
            raise self.error
        try:
            return next(self.messages)
        except StopIteration:
            raise StopAsyncIteration from None


def connect_wire(monkeypatch, client, wire, error=None):
    @asynccontextmanager
    async def connect(*args, **kwargs):
        if error:
            raise error
        yield wire

    monkeypatch.setattr(client, "_connect", connect)


def text(value):
    return SimpleNamespace(type=aiohttp.WSMsgType.TEXT, data=json.dumps(value))


async def source(value=None, signal=None):
    if signal is not None:
        signal.set()
    if value is not None:
        yield value


@pytest.mark.parametrize(
    "operation,method,path",
    [
        ("bindings", "GET", "/v1/bindings"),
        ("health", "GET", "/health"),
        ("health_capabilities", "GET", "/health"),
        ("set_hints", "PUT", "/v1/satellites/satellite-a/hints"),
        ("open_turn", "POST", "/v1/turns"),
        ("event", "POST", "/v1/event"),
        ("turn", "GET", "/v1/turns/{turn_id}"),
        ("export", "GET", "/v1/export"),
        ("events", "WS", "/v1/events"),
        ("stt", "WS", "/v1/audio"),
        ("tts", "WS", "/v1/audio"),
        ("tts_stream", "WS", "/v1/audio"),
    ],
)
@pytest.mark.parametrize("status", [401, 403, 503])
@pytest.mark.parametrize("raise_for_status", [False, True])
def test_http_status_error_kind(
    turn_id, operation, method, path, status, raise_for_status
):
    async def run():
        seen = []

        async def refuse(request):
            seen.append(
                (
                    "WS"
                    if request.headers.get("Upgrade") == "websocket"
                    else request.method,
                    request.path,
                    request.headers.get("Authorization"),
                )
            )
            return web.Response(status=status)

        app = web.Application()
        app.router.add_route("*", "/{path:.*}", refuse)
        async with (
            TestServer(app) as server,
            aiohttp.ClientSession(raise_for_status=raise_for_status) as session,
            AssistLiveClient(
                str(server.make_url("/")), "test-token", session=session
            ) as client,
        ):
            args = {
                "set_hints": ("satellite-a", ["lamp"]),
                "open_turn": (TurnOpen(turn_id, "satellite-a", "voice", None, None),),
                "event": (
                    TimingEvent(
                        turn_id,
                        "satellite-a",
                        "integration",
                        "mic_open",
                        10,
                        "measured",
                        None,
                    ),
                ),
                "turn": (turn_id,),
                "export": ("satellite-a", 0),
                "events": ("satellite-a",),
                "stt": (turn_id, source(b"\0\0")),
                "tts": (turn_id, source("test")),
                "tts_stream": (turn_id, source("test")),
            }
            call = getattr(client, operation)(*args.get(operation, ()))
            if operation == "health" and status != 401:
                assert await call is False
            else:
                with pytest.raises(ProtocolError) as caught:
                    if method == "WS" or operation == "export":
                        async with aclosing(call):
                            await anext(call)
                    else:
                        await call
                if status == 401:
                    assert caught.value.kind == "unauthorized"
                elif method == "WS" or raise_for_status:
                    assert caught.value.kind == "closed"
                else:
                    assert caught.value.kind == "refused"
        assert seen == [
            (method, path.format(turn_id=turn_id), "Bearer " + "test-token")
        ]

    asyncio.run(run())


@pytest.mark.parametrize("status", [401, 503, None], ids=["401", "503", "timeout"])
def test_boolean_health_raises_only_for_unauthorized(status):
    async def run():
        release = asyncio.Event()

        async def health(_request):
            if status is None:
                await release.wait()
            return web.Response(status=status or 200)

        app = web.Application()
        app.router.add_get("/health", health)
        async with (
            TestServer(app) as server,
            AssistLiveClient(str(server.make_url("/")), "test-token") as client,
        ):
            try:
                if status == 401:
                    with pytest.raises(ProtocolError) as caught:
                        await client.health(timeout=1)
                    assert caught.value.kind == "unauthorized"
                else:
                    timeout = 0.05 if status is None else 1
                    assert (
                        await asyncio.wait_for(client.health(timeout=timeout), 2)
                        is False
                    )
            finally:
                release.set()

    asyncio.run(run())


@pytest.mark.parametrize(
    "case,message,kind",
    [
        ("request_size", "request too large", "malformed"),
        ("refused", "request refused", "refused"),
        ("response_size", "response too large", "malformed"),
        ("bindings", "invalid bindings", "malformed"),
        ("closed", "request failed", "closed"),
        ("timeout", "request failed", "timeout"),
        ("malformed", "request failed", "malformed"),
    ],
)
def test_request_error_kind(monkeypatch, case, message, kind):
    async def run():
        async with AssistLiveClient("http://service.invalid", "token") as client:
            wire = Wire(
                body=b"x" * 16385 if case == "response_size" else b"{}",
                status=403 if case == "refused" else 200,
            )
            errors = {
                "closed": aiohttp.ClientConnectionError(),
                "timeout": TimeoutError(),
                "malformed": ValueError(),
            }
            connect_wire(monkeypatch, client, wire, errors.get(case))
            with pytest.raises(ProtocolError, match=message) as caught:
                await client._request(
                    "GET",
                    "/v1/bindings" if case == "bindings" else "/health",
                    data="x" * 16385 if case == "request_size" else None,
                )
            assert caught.value.kind == kind

    asyncio.run(run())


@pytest.mark.parametrize(
    "value,message",
    [
        ({}, "invalid health response"),
        (
            {"version": 1, "healthy": True, "capabilities": []},
            "invalid health capabilities",
        ),
        (
            {"version": 1, "healthy": True, "capabilities": {"stt": 1}},
            "invalid health capability",
        ),
        ({"version": 1, "healthy": True, "local_only": 1}, "invalid health local only"),
        (
            {"version": 1, "healthy": True, "destinations": []},
            "invalid health destinations",
        ),
        (
            {"version": 1, "healthy": True, "capabilities": {"tts_voices": [None]}},
            "invalid health TTS voices",
        ),
    ],
)
def test_health_error_kind(monkeypatch, value, message):
    async def run():
        async with AssistLiveClient("http://service.invalid", "token") as client:
            connect_wire(monkeypatch, client, Wire(body=json.dumps(value).encode()))
            with pytest.raises(ProtocolError, match=message) as caught:
                await client.health_capabilities()
            assert caught.value.kind == "malformed"

    asyncio.run(run())


@pytest.mark.parametrize(
    "case,message,kind",
    [
        ("stopped", "event stream stopped", "closed"),
        ("envelope", "invalid event envelope", "malformed"),
        ("isolation", "event isolation or cursor mismatch", "malformed"),
        ("closed", "event stream failed", "closed"),
        ("timeout", "event stream failed", "timeout"),
        ("malformed", "event stream failed", "malformed"),
    ],
)
def test_events_error_kind(monkeypatch, case, message, kind):
    async def run():
        async with AssistLiveClient("http://service.invalid", "token") as client:
            envelope = {
                "version": 1,
                "event_id": 1,
                "event": {
                    "turn_id": None,
                    "satellite": "satellite-b",
                    "source": "integration",
                    "stage": "mic_open",
                    "t_utc_ns": 1,
                    "quality": "measured",
                    "uncertainty_ms": None,
                },
            }
            frame = (
                SimpleNamespace(type=aiohttp.WSMsgType.BINARY)
                if case == "stopped"
                else text(envelope if case == "isolation" else {})
            )
            errors = {
                "closed": aiohttp.ClientConnectionError(),
                "timeout": TimeoutError(),
                "malformed": ValueError(),
            }
            connect_wire(monkeypatch, client, Wire([frame]), errors.get(case))
            with pytest.raises(ProtocolError, match=message) as caught:
                await anext(client.events("satellite-a"))
            assert caught.value.kind == kind

    asyncio.run(run())


@pytest.mark.parametrize(
    "case,message,kind",
    [
        ("refused", "export refused", "refused"),
        ("time", "export time mismatch", "malformed"),
        ("size", "export line too large", "malformed"),
        ("incomplete", "incomplete export line", "malformed"),
        ("closed", "export failed", "closed"),
        ("timeout", "export failed", "timeout"),
        ("malformed", "export failed", "malformed"),
    ],
)
def test_export_error_kind(monkeypatch, case, message, kind):
    async def run():
        async with AssistLiveClient("http://service.invalid", "token") as client:
            envelope = {
                "version": 1,
                "event_id": 1,
                "event": {
                    "turn_id": None,
                    "satellite": "satellite-a",
                    "source": "integration",
                    "stage": "mic_open",
                    "t_utc_ns": 1,
                    "quality": "measured",
                    "uncertainty_ms": None,
                },
            }
            body = (
                (json.dumps(envelope) + "\n").encode()
                if case == "time"
                else b"x" * 16385
                if case == "size"
                else b"{"
            )
            errors = {
                "closed": aiohttp.ClientConnectionError(),
                "timeout": TimeoutError(),
                "malformed": ValueError(),
            }
            connect_wire(
                monkeypatch,
                client,
                Wire(body=body, status=403 if case == "refused" else 200),
                errors.get(case),
            )
            with pytest.raises(ProtocolError, match=message) as caught:
                await anext(client.export("satellite-a", 2))
            assert caught.value.kind == kind

    asyncio.run(run())


@pytest.mark.parametrize(
    "case,message,kind",
    [
        ("negotiation_close", "negotiation refused", "closed"),
        ("negotiation_refused", "negotiation refused", "refused"),
        ("input", "invalid audio input", "malformed"),
        ("sequence", "audio sequence mismatch", "malformed"),
        ("position", "TTS text position out of range", "malformed"),
        ("unknown_voice", "unknown_tts_voice", "refused"),
        ("refused", "audio refused", "refused"),
        ("stopped", "audio stream stopped", "closed"),
        ("timeout", "message deadline exceeded", "timeout"),
        ("closed", "audio stream failed", "closed"),
        ("malformed", "audio stream failed", "malformed"),
        ("type", "audio stream failed", "malformed"),
        ("key", "audio stream failed", "malformed"),
    ],
)
def test_audio_error_kind(monkeypatch, case, message, kind):
    async def run():
        turn_id = str(uuid7())
        async with AssistLiveClient("http://service.invalid", "token") as client:
            closed = SimpleNamespace(type=aiohttp.WSMsgType.CLOSE, data=None)
            frames = [text(encode_control("negotiated", turn_id, capabilities=["tts"]))]
            if case == "negotiation_close":
                frames = [closed]
            elif case == "negotiation_refused":
                frames = [
                    text(encode_control("negotiated", turn_id, capabilities=["stt"]))
                ]
            elif case == "sequence":
                frames += [
                    text(
                        encode_control(
                            "format",
                            turn_id,
                            codec="pcm_s16le",
                            rate=16000,
                            channels=1,
                            width=2,
                        )
                    ),
                    SimpleNamespace(
                        type=aiohttp.WSMsgType.BINARY,
                        data=encode_audio(turn_id, 1, b"\0\0"),
                    ),
                ]
            elif case == "position":
                frames += [
                    text(
                        encode_control(
                            "sentence_end", turn_id, sentence=0, text_end=100
                        )
                    )
                ]
            elif case in {"unknown_voice", "refused"}:
                frames += [
                    text(
                        encode_control(
                            "error",
                            turn_id,
                            code="unknown_tts_voice"
                            if case == "unknown_voice"
                            else "refused",
                        )
                    )
                ]
            errors = {
                "closed": aiohttp.ClientConnectionError(),
                "timeout": TimeoutError(),
                "malformed": ValueError(),
                "type": TypeError(),
                "key": KeyError(),
            }
            wire = Wire(frames)
            connect_wire(monkeypatch, client, wire, errors.get(case))
            stream = client._audio(
                turn_id,
                source(7 if case == "input" else None, wire.sent),
                "tts",
                text_positions=True,
            )
            with pytest.raises(ProtocolError, match=message) as caught:
                async for _ in stream:
                    pass
            assert caught.value.kind == kind

    asyncio.run(run())


@pytest.mark.parametrize(
    "method,values,message",
    [
        ("stt", [b"\0\0"], "unexpected PCM"),
        (
            "tts",
            [
                {
                    "type": "format",
                    "codec": "pcm_s16le",
                    "rate": 22050,
                    "channels": 1,
                    "width": 2,
                }
            ],
            "audio format needs the typed TTS stream",
        ),
        ("tts_stream", [b"\0\0"], "TTS format required before audio"),
        (
            "tts_stream",
            [
                {
                    "type": "format",
                    "codec": "pcm_s16le",
                    "rate": 16000,
                    "channels": 1,
                    "width": 2,
                }
            ]
            * 2,
            "duplicate TTS format",
        ),
        (
            "tts_stream",
            [{"type": "sentence_end", "sentence": 1}],
            "TTS sentence sequence mismatch",
        ),
        ("tts_stream", [], "incomplete TTS sentence stream"),
    ],
)
def test_typed_stream_error_kind(monkeypatch, method, values, message):
    async def audio(*args):
        for value in values:
            yield value

    async def run():
        async with AssistLiveClient("http://service.invalid", "token") as client:
            monkeypatch.setattr(client, "_audio", audio)
            with pytest.raises(ProtocolError, match=message) as caught:
                async for _ in getattr(client, method)(str(uuid7()), source()):
                    pass
            assert caught.value.kind == "malformed"

    asyncio.run(run())

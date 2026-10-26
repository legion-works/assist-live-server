"""Fake wire protocol: ElevenLabs stream-input AsyncAPI, docs/providers/cloud.md."""

import asyncio
import base64
import logging
import traceback

import aiohttp
import pytest
from aiohttp import web
from assist_live_client.models import TTSAudio, TTSFormat, TTSSentenceEnd
from assist_live_server.providers.elevenlabs import ElevenLabsError, ElevenLabsProvider

KEY = "cloud-test-credential"


async def parts(*values):
    for value in values:
        yield value


def test_normalized_alignment_has_no_request_sentence_positions(cloud_ws_server):
    async def run():
        async def handler(request):
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            async for message in ws:
                value = message.json()
                if value == {"text": ""}:
                    alignment = "Set three point five.  \U0001f642! Tail"
                    await ws.send_json(
                        {
                            "audio": base64.b64encode(b"\x01\0").decode(),
                            "normalizedAlignment": {
                                "chars": list(alignment),
                                "charStartTimesMs": list(range(len(alignment))),
                                "charDurationsMs": [1] * len(alignment),
                            },
                            "isFinal": True,
                        }
                    )
                    await ws.close()
            return ws

        async with cloud_ws_server(handler) as fake:
            provider = ElevenLabsProvider(KEY, endpoint=fake.url)
            items = [
                item
                async for item in provider.stream(
                    parts(*"Set 3.5.\n\U0001f642! Tail  "), "test-voice"
                )
            ]
            assert [
                item.text_end for item in items if isinstance(item, TTSSentenceEnd)
            ] == [None, None, None]

    asyncio.run(run())


@pytest.mark.parametrize(
    "request_text,frames,expected",
    [
        (
            "Jan. 5 is the date. ",
            [{"normalizedAlignment": "January 5 is the date. "}],
            [None],
        ),
        (
            "Jan. 5 is the date. ",
            [
                {
                    "alignment": "Jan. 5 is the date. ",
                    "normalizedAlignment": "January 5 is the date. ",
                }
            ],
            [4, 19],
        ),
        (
            "Dr. Test arrived. Tail.",
            [{"alignment": "Doctor Test arrived. Tail."}],
            [None, None],
        ),
        (
            "Set 3.5. Next.",
            [{"alignment": "Set three point five. Next."}],
            [None, None],
        ),
        ("One. Two. Three.", [{"alignment": "One. Changed. Three."}], [4, None, None]),
        (
            "One. Two. Three.",
            [
                {"alignment": "One. "},
                {"normalizedAlignment": "Two. "},
                {"alignment": "Three."},
            ],
            [4, None, None],
        ),
        (
            "One. Two. Three.",
            [
                {"alignment": "One. "},
                {"alignment": "Cha"},
                {"alignment": "nged. Three."},
            ],
            [4, None, None],
        ),
        (
            "One.  \nTwo.\t\U0001f642!",
            [{"alignment": " One. \tTw"}, {"alignment": "o. \U0001f642!"}],
            [4, 11, 14],
        ),
        ("One.", [{"alignment": "One. Two."}], [4, None]),
        ("One.", [{"normalizedAlignment": "One. Two."}], [None, None]),
        ("One. Two. Three.", [{"alignment": "One. Bad. Three."}], [4, None, None]),
        ("Tail  ", [{"alignment": "Tail"}], [6]),
    ],
    ids=[
        "month-expansion",
        "raw-precedes-normalized",
        "abbreviation-expansion",
        "decimal-expansion",
        "middle-mismatch",
        "normalized-disables-later",
        "split-middle-mismatch",
        "whitespace-and-split-word",
        "extra-raw-sentence",
        "extra-normalized-sentence",
        "same-length-middle-mismatch",
        "last-fragment-total",
    ],
)
def test_alignment_positions_require_matching_request_sentences(
    cloud_ws_server, request_text, frames, expected
):
    async def run():
        async def handler(request):
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            async for message in ws:
                if message.json() == {"text": ""}:
                    for frame in frames:
                        payload = {"audio": base64.b64encode(b"\x01\0").decode()}
                        for key, text in frame.items():
                            payload[key] = {
                                "chars": list(text),
                                "charStartTimesMs": [0] * len(text),
                                "charDurationsMs": [1] * len(text),
                            }
                        await ws.send_json(payload)
                    await ws.send_json({"isFinal": True})
                    await ws.close()
            return ws

        async with cloud_ws_server(handler) as fake:
            provider = ElevenLabsProvider(KEY, endpoint=fake.url)
            items = [
                item
                async for item in provider.stream(parts(*request_text), "test-voice")
            ]
            markers = [item for item in items if isinstance(item, TTSSentenceEnd)]
            assert [item.text_end for item in markers] == expected
            assert [item.sentence for item in markers] == list(range(len(expected)))
            assert b"".join(
                item.data for item in items if isinstance(item, TTSAudio)
            ) == b"\x01\0" * len(frames)

    asyncio.run(run())


def test_small_schedule_immediate_parts_first_byte_flush_and_alignment(cloud_ws_server):
    async def run():
        sent = []
        release = asyncio.Event()
        requests = []

        async def handler(request):
            requests.append((dict(request.query), dict(request.headers), request.path))
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            async for message in ws:
                value = message.json()
                sent.append(value)
                if value.get("text") == "First. ":
                    await ws.send_json(
                        {
                            "audio": base64.b64encode(b"\x01\0").decode(),
                            "alignment": {
                                "chars": list("First. "),
                                "charStartTimesMs": list(range(7)),
                                "charDurationsMs": [1] * 7,
                            },
                        }
                    )
                if value.get("flush") is True:
                    await ws.send_json({"audio": base64.b64encode(b"\x02\0").decode()})
                if value == {"text": ""}:
                    await ws.send_json({"isFinal": True})
                    await ws.close()
            return ws

        async def text():
            yield "First. "
            await release.wait()
            yield "Last"

        async with cloud_ws_server(handler) as fake:
            provider = ElevenLabsProvider(KEY, endpoint=fake.url)
            stream = provider.stream(text(), "test-voice")
            try:
                assert await anext(stream) == TTSFormat("pcm_s16le", 16000, 1, 2)
                assert await asyncio.wait_for(anext(stream), 2) == TTSAudio(b"\x01\0")
                assert not release.is_set()
                assert sent[0] == {
                    "text": " ",
                    "generation_config": {"chunk_length_schedule": [50]},
                }
                assert sent[1] == {"text": "First. "}
                assert await anext(stream) == TTSSentenceEnd(0, 6)
                release.set()
                rest = [item async for item in stream]
                assert rest == [TTSAudio(b"\x02\0")]
                assert sent[2:] == [
                    {"text": "Last"},
                    {"text": " ", "flush": True},
                    {"text": ""},
                ]
                query, headers, path = requests[0]
                assert query == {
                    "model_id": "eleven_flash_v2_5",
                    "output_format": "pcm_16000",
                    "enable_logging": "true",
                }
                assert path == "/v1/text-to-speech/test-voice/stream-input"
                assert headers["xi-api-key"] == KEY
                assert KEY not in str(sent)
                assert KEY not in str(query)
                assert provider.destination == "cloud"
            finally:
                await stream.aclose()

    asyncio.run(run())


def test_split_words_are_forwarded_without_inserted_spaces(cloud_ws_server):
    async def run():
        sent = []

        async def handler(request):
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            async for message in ws:
                value = message.json()
                sent.append(value)
                if value == {"text": ""}:
                    await ws.send_json({"isFinal": True})
                    await ws.close()
            return ws

        async with cloud_ws_server(handler) as fake:
            provider = ElevenLabsProvider(KEY, endpoint=fake.url)
            _ = [
                item
                async for item in provider.stream(parts("Hel", "lo. "), "test-voice")
            ]
        assert sent[1:3] == [{"text": "Hel"}, {"text": "lo. "}]

    asyncio.run(run())


@pytest.mark.parametrize(
    "operation", ["connect", "initialize", "text", "flush", "end", "receive", "close"]
)
def test_transport_exceptions_do_not_escape(
    operation, cloud_ws_server, monkeypatch, caplog
):
    async def run():
        async def handler(request):
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            async for message in ws:
                if message.json() == {"text": ""}:
                    await ws.send_json({"isFinal": True})
                    await ws.close()
            return ws

        target = {
            "connect": (aiohttp.ClientSession, "ws_connect"),
            "receive": (aiohttp.ClientWebSocketResponse, "receive"),
            "close": (aiohttp.ClientWebSocketResponse, "close"),
        }.get(operation, (aiohttp.ClientWebSocketResponse, "send_json"))
        original = getattr(*target)

        async def broken(*args, **kwargs):
            if operation == "close":
                await original(*args, **kwargs)
                raise RuntimeError(KEY)
            value = args[1] if len(args) > 1 else None
            if operation in {"connect", "receive"} or (
                operation == "initialize"
                and "generation_config" in value
                or operation == "text"
                and value == {"text": "Hello "}
                or operation == "flush"
                and value.get("flush")
                or operation == "end"
                and value == {"text": ""}
            ):
                raise RuntimeError(KEY)
            return await original(*args, **kwargs)

        monkeypatch.setattr(*target, broken)
        async with cloud_ws_server(handler) as fake:
            provider = ElevenLabsProvider(KEY, endpoint=fake.url, timeout=0.2)
            if operation == "close":
                assert [
                    chunk
                    async for chunk in provider.synthesize(
                        parts("Hello "), "test-voice"
                    )
                ] == []
            else:
                with pytest.raises(ElevenLabsError) as error:
                    _ = [
                        chunk
                        async for chunk in provider.synthesize(
                            parts("Hello "), "test-voice"
                        )
                    ]
                assert KEY not in "".join(traceback.format_exception(error.value))
                assert error.value.__context__ is None
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
                provider = ElevenLabsProvider(KEY, endpoint=fake.url)
                with pytest.raises(ElevenLabsError):
                    _ = [
                        chunk
                        async for chunk in provider.synthesize(
                            parts("Hello "), "test-voice"
                        )
                    ]
                assert leaked == []

    asyncio.run(run())


def test_cancel_stops_text_producer_and_closes_socket(cloud_ws_server, monkeypatch):
    clients = []
    close = aiohttp.ClientSession.close

    async def tracked_close(client):
        await close(client)
        clients.append(client)

    monkeypatch.setattr(aiohttp.ClientSession, "close", tracked_close)

    async def run():
        entered = asyncio.Event()
        closed = asyncio.Event()
        stopped = asyncio.Event()

        async def handler(request):
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            async for _ in ws:
                entered.set()
            closed.set()
            return ws

        async def text():
            try:
                yield "Hello "
                await asyncio.Event().wait()
            finally:
                stopped.set()

        async with cloud_ws_server(handler) as fake:
            stream = ElevenLabsProvider(KEY, endpoint=fake.url).stream(
                text(), "test-voice"
            )
            await anext(stream)
            pending = asyncio.create_task(anext(stream))
            await asyncio.wait_for(entered.wait(), 2)
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
            await asyncio.wait_for(closed.wait(), 2)
            assert stopped.is_set()
            assert clients and all(client.closed for client in clients)
            assert pending.done()
            assert not any(
                "ElevenLabsProvider.stream.<locals>.send"
                in task.get_coro().__qualname__
                for task in asyncio.all_tasks()
            )

    asyncio.run(run())


@pytest.mark.parametrize(
    "failure",
    ["handshake", "json", "error", "base64", "disconnect", "timeout", "input"],
)
def test_failures_redact_authentication(failure, cloud_ws_server, caplog):
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
                await ws.send_json({"error": KEY})
            elif failure == "base64":
                await ws.send_json({"audio": KEY})
            elif failure == "disconnect":
                await ws.close(message=KEY.encode())
            else:
                async for _ in ws:
                    pass
                return ws
            await ws.close()
            return ws

        async def text():
            if failure == "input":
                raise RuntimeError(KEY)
            yield "Hello "

        async with cloud_ws_server(handler) as fake:
            provider = ElevenLabsProvider(KEY, endpoint=fake.url, timeout=0.1)
            with pytest.raises(ElevenLabsError) as error:
                _ = [chunk async for chunk in provider.synthesize(text(), "test-voice")]
            assert KEY not in "".join(traceback.format_exception(error.value))
            assert KEY not in repr(error.value)
            assert error.value.__context__ is None
        assert KEY not in caplog.text

    asyncio.run(run())


def test_pcm_splitting_alignment_tail_and_empty_parts(cloud_ws_server):
    async def run():
        messages = []
        pcm = b"\x01\0" * 20000

        async def handler(request):
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            async for message in ws:
                value = message.json()
                messages.append(value)
                if value.get("flush"):
                    await ws.send_json(
                        {
                            "audio": base64.b64encode(pcm).decode(),
                            "normalizedAlignment": {
                                "chars": list("Tail"),
                                "charStartTimesMs": [0, 1, 2, 3],
                                "charDurationsMs": [1, 1, 1, 1],
                            },
                        }
                    )
                elif value == {"text": ""}:
                    await ws.send_json({"isFinal": True})
                    await ws.close()
            return ws

        async with cloud_ws_server(handler) as fake:
            provider = ElevenLabsProvider(KEY, endpoint=fake.url)
            items = [
                item
                async for item in provider.stream(parts("", "Tail", ""), "test-voice")
            ]
        assert items[0] == TTSFormat("pcm_s16le", 16000, 1, 2)
        chunks = [item.data for item in items if isinstance(item, TTSAudio)]
        assert [len(chunk) for chunk in chunks] == [32000, 8000]
        assert b"".join(chunks) == pcm
        assert items[-1] == TTSSentenceEnd(0, None)
        assert messages[1:] == [
            {"text": "Tail"},
            {"text": " ", "flush": True},
            {"text": ""},
        ]

    asyncio.run(run())


@pytest.mark.parametrize(
    "value",
    [
        [],
        {"audio": "AA=="},
        {
            "alignment": {
                "chars": ["AB"],
                "charStartTimesMs": [0],
                "charDurationsMs": [1],
            }
        },
        {"alignment": {"chars": ["A"], "charStartTimesMs": [], "charDurationsMs": [1]}},
    ],
)
def test_invalid_output_is_a_static_error(value, cloud_ws_server, caplog):
    async def run():
        async def handler(request):
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            await ws.send_json(value)
            await ws.close()
            return ws

        async with cloud_ws_server(handler) as fake:
            provider = ElevenLabsProvider(KEY, endpoint=fake.url)
            with pytest.raises(ElevenLabsError) as error:
                _ = [
                    item
                    async for item in provider.stream(parts("Hello "), "test-voice")
                ]
            assert KEY not in "".join(traceback.format_exception(error.value))
            assert error.value.__context__ is None
        assert KEY not in caplog.text

    asyncio.run(run())


def test_input_cleanup_after_final_response_is_awaited(cloud_ws_server):
    async def run():
        cleaning = asyncio.Event()
        release = asyncio.Event()

        class Text:
            sent = False

            def __aiter__(self):
                return self

            async def __anext__(self):
                if not self.sent:
                    self.sent = True
                    return "Hi"
                raise StopAsyncIteration

            async def aclose(self):
                cleaning.set()
                await release.wait()

        async def handler(request):
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            async for message in ws:
                if message.json().get("flush"):
                    await ws.send_json(
                        {
                            "audio": "AAA=",
                            "alignment": {
                                "chars": list("Hi"),
                                "charStartTimesMs": [0, 1],
                                "charDurationsMs": [1, 1],
                            },
                        }
                    )
                if message.json() == {"text": ""}:
                    await ws.send_json({"isFinal": True})
                    await ws.close()
            return ws

        async with cloud_ws_server(handler) as fake:
            provider = ElevenLabsProvider(KEY, endpoint=fake.url)
            stream = provider.stream(Text(), "test-voice")
            await anext(stream)
            assert await anext(stream) == TTSAudio(b"\0\0")
            assert await anext(stream) == TTSSentenceEnd(0, 2)
            assert cleaning.is_set()
            release.set()
            assert [item async for item in stream] == []

    asyncio.run(run())


@pytest.mark.parametrize(
    "mode", ["alignment_chunk", "alignment_total", "audio", "deadline"]
)
def test_continuous_remote_output_has_stream_limits(mode, cloud_ws_server, monkeypatch):
    from assist_live_server.providers import elevenlabs

    now = 0.0
    monkeypatch.setattr(elevenlabs, "monotonic", lambda: now, raising=False)

    async def run():
        closed = asyncio.Event()
        received_audio = 0
        markers = 0
        if mode == "alignment_chunk":
            chars, count = "a" * 4097, 1
        else:
            chars, count = "word. " * 500, 24

        async def handler(request):
            nonlocal now
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            try:
                async for message in ws:
                    if message.json() != {"text": ""}:
                        continue
                    if mode == "deadline":
                        now = 11.0
                        await ws.send_json({"audio": "AAA="})
                    elif mode == "audio":
                        chunk = base64.b64encode(bytes(128000)).decode()
                        for _ in range(31):
                            await ws.send_json({"audio": chunk})
                    else:
                        value = {
                            "alignment": {
                                "chars": list(chars),
                                "charStartTimesMs": [0] * len(chars),
                                "charDurationsMs": [1] * len(chars),
                            }
                        }
                        for _ in range(count):
                            await ws.send_json(value)
                    await ws.send_json({"isFinal": True})
                    await ws.receive()
                    break
            except ConnectionResetError:
                pass
            finally:
                closed.set()
            return ws

        async with cloud_ws_server(handler) as fake:
            stream = ElevenLabsProvider(KEY, endpoint=fake.url).stream(
                parts("Hello "), "test-voice"
            )
            try:
                with pytest.raises(ElevenLabsError) as error:
                    async for item in stream:
                        if isinstance(item, TTSAudio):
                            received_audio += len(item.data)
                        if isinstance(item, TTSSentenceEnd):
                            markers += 1
                assert received_audio <= 344000
                assert markers <= 22000
                assert KEY not in str(error.value)
                assert error.value.__context__ is None
                await asyncio.wait_for(closed.wait(), 2)
            finally:
                await stream.aclose()

    asyncio.run(run())


def test_audio_limit_accepts_exactly_two_minutes(cloud_ws_server):
    async def run():
        async def handler(request):
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            async for message in ws:
                if message.json() != {"text": ""}:
                    continue
                chunk = base64.b64encode(bytes(128000)).decode()
                for _ in range(30):
                    await ws.send_json({"audio": chunk})
                await ws.send_json({"isFinal": True})
                await ws.close()
                break
            return ws

        async with cloud_ws_server(handler) as fake:
            provider = ElevenLabsProvider(KEY, endpoint=fake.url)
            size = 0
            async for chunk in provider.synthesize(parts("a" * 880), "test-voice"):
                size += len(chunk)
            assert size == 3840000

    asyncio.run(run())


@pytest.mark.parametrize("rate", [16000, 22050, 24000])
@pytest.mark.parametrize("excess", [False, True])
def test_audio_allowance_follows_sent_text_and_declared_format(
    rate, excess, cloud_ws_server, monkeypatch
):
    from assist_live_server.providers import elevenlabs

    now = 0.0
    monkeypatch.setattr(elevenlabs, "monotonic", lambda: now)

    async def run():
        text = "a" * (8 if excess else 1200)
        seconds = 11 if excess else 160
        expected = seconds * rate * 2
        supplied = 0

        async def handler(request):
            nonlocal now
            assert request.query["output_format"] == f"pcm_{rate}"
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            async for message in ws:
                if message.json() != {"text": ""}:
                    continue
                now = 1.0 if excess else 159.0
                remaining = expected + (2 if excess else 0)
                try:
                    while remaining:
                        size = min(remaining, 128000)
                        await ws.send_json(
                            {"audio": base64.b64encode(bytes(size)).decode()}
                        )
                        remaining -= size
                    await ws.send_json({"isFinal": True})
                    await ws.close()
                except ConnectionResetError:
                    pass
                break
            return ws

        async with cloud_ws_server(handler) as fake:
            provider = ElevenLabsProvider(KEY, endpoint=fake.url)
            provider.audio_format = TTSFormat("pcm_s16le", rate, 1, 2)

            async def consume():
                nonlocal supplied
                async for item in provider.stream(parts(text), "test-voice"):
                    if isinstance(item, TTSAudio):
                        supplied += len(item.data)

            if excess:
                with pytest.raises(ElevenLabsError):
                    await consume()
                assert supplied <= expected
            else:
                await consume()
                assert supplied == expected

    asyncio.run(run())


@pytest.mark.parametrize("length,accepted", [(16384, True), (16385, False)])
def test_text_budget_has_server_character_ceiling(
    length, accepted, cloud_ws_server, monkeypatch
):
    from assist_live_server.providers import elevenlabs

    now = 0.0
    monkeypatch.setattr(elevenlabs, "monotonic", lambda: now)

    async def run():
        sent = []

        async def handler(request):
            nonlocal now
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            async for message in ws:
                value = message.json()
                if value.get("text", "").strip():
                    sent.append(value["text"])
                if value == {"text": ""}:
                    now = 2057.0
                    await ws.send_json({"isFinal": True})
                    await ws.close()
                    break
            return ws

        async with cloud_ws_server(handler) as fake:
            provider = ElevenLabsProvider(KEY, endpoint=fake.url)

            async def consume():
                async for _ in provider.stream(parts("a" * length), "test-voice"):
                    pass

            if accepted:
                await consume()
                assert sent == ["a" * 16384]
            else:
                with pytest.raises(ElevenLabsError):
                    await consume()
                assert sent == []

    asyncio.run(run())


@pytest.mark.parametrize(
    "setting,expected", [(None, "true"), (True, "true"), (False, "false")]
)
def test_logging_option_is_explicit_and_preserves_provider_default(
    setting, expected, cloud_ws_server
):
    async def run():
        seen = []

        async def handler(request):
            seen.append(request.query.get("enable_logging"))
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            async for message in ws:
                if message.json() == {"text": ""}:
                    await ws.send_json({"isFinal": True})
                    await ws.close()
                    break
            return ws

        async with cloud_ws_server(handler) as fake:
            options = {} if setting is None else {"enable_logging": setting}
            provider = ElevenLabsProvider(KEY, endpoint=fake.url, **options)
            async for _ in provider.stream(parts("Hello "), "test-voice"):
                pass
        assert seen == [expected]

    asyncio.run(run())

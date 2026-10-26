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
                assert await anext(stream) == TTSSentenceEnd(0)
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


def test_cancel_stops_text_producer_and_closes_socket(cloud_ws_server):
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
            await stream.aclose()
            await asyncio.wait_for(closed.wait(), 2)
            assert stopped.is_set()

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
        assert items[-1] == TTSSentenceEnd(0)
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
            assert await anext(stream) == TTSSentenceEnd(0)
            assert cleaning.is_set()
            release.set()
            assert [item async for item in stream] == []

    asyncio.run(run())

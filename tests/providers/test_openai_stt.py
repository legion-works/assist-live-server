"""OpenAI wire contract: assist-live docs/design.md, section 5.4."""

import asyncio
import base64
import importlib
import importlib.util
import json
import logging
import traceback
from pathlib import Path

import numpy as np
import pytest
from aiohttp import web

KEY = "cloud-test-credential"
EVENT = "conversation.item.input_audio_transcription."


def adapter():
    name = "assist_live_server.providers.openai_stt"
    assert importlib.util.find_spec(name) is not None, "OpenAI STT adapter missing"
    return importlib.import_module(name)


def event(kind, item="selected", text="turn on"):
    return {
        "type": EVENT + kind,
        "item_id": item,
        "content_index": 0,
        "delta" if kind == "delta" else "transcript": text,
    }


@pytest.mark.parametrize("delay", ["minimal", "low", "medium", "high", "xhigh"])
def test_session_configuration_audio_and_commit_only_at_finish(cloud_ws_server, delay):
    module = adapter()

    async def run():
        seen = []
        received = asyncio.Event()

        async def handler(request):
            assert dict(request.query) == {"intent": "transcription"}
            assert request.headers["Authorization"] == "Bearer " + KEY
            assert KEY not in str(request.rel_url)
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            async for message in ws:
                value = message.json()
                seen.append(value)
                if value["type"] == "input_audio_buffer.append":
                    received.set()
                if value["type"] == "input_audio_buffer.commit":
                    await ws.send_json(
                        {"type": "input_audio_buffer.committed", "item_id": "selected"}
                    )
                    await ws.send_json(event("completed"))
            return ws

        async with cloud_ws_server(handler) as fake:
            session = module.OpenAIProvider(
                KEY, endpoint=fake.url, delay=delay
            ).session()
            try:
                assert not getattr(session, "final_redecodes", False)
                await session.push(bytes(3200))
                await asyncio.wait_for(received.wait(), 2)
                assert [v["type"] for v in seen] == [
                    "session.update",
                    "input_audio_buffer.append",
                ], "commit sent before end decision"
                assert seen[0] == {
                    "type": "session.update",
                    "session": {
                        "type": "transcription",
                        "audio": {
                            "input": {
                                "format": {"type": "audio/pcm", "rate": 24000},
                                "transcription": {
                                    "model": "gpt-live-transcribe",
                                    "delay": delay,
                                },
                                "turn_detection": None,
                            }
                        },
                    },
                }
                pcm = base64.b64decode(seen[1]["audio"], validate=True)
                assert len(pcm) == 4800
                assert not any(pcm)
                assert await session.finish() == "turn on"
                assert await session.finish() == "turn on"
                assert sum(v["type"] == "input_audio_buffer.commit" for v in seen) == 1
            finally:
                await session.aclose()

    asyncio.run(asyncio.wait_for(run(), 5))


@pytest.mark.parametrize("completed_before_ack", [False, True])
def test_partial_per_item_and_final_matches_committed_id(
    cloud_ws_server, completed_before_ack
):
    module = adapter()

    async def run():
        async def handler(request):
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            await ws.receive_json()
            await ws.receive_json()
            for value in [
                event("delta", text="turn "),
                event("delta", "other", "unrelated"),
                event("delta", text="on"),
            ]:
                await ws.send_json(value)
            async for message in ws:
                if message.json()["type"] == "input_audio_buffer.commit":
                    await ws.send_json(event("completed", "other", "wrong"))
                    if completed_before_ack:
                        await ws.send_json(event("completed", text="Yeah."))
                    await ws.send_json(
                        {"type": "input_audio_buffer.committed", "item_id": "selected"}
                    )
                    if not completed_before_ack:
                        await ws.send_json(event("completed", text="Yeah."))
            return ws

        async with cloud_ws_server(handler) as fake:
            session = module.OpenAIProvider(KEY, endpoint=fake.url).session()
            try:
                await session.push(b"\0\0")
                await asyncio.wait_for(session._changed.wait(), 2)
                assert await session.partial() == ""
                session.speech_started()
                assert await session.partial() == "turn on", (
                    "deltas mixed across item IDs"
                )
                assert await session.finish() == "Yeah.", (
                    "final selected by arrival order, not committed item ID"
                )
            finally:
                await session.aclose()

    asyncio.run(asyncio.wait_for(run(), 5))


@pytest.mark.parametrize(
    "failure", ["failed", "error", "json", "disconnect", "timeout", "handshake"]
)
def test_failures_have_no_key_in_exception_or_logs(cloud_ws_server, caplog, failure):
    module = adapter()
    caplog.set_level(logging.DEBUG)

    async def run():
        async def handler(request):
            if failure == "handshake":
                return web.Response(status=401, reason=KEY, text=KEY)
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            async for message in ws:
                if message.json()["type"] != "input_audio_buffer.commit":
                    continue
                await ws.send_json(
                    {"type": "input_audio_buffer.committed", "item_id": "selected"}
                )
                if failure == "failed":
                    await ws.send_json({**event("failed"), "error": {"message": KEY}})
                elif failure == "error":
                    await ws.send_json({"type": "error", "error": {"message": KEY}})
                elif failure == "json":
                    await ws.send_str(KEY)
                elif failure == "disconnect":
                    await ws.close(message=KEY.encode())
                if failure != "timeout":
                    await ws.close()
            return ws

        async with cloud_ws_server(handler) as fake:
            session = module.OpenAIProvider(
                KEY, endpoint=fake.url, timeout=0.1
            ).session()
            try:
                with pytest.raises(module.OpenAIError) as error:
                    await session.push(b"\0\0")
                    await session.finish()
                assert KEY not in "".join(traceback.format_exception(error.value)), (
                    "credential leaked in exception"
                )
                assert error.value.__context__ is None
            finally:
                await session.aclose()
        assert all(KEY not in record.getMessage() for record in caplog.records), (
            "credential leaked in log"
        )

    asyncio.run(asyncio.wait_for(run(), 5))


@pytest.mark.parametrize("kind", ["messages", "segment", "transcript", "wire"])
def test_each_remote_limit_fails_closed(cloud_ws_server, kind):
    module = adapter()
    values = {
        "messages": [event("delta", text="")] * 129,
        "segment": [event("delta", text="é" * 2049)],
        "transcript": [event("delta", str(i), "\x01" * 700) for i in range(4)],
        "wire": [{"type": "ignored", "padding": "x" * 262144}],
    }[kind]

    async def run():
        async def handler(request):
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            await ws.receive_json()
            await ws.receive_json()
            try:
                for value in values:
                    await ws.send_json(value)
                await ws.receive()
            except ConnectionResetError:
                pass
            return ws

        async with cloud_ws_server(handler) as fake:
            session = module.OpenAIProvider(
                KEY, endpoint=fake.url, timeout=0.2
            ).session()
            try:
                await session.push(b"\0\0")
                done, _ = await asyncio.wait({session._reader}, timeout=2)
                assert session._reader in done, "remote limit did not close socket"
                with pytest.raises(module.OpenAIError, match="failed"):
                    await session.partial()
                assert session._ws.closed, "remote limit did not close socket"
            finally:
                await session.aclose()

    asyncio.run(asyncio.wait_for(run(), 5))


def test_redirect_refused_before_credential_forwarding(cloud_ws_server):
    module = adapter()

    async def run():
        forwarded = []

        async def target(request):
            forwarded.append(request.headers)
            return web.Response(status=401)

        async with cloud_ws_server(target) as other:

            async def redirect(request):
                return web.Response(status=307, headers={"Location": other.url})

            async with cloud_ws_server(redirect) as fake:
                session = module.OpenAIProvider(KEY, endpoint=fake.url).session()
                try:
                    with pytest.raises(module.OpenAIError):
                        await session.connect()
                    assert forwarded == []
                finally:
                    await session.aclose()

    asyncio.run(run())


@pytest.mark.parametrize(
    "endpoint",
    [
        "ws://service.example",
        "http://service.example",
        "https://user:pass@service.example",
        "https://service.example?key=" + KEY,
        "https://[" + KEY + "]",
    ],
)
def test_endpoint_validation_redacts_key(endpoint):
    module = adapter()
    with pytest.raises(ValueError) as error:
        module.OpenAIProvider(KEY, endpoint=endpoint)
    assert KEY not in "".join(traceback.format_exception(error.value))
    assert error.value.__context__ is None


@pytest.mark.parametrize(
    "options",
    [
        {"api_key": ""},
        {"api_key": "x\r\n"},
        {"timeout": 0},
        {"delay": "unknown"},
    ],
)
def test_invalid_provider_options(options):
    module = adapter()
    with pytest.raises(ValueError):
        module.OpenAIProvider(**{"api_key": KEY, **options})


def test_default_wire_rate_and_delay():
    module = adapter()
    provider = module.OpenAIProvider(KEY)
    assert provider._delay == "low"
    with pytest.raises(TypeError, match="sample_rate"):
        module.OpenAIProvider(KEY, sample_rate=16000)


def test_streaming_resample_sample_count_frequency_and_chunk_invariance(
    cloud_ws_server,
):
    module = adapter()
    source = (
        np.rint(np.sin(2 * np.pi * 1000 * np.arange(16000) / 16000) * 12000)
        .astype("<i2")
        .tobytes()
    )

    async def run():
        outputs = []

        async def handler(request):
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            chunks = []
            async for message in ws:
                value = message.json()
                if value["type"] == "input_audio_buffer.append":
                    chunks.append(base64.b64decode(value["audio"]))
                if value["type"] == "input_audio_buffer.commit":
                    outputs.append(b"".join(chunks))
                    await ws.send_json(
                        {"type": "input_audio_buffer.committed", "item_id": "selected"}
                    )
                    await ws.send_json(event("completed"))
            return ws

        async with cloud_ws_server(handler) as fake:
            for width in [len(source), 642, 2]:
                session = module.OpenAIProvider(KEY, endpoint=fake.url).session()
                try:
                    for start in range(0, len(source), width):
                        await session.push(source[start : start + width])
                    await session.finish()
                finally:
                    await session.aclose()
        for output in outputs:
            samples = np.frombuffer(output, dtype="<i2")
            assert len(samples) == 24000, "resample sample count changed"
            frequency = np.argmax(np.abs(np.fft.rfft(samples)))
            assert frequency == 1000, "resample changed sine frequency"
        assert outputs[0] == outputs[1] == outputs[2], (
            "resample depends on chunk boundaries"
        )

    asyncio.run(asyncio.wait_for(run(), 15))


def test_registry_local_only_and_environment_loader(monkeypatch):
    from assist_live_server.config import ProviderFactory, ServerConfig
    from assist_live_server.providers import stt

    assert "openai" in stt.provider_names(), "OpenAI missing from STT registry"
    assert "openai" not in stt.provider_names(mode="local_compatible")
    assert stt.provider_destination("openai") == "cloud"
    with pytest.raises(ValueError, match="local only"):
        ServerConfig(stt_provider="openai")
    module = adapter()
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    monkeypatch.setenv("OPENAI_TRANSCRIPTION_DELAY", "high")

    async def run():
        config = ServerConfig(
            stt_provider="openai", tts_provider=None, local_only=False
        )
        factory = await ProviderFactory.load(config)
        session = factory.stt("openai")
        assert isinstance(session, module.OpenAISession)
        assert session._provider._api_key == KEY
        assert session._provider._delay == "high"
        object.__setattr__(factory.config, "local_only", True)
        with pytest.raises(ValueError, match="local only"):
            factory.stt("openai")
        assert session._client is None

    asyncio.run(run())


def test_app_options_map_openai_key_delay_and_schema(tmp_path, monkeypatch, capsys):
    import yaml

    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "app_run", root / "assist-live-server/run.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    options = tmp_path / "options.json"
    options.write_text(
        json.dumps(
            {
                "stt_provider": "openai",
                "local_only": False,
                "openai_api_key": KEY,
                "openai_transcription_delay": "medium",
            }
        )
    )
    calls = []
    monkeypatch.setattr(module.os, "execvpe", lambda *args: calls.append(args))
    module.main(options)
    environment = calls[0][2]
    assert environment.get("OPENAI_API_KEY") == KEY, "OpenAI key app mapping missing"
    assert environment["OPENAI_TRANSCRIPTION_DELAY"] == "medium"
    assert environment["ASSIST_LIVE_STT_PROVIDER"] == "openai"
    assert environment["ASSIST_LIVE_LOCAL_ONLY"] == "false"
    assert KEY not in str(capsys.readouterr())
    config = yaml.safe_load((root / "assist-live-server/config.yaml").read_text())
    assert config["schema"]["openai_api_key"] == "password"
    assert "openai" in config["schema"]["stt_provider"].removeprefix(
        "list("
    ).removesuffix(")").split("|")
    assert config["options"]["openai_transcription_delay"] == "low"
    assert (
        config["schema"]["openai_transcription_delay"]
        == "list(minimal|low|medium|high|xhigh)"
    )


@pytest.mark.parametrize("failed", [False, True])
def test_gateway_commit_follows_end_decision_and_failure_uses_existing_path(
    cloud_ws_server, failed
):
    from assist_live_server.audio_gateway import AudioGateway

    from tests.stt_fakes import FirstBlockCommandDetector

    module = adapter()

    async def run():
        decisions = []

        async def record(stage, data):
            decisions.append(stage)

        async def handler(request):
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            async for message in ws:
                if message.json()["type"] == "input_audio_buffer.commit":
                    assert "end_decision" in decisions, (
                        "commit sent before detector end decision"
                    )
                    await ws.send_json(
                        {"type": "input_audio_buffer.committed", "item_id": "selected"}
                    )
                    await ws.send_json(
                        event("failed" if failed else "completed", text="Yeah.")
                    )
            return ws

        async with cloud_ws_server(handler) as fake:
            session = module.OpenAIProvider(KEY, endpoint=fake.url).session()
            gateway = AudioGateway(
                lambda _: session,
                provider="openai",
                destination="cloud",
                local_only=False,
                detector_factory=FirstBlockCommandDetector,
            )

            async def audio():
                yield bytes(3200)

            replies = [
                r
                async for r in gateway.stt(
                    "01900000-0000-7000-8000-000000000001", audio(), record=record
                )
            ]
            if failed:
                assert any(r.get("code") == "stt_failed" for r in replies)
                assert not any(r["type"] == "final" for r in replies)
            else:
                assert (
                    next(r["text"] for r in replies if r["type"] == "final") == "Yeah."
                )
                assert gateway.stt_final_replaced_total == 0
            await session.aclose()

    asyncio.run(asyncio.wait_for(run(), 5))


def test_audio_validation_empty_finish_and_cancel():
    from assist_live_server.providers.stt import AudioLimitError, STTClosedError

    module = adapter()
    cancelled = module.OpenAIProvider(KEY).session()
    cancelled.cancel()

    async def run():
        session = module.OpenAIProvider(KEY).session()
        with pytest.raises(ValueError, match="int16"):
            await session.push(b"\0")
        with pytest.raises(AudioLimitError, match="15 s"):
            await session.push(bytes(480002))
        assert await session.finish() == ""
        assert session._client is None
        for operation in (
            cancelled.push(b"\0\0"),
            cancelled.partial(),
            cancelled.finish(),
        ):
            with pytest.raises(STTClosedError):
                await operation
        await cancelled.aclose()

    asyncio.run(run())


@pytest.mark.parametrize(
    "operation", ["connect", "audio", "finish", "receive", "close"]
)
def test_transport_exceptions_redact_key(
    cloud_ws_server, monkeypatch, caplog, operation
):
    import aiohttp

    module = adapter()

    async def run():
        async def handler(request):
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            async for message in ws:
                if message.json()["type"] == "input_audio_buffer.commit":
                    await ws.send_json(
                        {"type": "input_audio_buffer.committed", "item_id": "selected"}
                    )
                    await ws.send_json(event("completed"))
            return ws

        target = {
            "connect": (aiohttp.ClientSession, "ws_connect"),
            "audio": (aiohttp.ClientWebSocketResponse, "send_json"),
            "finish": (aiohttp.ClientWebSocketResponse, "send_json"),
            "receive": (aiohttp.ClientWebSocketResponse, "receive"),
            "close": (aiohttp.ClientWebSocketResponse, "close"),
        }[operation]
        original = getattr(*target)

        async def broken(*args, **kwargs):
            if operation == "close":
                await original(*args, **kwargs)
            if operation in {"audio", "finish"}:
                kind = args[1]["type"]
                if kind != (
                    "input_audio_buffer.append"
                    if operation == "audio"
                    else "input_audio_buffer.commit"
                ):
                    return await original(*args, **kwargs)
            raise RuntimeError(KEY)

        monkeypatch.setattr(*target, broken)
        async with cloud_ws_server(handler) as fake:
            session = module.OpenAIProvider(
                KEY, endpoint=fake.url, timeout=0.1
            ).session()
            try:
                with pytest.raises(module.OpenAIError) as error:
                    await session.push(b"\0\0")
                    await session.finish()
                assert KEY not in "".join(traceback.format_exception(error.value)), (
                    "credential leaked in exception"
                )
                assert error.value.__context__ is None
            finally:
                await session.aclose()
        assert KEY not in caplog.text

    asyncio.run(asyncio.wait_for(run(), 5))

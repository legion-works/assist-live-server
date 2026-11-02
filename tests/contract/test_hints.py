import asyncio
import hashlib
import json
import logging
import os
import subprocess
import sys
import tarfile
from contextlib import asynccontextmanager
from io import BytesIO
from pathlib import Path

import pytest
from aiohttp import ClientSession, UnixConnector, web
from assist_live_client import AssistLiveClient
from assist_live_server.app import create_app
from assist_live_server.auth import Registry


@asynccontextmanager
async def service(tmp_path, store=None, providers=None):
    registry = Registry()
    for satellite in ("satellite-a", "satellite-b", "satellite-c"):
        registry.bind(satellite, satellite, b"synthetic-device-secret")
    registry.add_token("test-token", ["satellite-a", "satellite-b"])
    registry.add_token("other-token", ["satellite-c"])
    runner = web.AppRunner(
        create_app(registry, store, providers=providers), access_log=None
    )
    await runner.setup()
    path = tmp_path / "hints.sock"
    await web.UnixSite(runner, path).start()
    try:
        async with ClientSession(connector=UnixConnector(path=path)) as session:
            yield (
                AssistLiveClient(
                    "http://service.invalid", "test-token", session=session
                ),
                session,
            )
    finally:
        await runner.cleanup()


def digest(names):
    return hashlib.sha256(
        json.dumps(names, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


@pytest.mark.parametrize(
    "names",
    [
        None,
        {},
        "light",
        [None],
        [1],
        [True],
        [""],
        ["1-2"],
        ["Light"],
        [" light"],
        ["light "],
        ["light\n"],
        ["a" * 65],
        ["light", "light"],
        [f"light {i}" for i in range(501)],
        ["\ud800a"],
    ],
)
def test_invalid_hints_each_rule(tmp_path, names):
    async def run():
        async with service(tmp_path) as (client, session):
            async with session.put(
                client.url + "/v1/satellites/satellite-a/hints",
                headers=client._headers,
                json=names,
            ) as response:
                assert response.status == 400, "invalid_hints status"
                assert await response.json() == {"error": "invalid_hints"}

    asyncio.run(run())


@pytest.mark.parametrize(
    "raw",
    [b"[", b"NaN", b"[]" + b" " * 800000],
    ids=["truncated", "nonfinite", "oversized"],
)
def test_invalid_hints_json_and_body_bound(tmp_path, raw):
    async def run():
        async with service(tmp_path) as (client, session):
            async with session.put(
                client.url + "/v1/satellites/satellite-a/hints",
                headers=client._headers,
                data=raw,
            ) as response:
                assert response.status == 400
                assert await response.json() == {"error": "invalid_hints"}

    asyncio.run(run())


def test_hints_scope_limits_digest_restart_and_memory_only(tmp_path, store, caplog):
    caplog.set_level(logging.DEBUG)
    names = ["éclairage", "a", "z" * 64] + [
        f"synthetic lamp {i:03d}".ljust(64, "x") for i in range(497)
    ]

    async def run():
        async with service(tmp_path, store) as (client, session):
            initial = await client.health_capabilities()
            assert initial.stt_hints is False
            assert initial.stt_hints_by_satellite == {
                s: {"count": 0, "digest": digest([])}
                for s in ("satellite-a", "satellite-b")
            }
            await client.set_hints("satellite-a", names)
            status = await client.health_capabilities()
            assert status.stt_hints_by_satellite["satellite-a"] == {
                "count": 500,
                "digest": digest(names),
            }
            assert status.stt_hints_by_satellite["satellite-b"]["digest"] == digest([])
            for token, satellite, code in [
                ("test-token", "satellite-c", 403),
                ("bad-token", "satellite-a", 401),
            ]:
                async with session.put(
                    client.url + f"/v1/satellites/{satellite}/hints",
                    headers={"Authorization": "Bearer " + token},
                    json=names,
                ) as response:
                    assert response.status == code
            await client.set_hints("satellite-b", list(reversed(names)))
            assert (await client.health_capabilities()).stt_hints_by_satellite[
                "satellite-b"
            ]["digest"] == digest(list(reversed(names)))
            assert store.turns == {} and store.rows == [], (
                "hints must not write the store"
            )
        async with service(tmp_path, store) as (client, _):
            status = await client.health_capabilities()
            assert status.stt_hints_by_satellite["satellite-a"]["digest"] == digest([])
            assert digest(names) != digest([]), "restart must invalidate nonempty hints"
            await client.set_hints("satellite-a", [])
        assert not any(name in caplog.text for name in names if len(name) > 3), (
            "hint names must not appear in logs at any level"
        )

    asyncio.run(run())


@pytest.mark.parametrize("provider", ["parakeet", "deepgram", "openai"])
def test_health_hint_capability_matches_provider(tmp_path, provider):
    from assist_live_server.config import ProviderFactory, ServerConfig

    async def run():
        factory = ProviderFactory(
            ServerConfig(
                stt_provider=provider,
                tts_provider=None,
                local_only=False,
            )
        )
        async with service(tmp_path, providers=factory) as (client, _):
            status = await client.health_capabilities()
            assert status.stt_hints is (provider == "parakeet")

    asyncio.run(run())


def test_session_uses_its_satellite_snapshot_not_later_updates(
    tmp_path, store, monkeypatch
):
    from uuid import uuid7

    from assist_live_client import TurnOpen
    from assist_live_server.audio_gateway import AudioGateway
    from assist_live_server.config import ProviderFactory, ServerConfig
    from assist_live_server.providers.parakeet import ParakeetProvider, ParakeetSession
    from assist_live_server.turn.detector import TurnDetector

    seen = []
    provider = ParakeetProvider(None)

    def session(*, hints, bonus):
        seen.append((hints.names, bonus))
        return ParakeetSession(lambda _: "synthetic final")

    monkeypatch.setattr(provider, "session", session)
    factory = ProviderFactory(
        ServerConfig(tts_provider=None, stt_hint_bonus=2), provider
    )

    async def prepare(hints):
        pass

    monkeypatch.setattr(factory, "prepare_hints", prepare, raising=False)
    monkeypatch.setitem(
        AudioGateway.__init__.__kwdefaults__,
        "detector_factory",
        lambda: TurnDetector(vad_probability=lambda _: 0, turn_probability=lambda _: 0),
    )

    async def run():
        async with service(tmp_path, store, factory) as (client, _):
            await client.set_hints("satellite-a", ["first lamp"])
            await client.set_hints("satellite-b", ["other lamp"])
            for satellite in ("satellite-a", "satellite-b", "satellite-a"):
                turn = TurnOpen(str(uuid7()), satellite, "voice", None, None)
                await client.open_turn(turn)

                async def audio():
                    await client.set_hints("satellite-a", ["new lamp"])
                    yield b"\x01\0"

                replies = [r async for r in client.stt(turn.turn_id, audio())]
                assert any(r["type"] == "final" for r in replies)
            assert seen == [
                (("first lamp",), 2),
                (("other lamp",), 2),
                (("new lamp",), 2),
            ], "turn must keep its own satellite hint snapshot"

    asyncio.run(run())


def test_put_builds_once_and_immediate_turn_uses_previous_list(
    tmp_path, store, monkeypatch
):
    from types import SimpleNamespace
    from uuid import uuid7

    from assist_live_client import TurnOpen
    from assist_live_server.audio_gateway import AudioGateway
    from assist_live_server.config import ProviderFactory, ServerConfig
    from assist_live_server.providers.parakeet import ParakeetProvider, ParakeetSession
    from assist_live_server.turn.detector import TurnDetector

    async def run():
        entered, release, old_ready, new_ready = (asyncio.Event() for _ in range(4))
        builds, seen = [], []
        provider = ParakeetProvider(
            SimpleNamespace(
                asr=SimpleNamespace(
                    _vocab={0: " old lamp", 1: " new lamp", 2: "<blk>"},
                    _vocab_size=3,
                )
            )
        )
        original = getattr(provider, "prepare_hints", None)

        async def prepare(hints):
            builds.append(hints.names)
            if hints.names == ("new lamp",):
                entered.set()
                await release.wait()
            if original is not None:
                await original(hints)
            (new_ready if hints.names == ("new lamp",) else old_ready).set()

        def session(*, hints, bonus):
            seen.append(hints.names)
            assert hints._context is not None, "TURN_MUST_NOT_BUILD_GRAPH"
            return ParakeetSession(lambda _: "synthetic final")

        monkeypatch.setattr(provider, "prepare_hints", prepare, raising=False)
        monkeypatch.setattr(provider, "session", session)
        monkeypatch.setitem(
            AudioGateway.__init__.__kwdefaults__,
            "detector_factory",
            lambda: TurnDetector(
                vad_probability=lambda _: 0, turn_probability=lambda _: 0
            ),
        )
        factory = ProviderFactory(ServerConfig(tts_provider=None), provider)
        async with service(tmp_path, store, factory) as (client, _):
            await client.set_hints("satellite-a", ["old lamp"])
            try:
                await asyncio.wait_for(old_ready.wait(), 1)
            except TimeoutError:
                pytest.fail("PUT_MUST_BUILD_HINTS")
            await client.set_hints("satellite-a", ["new lamp"])
            await asyncio.wait_for(entered.wait(), 1)

            async def turn():
                value = TurnOpen(str(uuid7()), "satellite-a", "voice", None, None)
                await client.open_turn(value)

                async def audio():
                    yield b"\x01\0"

                return [r async for r in client.stt(value.turn_id, audio())]

            assert any(
                r["type"] == "final" for r in await asyncio.wait_for(turn(), 1)
            ), "PENDING_BUILD_MUST_NOT_DELAY_TURN"
            assert seen == [("old lamp",)], "PENDING_BUILD_USES_PREVIOUS_LIST"
            assert (await client.health_capabilities()).stt_hints_by_satellite[
                "satellite-a"
            ]["digest"] == digest(["new lamp"])
            release.set()
            await asyncio.wait_for(new_ready.wait(), 1)
            await turn()
            await turn()
            assert seen == [("old lamp",), ("new lamp",), ("new lamp",)]
            assert builds == [("old lamp",), ("new lamp",)], "PUT_BUILDS_ONCE"

    asyncio.run(run())


@pytest.mark.parametrize("sha", ["b3b1e16", "c3aadd3", "5ac300b", "7ab511c", "8f9c620"])
def test_archived_clients_decode_hint_health(tmp_path, store, sha):
    root = Path(__file__).resolve().parents[2]
    archive = subprocess.run(
        ["git", "archive", sha, "client"],
        cwd=root,
        capture_output=True,
        check=True,
        timeout=10,
    ).stdout
    with tarfile.open(fileobj=BytesIO(archive)) as source:
        source.extractall(tmp_path / "archived", filter="data")

    async def run():
        async with service(tmp_path, store) as (client, _):
            await client.set_hints("satellite-a", ["synthetic lamp"])
            code = """
import asyncio,sys
from aiohttp import ClientSession,UnixConnector
from assist_live_client import AssistLiveClient
async def run():
    async with ClientSession(connector=UnixConnector(path=sys.argv[1])) as s:
        c=AssistLiveClient('http://service.invalid','test-token',session=s)
        h=await c.health_capabilities(timeout=2)
        assert h.stt is False and h.tts is False, 'archived_hint_health_decode'
        assert await c.health() is h.healthy, 'archived_boolean_health'
asyncio.run(run())
"""
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                code,
                str(tmp_path / "hints.sock"),
                env={**os.environ, "PYTHONPATH": str(tmp_path / "archived/client")},
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(process.communicate(), 10)
            assert process.returncode == 0, stdout.decode() + stderr.decode()

    asyncio.run(run())


@pytest.mark.parametrize("provider", ["deepgram", "openai"])
def test_cloud_provider_never_receives_names(provider):
    from assist_live_server.config import ProviderFactory, ServerConfig

    class Cloud:
        def session(self, *args, **kwargs):
            assert not args and not kwargs, "cloud provider must not receive hints"
            return self

    cloud = Cloud()
    factory = ProviderFactory(
        ServerConfig(stt_provider=provider, tts_provider=None, local_only=False), cloud
    )
    assert factory.stt_with_hints(("synthetic lamp",)) is cloud


@pytest.mark.parametrize("bonus", [-0.1, 4.01, float("nan"), float("inf"), True, "1"])
def test_hint_bonus_bounds(bonus):
    from assist_live_server.config import ServerConfig

    with pytest.raises(ValueError, match="hint bonus"):
        ServerConfig(stt_hint_bonus=bonus)


@pytest.mark.parametrize(
    "summary",
    [
        [],
        {"satellite-a": None},
        {"satellite-a": {"count": True, "digest": "a" * 64}},
        {"satellite-a": {"count": 501, "digest": "a" * 64}},
        {"satellite-a": {"count": -1, "digest": "a" * 64}},
        {"satellite-a": {"count": 1, "digest": "A" * 64}},
        {"satellite-a": {"count": 1, "digest": "a" * 63}},
    ],
)
def test_client_refuses_invalid_hint_health(tmp_path, summary):
    from assist_live_client.protocol import ProtocolError

    async def health(request):
        return web.json_response(
            {
                "version": 1,
                "healthy": True,
                "stt_hints_by_satellite": summary,
            }
        )

    async def run():
        app = web.Application()
        app.router.add_get("/health", health)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        path = tmp_path / "health.sock"
        await web.UnixSite(runner, path).start()
        try:
            async with ClientSession(connector=UnixConnector(path=path)) as session:
                client = AssistLiveClient(
                    "http://service.invalid", "test-token", session=session
                )
                with pytest.raises(ProtocolError, match="invalid health STT hints"):
                    await client.health_capabilities()
        finally:
            await runner.cleanup()

    asyncio.run(run())


@pytest.mark.parametrize(
    "error",
    [KeyError("private name"), LookupError("private name"), OSError("private name")],
)
def test_hint_lookup_failure_is_counted_and_does_not_fail_audio(error, caplog):
    from assist_live_server.audio_gateway import AudioGateway
    from assist_live_server.providers.parakeet import ParakeetSession
    from assist_live_server.turn.detector import TurnDetector

    caplog.set_level(logging.DEBUG)
    calls, selected = [], []

    async def lookup(turn_id):
        calls.append(turn_id)
        raise error

    def hinted(hints):
        selected.append(hints)
        return ParakeetSession(lambda _: "synthetic final")

    async def run():
        gateway = AudioGateway(
            lambda _: pytest.fail("hint factory not selected"),
            hinted_factory=hinted,
            hint_snapshot=lookup,
            detector_factory=lambda: TurnDetector(
                vad_probability=lambda _: 0, turn_probability=lambda _: 0
            ),
        )

        async def audio():
            yield b"\x01\0"

        turn_id = "01900000-0000-7000-8000-000000000001"
        replies = [r async for r in gateway.stt(turn_id, audio())]
        assert any(r["type"] == "final" for r in replies), (
            "HINT_LOOKUP_MUST_NOT_FAIL_TURN"
        )
        assert calls == [turn_id] and selected == [()]
        assert gateway.stt_hints_lookup_misses_total == 1
        messages = [
            r.getMessage() for r in caplog.records if r.name.endswith("audio_gateway")
        ]
        assert messages == [type(error).__name__], "LOOKUP_LOG_ERROR_CLASS_ONLY"
        assert "private name" not in caplog.text

    asyncio.run(run())


def test_gateway_stt_signature_keeps_original_parameters():
    import inspect

    from assist_live_server.audio_gateway import AudioGateway

    assert list(inspect.signature(AudioGateway.stt).parameters) == [
        "self",
        "turn_id",
        "audio",
        "record",
    ], "GATEWAY_STT_SIGNATURE_UNCHANGED"


@pytest.mark.parametrize("shutdown", [False, True])
def test_pending_hint_builds_are_joined_and_superseded_lists_do_not_publish(
    tmp_path, store, monkeypatch, shutdown
):
    from uuid import uuid7

    from assist_live_client import TurnOpen
    from assist_live_server.audio_gateway import AudioGateway
    from assist_live_server.config import ProviderFactory, ServerConfig
    from assist_live_server.providers.parakeet import ParakeetProvider, ParakeetSession
    from assist_live_server.turn.detector import TurnDetector

    async def run():
        entered, canceled, release, newest_ready = (asyncio.Event() for _ in range(4))
        seen = []
        provider = ParakeetProvider(None)

        async def prepare(hints):
            if hints.names == ("slow lamp",):
                entered.set()
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    canceled.set()
                    if shutdown:
                        raise
                    await release.wait()
            else:
                newest_ready.set()

        def session(*, hints, bonus):
            seen.append(hints.names)
            return ParakeetSession(lambda _: "synthetic final")

        monkeypatch.setattr(provider, "prepare_hints", prepare)
        monkeypatch.setattr(provider, "session", session)
        monkeypatch.setitem(
            AudioGateway.__init__.__kwdefaults__,
            "detector_factory",
            lambda: TurnDetector(
                vad_probability=lambda _: 0, turn_probability=lambda _: 0
            ),
        )
        factory = ProviderFactory(ServerConfig(tts_provider=None), provider)
        async with service(tmp_path, store, factory) as (client, _):
            await client.set_hints("satellite-a", ["slow lamp"])
            await asyncio.wait_for(entered.wait(), 1)
            if not shutdown:
                await client.set_hints("satellite-a", ["newest lamp"])
                await asyncio.wait_for(canceled.wait(), 1)
                release.set()
                await asyncio.wait_for(newest_ready.wait(), 1)
                value = TurnOpen(str(uuid7()), "satellite-a", "voice", None, None)
                await client.open_turn(value)

                async def audio():
                    yield b"\x01\0"

                _ = [r async for r in client.stt(value.turn_id, audio())]
                assert seen == [("newest lamp",)], "LATEST_HINT_GENERATION_ONLY"
        assert canceled.is_set(), "SHUTDOWN_MUST_CANCEL_AND_JOIN_HINT_BUILDS"

    asyncio.run(asyncio.wait_for(run(), 3))


def test_missing_ready_list_uses_empty_hints_without_lookup_miss(tmp_path, monkeypatch):
    from uuid import uuid7

    from assist_live_client import TurnOpen
    from assist_live_server.audio_gateway import AudioGateway
    from assist_live_server.config import ProviderFactory, ServerConfig
    from assist_live_server.providers.parakeet import ParakeetProvider, ParakeetSession
    from assist_live_server.store import SQLiteStore
    from assist_live_server.turn.detector import TurnDetector

    selected = []

    class Provider(ParakeetProvider):
        def session(self, *, hints, bonus):
            selected.append(hints.names if hasattr(hints, "names") else hints)
            return ParakeetSession(lambda _: "synthetic final")

    monkeypatch.setitem(
        AudioGateway.__init__.__kwdefaults__,
        "detector_factory",
        lambda: TurnDetector(vad_probability=lambda _: 0, turn_probability=lambda _: 0),
    )

    async def run():
        store = SQLiteStore(tmp_path / "turns.sqlite3")
        try:
            factory = ProviderFactory(ServerConfig(tts_provider=None), Provider(None))
            async with service(tmp_path, store, factory) as (client, session):
                value = TurnOpen(str(uuid7()), "satellite-a", "voice", None, None)
                await client.open_turn(value)

                async def audio():
                    yield b"\x01\0"

                replies = [r async for r in client.stt(value.turn_id, audio())]
                assert any(r["type"] == "final" for r in replies)
                assert selected == [()], "NO_LIST_USES_EMPTY_HINTS"
                async with session.get(
                    client.url + "/metrics", headers=client._headers
                ) as response:
                    assert (
                        "assist_live_stt_hints_lookup_misses_total 0\n"
                        in await response.text()
                    ), "NO_LIST_IS_NOT_LOOKUP_FAILURE"
        finally:
            store.close()

    asyncio.run(run())


def test_two_satellites_share_prepared_tree_until_last_list_change(
    tmp_path, monkeypatch
):
    import gc
    import weakref
    from types import SimpleNamespace

    from assist_live_server.config import ProviderFactory, ServerConfig
    from assist_live_server.providers import name_context
    from assist_live_server.providers.parakeet import ParakeetProvider

    builds, trees = [], []
    tokenize = name_context.tokenize_names

    def counted(names, vocabulary):
        builds.append(names)
        return tokenize(names, vocabulary)

    monkeypatch.setattr(name_context, "tokenize_names", counted)

    async def run():
        provider = ParakeetProvider(
            SimpleNamespace(
                asr=SimpleNamespace(
                    _vocab={0: " command", 1: " filler", 2: "<blk>"}, _vocab_size=3
                )
            )
        )
        prepared = asyncio.Queue()
        original = provider.prepare_hints

        async def prepare(hints):
            await original(hints)
            trees.append(weakref.ref(hints._context))
            prepared.put_nowait(None)

        monkeypatch.setattr(provider, "prepare_hints", prepare)
        factory = ProviderFactory(ServerConfig(tts_provider=None), provider)
        async with service(tmp_path, providers=factory) as (client, _):
            for satellite in ("satellite-a", "satellite-b"):
                await client.set_hints(satellite, ["command"])
                await asyncio.wait_for(prepared.get(), 1)
            assert builds == [("command",)], "SATELLITES_SHARE_ONE_BUILD"
            assert trees[0]() is trees[1](), "SATELLITES_SHARE_ONE_TREE"
            await client.set_hints("satellite-a", ["filler"])
            await asyncio.wait_for(prepared.get(), 1)
            gc.collect()
            assert trees[1]() is not None, "OTHER_SATELLITE_KEEPS_PREPARED_TREE"
            assert trees[2]() is not trees[1](), (
                "LIST_CHANGE_DOES_NOT_MUTATE_OTHER_TREE"
            )
            health = await client.health_capabilities()
            assert health.stt_hints_by_satellite["satellite-b"]["digest"] == digest(
                ["command"]
            ), "SHARING_KEEPS_LOWER_CASE_WIRE_DIGEST"
            await client.set_hints("satellite-b", ["filler"])
            await asyncio.wait_for(prepared.get(), 1)
            gc.collect()
            assert trees[1]() is None, "LAST_SATELLITE_RELEASES_UNUSED_TREE"
            assert builds == [("command",), ("filler",)], "SHARED_UPDATE_BUILDS_ONCE"

    asyncio.run(run())

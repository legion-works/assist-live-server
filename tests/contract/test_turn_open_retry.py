import asyncio
import json
import os
import subprocess
import sys
import tarfile
from contextlib import suppress
from dataclasses import asdict, replace
from io import BytesIO
from pathlib import Path
from uuid import uuid7

import pytest
from aiohttp import ClientSession, UnixConnector
from assist_live_client import TimingEvent, TurnOpen
from assist_live_client.protocol import ProtocolError

NOW = 100_000_000_000
PRIVATE_KEYS = {"received_parent_turn_id", "requested_opened_utc_ns"}


@pytest.fixture
def service():
    from tests.contract.test_event_store import service

    return service


async def post(client, session, turn):
    async with session.post(
        client.url + "/v1/turns", headers=client._headers, json=asdict(turn)
    ) as response:
        body = await response.text()
        return response.status, (
            json.loads(body) if response.content_type == "application/json" else body
        )


async def snapshot(store):
    return await store._call(
        lambda: (
            tuple(store.connection.iterdump()),
            store.connection.total_changes,
            dict(store.removed_parents),
            dict(store.removed_conversation_ids),
            dict(store.invalid_rows),
            dict(store.wrong_owners),
            dict(store.turn_limits),
        )
    )


@pytest.mark.parametrize(
    "values",
    [
        {},
        {"satellite": "satellite-b"},
        {"turn_type": "tts_only"},
        {"parent_turn_id": str(uuid7())},
        {"conversation_id": "conversation-a"},
        {"record_transcripts": True},
        {"opened_utc_ns": NOW - 1},
    ],
    ids=[
        "defaults",
        "satellite",
        "type",
        "parent",
        "conversation",
        "permission",
        "time",
    ],
)
def test_identical_open_returns_original_response_without_writes(
    tmp_path, values, service
):
    async def run():
        async with service(tmp_path, satellites=("satellite-a", "satellite-b")) as (
            client,
            session,
            store,
            _,
            _,
        ):
            store.now_ns = lambda: NOW
            turn = replace(
                TurnOpen(str(uuid7()), "satellite-a", "voice", None, None), **values
            )
            first = await post(client, session, turn)
            assert first == (200, {"version": 1}), "initial_open_response"
            before = await snapshot(store)
            second = await post(client, session, turn)
            assert second == first, "identical_retry_response"
            assert await snapshot(store) == before, "identical_retry_store_unchanged"

    asyncio.run(run())


@pytest.mark.parametrize(
    "field,value",
    [
        ("satellite", "satellite-b"),
        ("turn_type", "tts_only"),
        ("parent_turn_id", str(uuid7())),
        ("conversation_id", "conversation-b"),
        ("record_transcripts", True),
        ("opened_utc_ns", NOW - 1),
    ],
)
def test_different_open_field_is_named_conflict(tmp_path, field, value, service):
    async def run():
        async with service(tmp_path, satellites=("satellite-a", "satellite-b")) as (
            client,
            session,
            store,
            _,
            _,
        ):
            store.now_ns = lambda: NOW
            turn = TurnOpen(
                str(uuid7()), "satellite-a", "voice", None, None, False, NOW
            )
            assert await post(client, session, turn) == (200, {"version": 1})
            before = await snapshot(store)
            assert await post(client, session, replace(turn, **{field: value})) == (
                409,
                {"error": "turn_conflict"},
            ), f"conflict_compares_{field}"
            assert await snapshot(store) == before, "conflict_store_unchanged"
            with pytest.raises(ProtocolError) as error:
                await client.open_turn(replace(turn, **{field: value}))
            assert error.value.kind == "refused", "client_conflict_kind"

    asyncio.run(run())


@pytest.mark.parametrize("different", [False, True], ids=["identical", "conflict"])
def test_simultaneous_opens_compare_in_writer(
    tmp_path, monkeypatch, different, service
):
    async def run():
        async with service(tmp_path) as (client, session, store, _, _):
            store.now_ns = lambda: NOW
            original = store.open_turn
            release = asyncio.Event()
            entered = 0

            async def simultaneous(turn):
                nonlocal entered
                entered += 1
                if entered == 2:
                    release.set()
                await release.wait()
                await original(turn)

            monkeypatch.setattr(store, "open_turn", simultaneous)
            turn = TurnOpen(
                str(uuid7()), "satellite-a", "voice", None, None, False, NOW
            )
            other = replace(turn, record_transcripts=True) if different else turn
            async with asyncio.timeout(2):
                results = await asyncio.gather(
                    post(client, session, turn), post(client, session, other)
                )
            assert sorted(status for status, _ in results) == (
                [200, 409] if different else [200, 200]
            ), "concurrent_open_statuses"
            for status, body in results:
                assert body == (
                    {"version": 1} if status == 200 else {"error": "turn_conflict"}
                ), "concurrent_open_body"
            count = await store._call(
                lambda: store.connection.execute(
                    "SELECT count(*) FROM turns"
                ).fetchone()[0]
            )
            assert count == 1, "concurrent_single_turn"

    asyncio.run(run())


def test_retry_after_response_loss(tmp_path, monkeypatch, service):
    async def run():
        async with service(tmp_path) as (client, session, store, _, _):
            committed = asyncio.Event()
            release = asyncio.Event()
            original = store.open_turn

            async def hold_response(turn):
                await original(turn)
                committed.set()
                await release.wait()

            monkeypatch.setattr(store, "open_turn", hold_response)
            turn = TurnOpen(str(uuid7()), "satellite-a", "voice", None, None)
            async with ClientSession(
                connector=UnixConnector(path=tmp_path / "service.sock")
            ) as lost_session:
                pending = asyncio.create_task(post(client, lost_session, turn))
                await asyncio.wait_for(committed.wait(), 2)
                before = await snapshot(store)
                pending.cancel()
                with suppress(asyncio.CancelledError):
                    await pending
            release.set()
            monkeypatch.setattr(store, "open_turn", original)
            assert await post(client, session, turn) == (200, {"version": 1}), (
                "lost_response_retry_success"
            )
            assert await snapshot(store) == before, "lost_response_retry_no_write"

    asyncio.run(run())


@pytest.mark.parametrize("parent_kind", ["absent", "other_satellite"])
def test_cleared_parent_compares_received_value_after_restart(tmp_path, parent_kind):
    from assist_live_server.store import SQLiteStore

    async def run():
        path = tmp_path / "turns.db"
        store = SQLiteStore(path, now_ns=lambda: NOW)
        parent = TurnOpen(str(uuid7()), "satellite-b", "voice", None, None)
        turn = TurnOpen(str(uuid7()), "satellite-a", "voice", parent.turn_id, None)
        try:
            if parent_kind == "other_satellite":
                await store.open_turn(parent)
            await store.open_turn(turn)
            assert (await store.get_turn(turn.turn_id))["parent_turn_id"] is None
        finally:
            store.close()
        store = SQLiteStore(path, now_ns=lambda: NOW)
        try:
            before = await snapshot(store)
            await store.open_turn(turn)
            assert await snapshot(store) == before, "cleared_parent_retry_unchanged"
            from assist_live_server.protocol import TurnExistsError

            with pytest.raises(TurnExistsError):
                await store.open_turn(replace(turn, parent_turn_id=None))
        finally:
            store.close()

    asyncio.run(run())


def test_old_row_conversation_normalization_has_no_retry_side_effects(
    tmp_path, service
):
    async def run():
        async with service(tmp_path) as (client, session, store, _, _):
            store.now_ns = lambda: NOW
            turn = TurnOpen(
                str(uuid7()), "satellite-a", "voice", None, None, False, NOW
            )
            await store.open_turn(turn)
            raw = {**asdict(turn), "conversation_id": "invalid conversation"}
            await store._call(
                lambda: store.connection.execute(
                    "UPDATE turns SET payload=? WHERE turn_id=?",
                    (json.dumps(raw), turn.turn_id),
                )
            )
            before = await snapshot(store)
            assert await post(client, session, turn) == (200, {"version": 1}), (
                "old_row_empty_conversation_retry"
            )
            assert await snapshot(store) == before, "old_row_retry_no_counter_change"

    asyncio.run(run())


def test_old_cleared_parent_row_uses_stored_parent(tmp_path, service):
    async def run():
        async with service(tmp_path) as (client, session, store, _, _):
            store.now_ns = lambda: NOW
            turn = TurnOpen(
                str(uuid7()), "satellite-a", "voice", str(uuid7()), None, False, NOW
            )
            await store.open_turn(turn)
            raw = {**asdict(turn), "parent_turn_id": None}
            await store._call(
                lambda: store.connection.execute(
                    "UPDATE turns SET payload=? WHERE turn_id=?",
                    (json.dumps(raw), turn.turn_id),
                )
            )
            before = await snapshot(store)
            assert await post(client, session, turn) == (
                409,
                {"error": "turn_conflict"},
            ), "old_cleared_parent_conflict"
            assert await post(client, session, replace(turn, parent_turn_id=None)) == (
                200,
                {"version": 1},
            ), "old_stored_parent_retry"
            assert await snapshot(store) == before, "old_parent_retry_no_write"

    asyncio.run(run())


@pytest.mark.parametrize("original_time", [None, NOW])
def test_omitted_open_time_is_not_explicit_server_time(
    tmp_path, service, original_time
):
    async def run():
        async with service(tmp_path) as (client, session, store, _, _):
            store.now_ns = lambda: NOW
            turn = TurnOpen(
                str(uuid7()), "satellite-a", "voice", None, None, False, original_time
            )
            await client.open_turn(turn)
            other = replace(turn, opened_utc_ns=NOW if original_time is None else None)
            assert await post(client, session, other) == (
                409,
                {"error": "turn_conflict"},
            ), "received_open_time_not_normalized_time"

    asyncio.run(run())


@pytest.mark.parametrize(
    "key,value",
    [
        ("received_parent_turn_id", "invalid-parent"),
        ("received_parent_turn_id", 1),
        ("requested_opened_utc_ns", True),
        ("requested_opened_utc_ns", -1),
        ("requested_opened_utc_ns", 2**63),
    ],
)
def test_private_received_inputs_use_public_field_validation(tmp_path, key, value):
    from assist_live_server.protocol import TurnExistsError
    from assist_live_server.store import SQLiteStore

    async def run():
        store = SQLiteStore(tmp_path / "turns.db", now_ns=lambda: NOW)
        try:
            turn = TurnOpen(str(uuid7()), "satellite-a", "voice", None, None)
            await store.open_turn(turn)
            raw = {**asdict(turn), key: value}
            await store._call(
                lambda: store.connection.execute(
                    "UPDATE turns SET payload=? WHERE turn_id=?",
                    (json.dumps(raw), turn.turn_id),
                )
            )
            before = await snapshot(store)
            with pytest.raises(TurnExistsError):
                await store.open_turn(turn)
            assert await snapshot(store) == before, (
                "invalid_received_inputs_no_counter_change"
            )
            decoded = await store._call(
                store._decode_turn, json.dumps(raw), turn.satellite
            )
            assert decoded is None, "invalid_received_inputs_not_decoded"
        finally:
            store.close()

    asyncio.run(run())


def test_identical_retry_does_not_maintain_notify_or_attach_pending(
    tmp_path, monkeypatch, service
):
    async def run():
        async with service(tmp_path) as (client, session, store, _, _):
            now = [NOW]
            store.now_ns = lambda: now[0]
            turn = TurnOpen(
                str(uuid7()), "satellite-a", "voice", None, None, False, NOW - 1
            )
            await store.open_turn(turn)
            await store.append_device(
                TimingEvent(None, "satellite-b", "device", "mic_open", NOW, "proxy", 0),
                NOW,
            )
            before = await snapshot(store)
            notifications = []
            monkeypatch.setattr(store, "_notify", lambda: notifications.append(True))
            now[0] += 121_000_000_000
            assert await post(client, session, turn) == (200, {"version": 1}), (
                "late_identical_retry_success"
            )
            assert await snapshot(store) == before, (
                "retry_no_row_counter_or_late_mark_change"
            )
            assert notifications == [], "retry_no_notification"

    asyncio.run(run())


def test_private_open_inputs_are_absent_from_public_read_paths(tmp_path, service):
    async def run():
        async with service(tmp_path) as (client, session, store, _, _):
            turn = TurnOpen(str(uuid7()), "satellite-a", "voice", str(uuid7()), None)
            await client.open_turn(turn)
            await store.append(
                TimingEvent(
                    turn.turn_id,
                    turn.satellite,
                    "integration",
                    "fallback_used",
                    1,
                    "measured",
                    None,
                    data={"part": "stt"},
                )
            )
            raw = await store._call(
                lambda: store.connection.execute(
                    "SELECT payload FROM turns WHERE turn_id=?", (turn.turn_id,)
                ).fetchone()[0]
            )
            assert PRIVATE_KEYS <= json.loads(raw).keys(), "received_inputs_persisted"
            decoded = await store._call(store._decode_turn, raw, turn.satellite)
            assert decoded is not None, "private_inputs_decode_valid"
            public = [await client.turn(turn.turn_id), asdict(decoded)]
            public.extend([row async for row in client.export(turn.satellite, 0)])
            stream = client.events(turn.satellite)
            try:
                public.append(await asyncio.wait_for(anext(stream), 2))
            finally:
                await stream.aclose()
            public.append(await store.diagnostics())
            async with session.get(
                client.url + "/metrics", headers=client._headers
            ) as response:
                public.append(await response.text())
            assert not any(key in json.dumps(public) for key in PRIVATE_KEYS), (
                "private_open_inputs_not_public"
            )

    asyncio.run(run())


@pytest.mark.parametrize("sha", ["b3b1e16", "c3aadd3", "5ac300b", "7ab511c"])
def test_archived_client_identical_open_retry(tmp_path, sha, service):
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
        async with service(tmp_path):
            code = """
import asyncio, sys
from uuid import uuid7
from aiohttp import ClientSession, UnixConnector
from assist_live_client import AssistLiveClient, TurnOpen
async def run():
    async with ClientSession(connector=UnixConnector(path=sys.argv[1])) as session:
        client = AssistLiveClient(
            "http://service.invalid", "synthetic-integration-token", session=session
        )
        turn = TurnOpen(str(uuid7()), "satellite-a", "voice", None, None)
        await client.open_turn(turn)
        try:
            await client.open_turn(turn)
        except Exception as error:
            raise AssertionError("archived_identical_open_retry") from error
asyncio.run(run())
"""
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                code,
                str(tmp_path / "service.sock"),
                env={**os.environ, "PYTHONPATH": str(tmp_path / "archived/client")},
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(process.communicate(), 10)
            assert process.returncode == 0, stdout.decode() + stderr.decode()

    asyncio.run(run())

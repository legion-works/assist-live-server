import asyncio
import json
import sqlite3

import pytest

OLD = b"synthetic-old-key"
NEW = b"synthetic-new-key"
EVENT = {
    "version": 1,
    "device": "satellite-a",
    "boot": 1,
    "seq": 0,
    "device_ms": 1000,
    "stage": "wake_detected",
    "data": {},
}
UNCHANGED = {"old": (True, True), "new": (False, False)}
ROTATED = {"old": (False, False), "new": (True, True)}
REFUSED = {"old": (False, False), "new": (False, False)}


class Crash(BaseException):
    """Stop the rotation like a process exit: no handler runs."""


class CommitProbe:
    """Wrap the writer connection and act on the COMMIT of the rotation."""

    def __init__(self, real, mode):
        self.real = real
        self.mode = mode
        self.armed = False
        self.synchronous = None

    def execute(self, sql, *args):
        if sql.startswith("DELETE FROM device_events"):
            self.armed = True
        return self.real.execute(sql, *args)

    def __getattr__(self, name):
        return getattr(self.real, name)

    def __enter__(self):
        return self.real.__enter__()

    def __exit__(self, kind, value, traceback):
        if not self.armed or kind is not None:
            return self.real.__exit__(kind, value, traceback)
        self.armed = False
        self.synchronous = self.real.execute("PRAGMA synchronous").fetchone()[0]
        if self.mode == "record":
            return self.real.__exit__(kind, value, traceback)
        if self.mode == "applied":
            self.real.commit()
        else:
            self.real.rollback()
        raise sqlite3.OperationalError("synthetic commit error")


def signed(key, seq, boot=7):
    from assist_live_server.auth import sign_event

    value = {**EVENT, "boot": boot, "seq": seq}
    return json.dumps({**value, "tag": sign_event(value, key)}).encode()


async def live_acceptance(store, registry, gateway, first=1):
    from assist_live_server.protocol import TimingIngress

    ingress = TimingIngress(registry)
    result = {}
    for name, key, seq in (("old", OLD, first), ("new", NEW, first + 1)):
        authenticated = ingress.accept(signed(key, seq))
        stored = False
        if authenticated:
            event = await ingress.queue.get()
            ingress.queue.task_done()
            try:
                stored = await store.accept_device(registry, event, gateway.convert)
            except sqlite3.Error:
                stored = False
        result[name] = (authenticated, stored)
    return result


async def rotation(tmp_path, inject, *, error=None, live=True):
    """Rotate with one injected fault; return live and restarted state."""
    from assist_live_server.auth import Registry
    from assist_live_server.event_gateway import EventGateway
    from assist_live_server.protocol import TimingIngress
    from assist_live_server.store import SQLiteStore

    path = tmp_path / "credentials"
    database = tmp_path / "timing.db"
    registry = Registry(path)
    registry.bind("satellite-a", "satellite-a", OLD)
    store = SQLiteStore(database)
    await store.configure_registry(registry)
    gateway = EventGateway(store)
    ingress = TimingIngress(registry)
    state = {}
    try:
        assert ingress.accept(signed(OLD, 0, boot=1))
        first = await ingress.queue.get()
        ingress.queue.task_done()
        assert await store.accept_device(registry, first, gateway.convert)
        probe = await inject(store, registry)
        if error is None:
            await store.rotate(registry, "satellite-a", NEW)
        else:
            with pytest.raises(error):
                await store.rotate(registry, "satellite-a", NEW)
        state["probe"] = probe
        if live:
            state["live"] = await live_acceptance(store, registry, gateway)
    finally:
        store.close()
    state["file"] = json.loads(path.read_text())["devices"]["satellite-a"]
    restarted = Registry.load(path)
    store = SQLiteStore(database)
    try:
        await store.configure_registry(restarted)
        replay = await store._call(store.replay.load_replay, "satellite-a")
        state["restart"] = {
            "key": restarted.device("satellite-a"),
            "first_boot_kept": replay is not None and 1 in replay.seen_boots,
            "rows": await store._call(
                lambda: store.connection.execute(
                    "SELECT count(*) FROM device_events WHERE seq='0'"
                ).fetchone()[0]
            ),
            "pending": "pending" in restarted.devices["satellite-a"],
        }
        state["after_restart"] = await live_acceptance(
            store, restarted, EventGateway(store), first=11
        )
    finally:
        store.close()
    return state


def assert_restart_unchanged(state):
    assert state["restart"] == {
        "key": ("satellite-a", OLD),
        "first_boot_kept": True,
        "rows": 1,
        "pending": False,
    }


def assert_restart_rotated(state):
    assert state["restart"] == {
        "key": ("satellite-a", NEW),
        "first_boot_kept": False,
        "rows": 0,
        "pending": False,
    }
    assert state["after_restart"] == {"old": (False, False), "new": (True, True)}


def run(tmp_path, inject, **options):
    return asyncio.run(rotation(tmp_path, inject, **options))


def test_rotation_commits_with_full_synchronous_and_restores_setting(tmp_path):
    async def inject(store, registry):
        store.connection = CommitProbe(store.connection, "record")
        return store.connection

    state = run(tmp_path, inject)
    assert state["probe"].synchronous == 2
    assert state["live"] == ROTATED
    assert state["file"] == {
        "satellite": "satellite-a",
        "secret": NEW.hex(),
        "epoch": 1,
    }
    assert_restart_rotated(state)


def test_rotation_pending_write_failure_keeps_old_key(tmp_path):
    async def inject(store, registry):
        def fail():
            raise OSError("synthetic pending write failure")

        registry.save = fail

    state = run(tmp_path, inject, error=OSError)
    assert state["live"] == UNCHANGED
    assert "pending" not in state["file"]
    assert_restart_unchanged(state)


def test_rotation_delete_failure_keeps_old_key(tmp_path):
    async def inject(store, registry):
        await store._call(
            store.connection.execute,
            "CREATE TRIGGER refuse_delete BEFORE DELETE ON device_events "
            "BEGIN SELECT RAISE(ABORT, 'synthetic delete failure'); END",
        )

    state = run(tmp_path, inject, error=sqlite3.IntegrityError)
    assert state["live"]["new"] == (False, False)
    assert state["live"]["old"][0] is True
    assert "pending" not in state["file"]
    assert state["restart"]["key"] == ("satellite-a", OLD)
    assert state["restart"]["rows"] == 1
    assert not state["restart"]["pending"]


def test_rotation_commit_error_without_commit_keeps_old_key(tmp_path):
    async def inject(store, registry):
        store.connection = CommitProbe(store.connection, "lost")

    state = run(tmp_path, inject, error=sqlite3.OperationalError)
    assert state["live"] == UNCHANGED
    assert "pending" not in state["file"]
    assert_restart_unchanged(state)


def test_rotation_commit_error_after_commit_completes_rotation(tmp_path):
    async def inject(store, registry):
        store.connection = CommitProbe(store.connection, "applied")

    state = run(tmp_path, inject)
    assert state["live"] == ROTATED
    assert state["file"]["secret"] == NEW.hex()
    assert_restart_rotated(state)


def test_rotation_epoch_read_failure_refuses_both_keys(tmp_path, monkeypatch):
    from assist_live_server import store as store_module
    from assist_live_server.store import CredentialStateError

    original = store_module.connect

    async def inject(store, registry):
        store.connection = CommitProbe(store.connection, "applied")

        def refuse(path):
            # Only the reopen after the COMMIT error fails; the restart works.
            monkeypatch.setattr(store_module, "connect", original)
            raise OSError("synthetic reopen failure")

        monkeypatch.setattr(store_module, "connect", refuse)
        return store

    state = run(tmp_path, inject, error=CredentialStateError)
    assert state["probe"].device_write_failed
    assert state["live"] == REFUSED
    assert_restart_rotated(state)


def test_rotation_publish_failure_refuses_both_keys(tmp_path):
    async def inject(store, registry):
        def fail(*args):
            raise RuntimeError("synthetic publish failure")

        registry._publish = fail
        registry._publish_rotation = fail

    state = run(tmp_path, inject, error=RuntimeError)
    assert state["live"] == REFUSED
    assert_restart_rotated(state)


def test_rotation_stop_before_publish_refuses_previous_epoch_writes(tmp_path):
    async def inject(store, registry):
        def stop(*args):
            raise Crash()

        registry._publish = stop
        registry._publish_rotation = stop

    state = run(tmp_path, inject, error=Crash)
    # The old key still authenticates in memory, but the store refuses the
    # write because its epoch is not the committed epoch.
    assert state["live"] == {"old": (True, False), "new": (False, False)}
    assert_restart_rotated(state)


def test_rotation_promote_write_failure_completes_after_restart(tmp_path):
    async def inject(store, registry):
        save = registry.save
        calls = []

        def second_fails():
            calls.append(None)
            if len(calls) == 2:
                raise OSError("synthetic promote write failure")
            save()

        registry.save = second_fails

    state = run(tmp_path, inject)
    assert state["live"] == ROTATED
    assert state["file"]["secret"] == OLD.hex()
    assert state["file"]["pending"] == {"secret": NEW.hex(), "epoch": 1}
    assert_restart_rotated(state)


@pytest.mark.parametrize(
    ("method", "rotated"),
    [
        ("_write_pending", False),
        ("clear_replay", False),
        ("_publish", True),
        ("_promote_pending", True),
    ],
)
def test_rotation_stop_between_steps_resolves_at_restart(tmp_path, method, rotated):
    async def inject(store, registry):
        if method == "clear_replay":
            target = store.replay
        else:
            target = registry
        original = getattr(target, method)

        def stop(*args):
            if method in {"_write_pending", "_promote_pending"}:
                original(*args)
            raise Crash()

        setattr(target, method, stop)

    state = run(tmp_path, inject, error=Crash, live=False)
    if method == "_write_pending":
        # An older server reads only "secret" and keeps the previous key.
        assert state["file"]["secret"] == OLD.hex()
        assert state["file"]["pending"]["epoch"] == 1
    if rotated:
        assert_restart_rotated(state)
    else:
        assert_restart_unchanged(state)


async def start(tmp_path, *, save_fails=False):
    """Load the files like a server start; return the registry and its store."""
    from assist_live_server.auth import Registry
    from assist_live_server.store import SQLiteStore

    registry = Registry.load(tmp_path / "credentials")
    if save_fails:

        def fail():
            raise OSError("synthetic start write failure")

        registry.save = fail
    store = SQLiteStore(tmp_path / "timing.db")
    await store.configure_registry(registry)
    return registry, store


@pytest.mark.parametrize("committed", [True, False])
def test_start_write_failure_keeps_device_blocked(tmp_path, committed):
    from assist_live_server.auth import Registry
    from assist_live_server.store import SQLiteStore

    def stop(*args):
        raise Crash()

    async def interrupted():
        registry = Registry(tmp_path / "credentials")
        registry.bind("satellite-a", "satellite-a", OLD)
        store = SQLiteStore(tmp_path / "timing.db")
        try:
            await store.configure_registry(registry)
            if committed:
                registry._promote_pending = stop
            else:
                store.replay.clear_replay = stop
            with pytest.raises(Crash):
                await store.rotate(registry, "satellite-a", NEW)
        finally:
            store.close()

    async def check():
        registry, store = await start(tmp_path, save_fails=True)
        try:
            # The file keeps the pending entry, so neither key is accepted.
            assert registry.device_snapshot("satellite-a") is None
            assert registry.device("satellite-a") is None
        finally:
            store.close()

    asyncio.run(interrupted())
    devices = json.loads((tmp_path / "credentials").read_text())["devices"]
    assert devices["satellite-a"]["pending"]["epoch"] == 1
    asyncio.run(check())


def test_missing_store_row_with_file_epoch_blocks_device(tmp_path, caplog):
    from assist_live_server.auth import Registry

    registry = Registry(tmp_path / "credentials")
    registry.bind("satellite-a", "satellite-a", OLD)
    registry.devices["satellite-a"]["epoch"] = 2
    registry.save()

    async def check():
        registry, store = await start(tmp_path)
        try:
            assert registry.device_snapshot("satellite-a") is None
            assert registry.blocked_devices() == ["satellite-a"]
            rows = await store._call(
                lambda: store.connection.execute(
                    "SELECT count(*) FROM credential_state"
                ).fetchone()[0]
            )
            assert rows == 0
        finally:
            store.close()

    asyncio.run(check())
    assert "credential_state_mismatch" in caplog.text


def test_missing_store_row_with_epoch_zero_keeps_legacy_key(tmp_path):
    from assist_live_server.auth import Registry

    registry = Registry(tmp_path / "credentials")
    registry.bind("satellite-a", "satellite-a", OLD)

    async def check():
        registry, store = await start(tmp_path)
        try:
            assert registry.device_snapshot("satellite-a") == ("satellite-a", OLD, 0)
        finally:
            store.close()

    asyncio.run(check())


def test_check_credentials_command_names_pending_satellites(
    tmp_path, monkeypatch, capsys
):
    from assist_live_server.__main__ import main
    from assist_live_server.auth import Registry

    monkeypatch.setenv("ASSIST_LIVE_DATA", str(tmp_path))
    registry = Registry(tmp_path / "registry.json")
    registry.bind("device-a", "satellite-a", OLD)
    registry.bind("device-b", "satellite-b", b"synthetic-other-key")
    assert main(["--check-credentials"]) == 0
    assert "PASS" in capsys.readouterr().out
    registry._write_pending("device-a", NEW, 1)
    assert main(["--check-credentials"]) == 1
    output = capsys.readouterr().out
    assert "satellite-a" in output
    assert "satellite-b" not in output
    assert NEW.hex() not in output and OLD.hex() not in output


def test_health_reports_blocked_credential_after_maintenance_and_writes(tmp_path):
    from assist_live_client import TimingEvent

    from tests.contract.test_event_store import service

    async def scenario():
        async with service(tmp_path) as (client, session, store, ingress, _):

            async def health():
                async with session.get(
                    client.url + "/health", headers=client._headers
                ) as response:
                    assert response.status == 200
                    return await response.json()

            assert (await health())["healthy"] is True
            await store._call(ingress.registry._block, "satellite-a")
            await store.maintain()
            await store.append(
                TimingEvent(
                    None, "satellite-b", "server", "unmatched", 1, "proxy", None
                )
            )
            body = await health()
            assert body["healthy"] is False
            assert body["unavailable"]["credentials"] == "credential_blocked"

    asyncio.run(scenario())

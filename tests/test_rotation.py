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


def data_files(path):
    """Give the SHA-256 of each file below the data directory."""
    import hashlib

    return {
        item.relative_to(path).as_posix(): hashlib.sha256(item.read_bytes()).hexdigest()
        for item in sorted(path.rglob("*"))
        if item.is_file()
    }


async def data_start(path):
    """Load the data directory like the server command; give blocked satellites."""
    from assist_live_server.auth import Registry
    from assist_live_server.store import SQLiteStore

    registry = Registry.load(path / "registry.json")
    store = SQLiteStore(path / "turns.sqlite3")
    try:
        await store.configure_registry(registry)
        return [registry.credentials[d].satellite for d in registry.blocked_devices()]
    finally:
        store.close()


def write_state_row(path, device, epoch, secret):
    from assist_live_server.auth import fingerprint
    from assist_live_server.store import SQLiteStore

    store = SQLiteStore(path / "turns.sqlite3")
    try:
        store.writer.submit(
            store.connection.execute,
            "INSERT INTO credential_state VALUES(?,?,?)",
            (device, epoch, fingerprint(secret)),
        ).result()
    finally:
        store.close()


@pytest.mark.parametrize("fault", ["missing_row", "other_epoch", "other_key"])
def test_check_credentials_refuses_blocked_device_without_pending(
    tmp_path, monkeypatch, capsys, fault
):
    from assist_live_server.__main__ import main
    from assist_live_server.auth import Registry

    monkeypatch.setenv("ASSIST_LIVE_DATA", str(tmp_path))
    registry = Registry(tmp_path / "registry.json")
    registry.bind("device-a", "satellite-a", OLD)
    registry.bind("device-b", "satellite-b", b"synthetic-other-key")
    registry.devices["device-a"]["epoch"] = 2
    registry.save()
    if fault == "other_epoch":
        write_state_row(tmp_path, "device-a", 1, OLD)
    elif fault == "other_key":
        write_state_row(tmp_path, "device-a", 2, NEW)
    before = data_files(tmp_path)
    assert "pending" not in json.loads((tmp_path / "registry.json").read_text())

    assert main(["--check-credentials"]) == 1
    output = capsys.readouterr().out
    assert output.startswith("FAIL credentials:")
    assert "satellite-a: credential_state_mismatch" in output
    assert "satellite-b" not in output
    assert OLD.hex() not in output and NEW.hex() not in output
    assert data_files(tmp_path) == before
    # The check and a server start refuse the same devices.
    assert asyncio.run(data_start(tmp_path)) == ["satellite-a"]


@pytest.mark.parametrize("committed", [True, False])
def test_check_credentials_refuses_pending_entry_against_store(
    tmp_path, monkeypatch, capsys, committed
):
    from assist_live_server.__main__ import main
    from assist_live_server.auth import Registry

    monkeypatch.setenv("ASSIST_LIVE_DATA", str(tmp_path))
    registry = Registry(tmp_path / "registry.json")
    registry.bind("device-a", "satellite-a", OLD)
    registry._write_pending("device-a", NEW, 1)
    if committed:
        write_state_row(tmp_path, "device-a", 1, NEW)
    before = data_files(tmp_path)

    assert main(["--check-credentials"]) == 1
    output = capsys.readouterr().out
    assert "satellite-a: pending" in output
    assert OLD.hex() not in output and NEW.hex() not in output
    assert data_files(tmp_path) == before


def start_copy(path, tmp_path, data="."):
    """Run a server start on a copy of the data directory; give blocked satellites."""
    import shutil

    copy = tmp_path / "start-copy"
    shutil.copytree(path, copy, symlinks=True)
    return asyncio.run(data_start(copy / data))


@pytest.mark.parametrize("database", ["none", "without_table", "current"])
def test_check_credentials_accepts_clean_legacy_data_without_change(
    tmp_path, monkeypatch, capsys, database
):
    from assist_live_server.__main__ import main
    from assist_live_server.auth import Registry
    from assist_live_server.store import SQLiteStore

    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("ASSIST_LIVE_DATA", str(data))
    registry = Registry(data / "registry.json")
    registry.bind("device-a", "satellite-a", OLD)
    registry.bind("device-b", "satellite-b", b"synthetic-other-key")
    assert all("epoch" not in row for row in registry.devices.values())
    if database != "none":
        SQLiteStore(data / "turns.sqlite3").close()
    if database == "without_table":
        # A current schema without the credential_state table is the schema
        # of an earlier version for the credential decision.
        connection = sqlite3.connect(data / "turns.sqlite3", isolation_level=None)
        connection.execute("DROP TABLE credential_state")
        connection.close()
    before = data_files(data)

    assert main(["--check-credentials"]) == 0
    captured = capsys.readouterr()
    assert captured.out.startswith("PASS credentials:")
    assert data_files(data) == before
    if database == "without_table":
        connection = sqlite3.connect(data / "turns.sqlite3")
        tables = connection.execute(
            "SELECT name FROM sqlite_master WHERE name='credential_state'"
        ).fetchall()
        connection.close()
        assert tables == []
    # A server start on a copy accepts the same credentials.
    assert start_copy(data, tmp_path) == []


def crash_snapshot(path, rows, sidecars):
    """Make a stopped database whose WAL keeps committed rows not in the main file."""
    import shutil

    from assist_live_server.auth import fingerprint
    from assist_live_server.replay_store import connect
    from assist_live_server.store import SQLiteStore

    work = path.parent / (path.name + ".work")
    work.mkdir()
    SQLiteStore(work / "turns.sqlite3").close()
    writer = connect(work / "turns.sqlite3")
    try:
        for device, epoch, secret in rows:
            writer.execute(
                "INSERT INTO credential_state VALUES(?,?,?)",
                (device, epoch, fingerprint(secret)),
            )
        # A copy while the writer is open is the state after a process stop.
        path.mkdir(exist_ok=True)
        for suffix in ("", *sidecars):
            shutil.copyfile(
                work / f"turns.sqlite3{suffix}", path / f"turns.sqlite3{suffix}"
            )
    finally:
        writer.close()
    shutil.rmtree(work)
    main_file = sqlite3.connect(
        f"{(path / 'turns.sqlite3').as_uri()}?mode=ro&immutable=1", uri=True
    )
    try:
        assert (
            main_file.execute("SELECT count(*) FROM credential_state").fetchone()[0]
            == 0
        )
    finally:
        main_file.close()


def test_check_credentials_reads_wal_at_symlink_destination(
    tmp_path, monkeypatch, capsys
):
    from assist_live_server.__main__ import main
    from assist_live_server.auth import Registry

    root = tmp_path / "root"
    data = root / "data"
    data.mkdir(parents=True)
    monkeypatch.setenv("ASSIST_LIVE_DATA", str(data))
    registry = Registry(data / "registry.json")
    registry.bind("device-a", "satellite-a", OLD)
    # The committed rotation is only in the WAL beside the link destination.
    crash_snapshot(root / "real", [("device-a", 1, NEW)], ("-wal", "-shm"))
    (data / "turns.sqlite3").symlink_to("../real/turns.sqlite3")
    before = data_files(root)

    assert main(["--check-credentials"]) == 1
    output = capsys.readouterr().out
    assert "satellite-a: credential_state_mismatch" in output
    assert data_files(root) == before
    assert (data / "turns.sqlite3").is_symlink()
    # A server start on a copy refuses the same device.
    assert start_copy(root, tmp_path, "data") == ["satellite-a"]


def test_check_credentials_reads_wal_without_shm_and_adds_no_file(
    tmp_path, monkeypatch, capsys
):
    from assist_live_server.__main__ import main
    from assist_live_server.auth import Registry

    data = tmp_path / "data"
    monkeypatch.setenv("ASSIST_LIVE_DATA", str(data))
    crash_snapshot(data, [("device-a", 1, NEW)], ("-wal",))
    registry = Registry(data / "registry.json")
    registry.bind("device-a", "satellite-a", NEW)
    registry.devices["device-a"]["epoch"] = 1
    registry.save()
    before = data_files(data)
    assert sorted(before) == ["registry.json", "turns.sqlite3", "turns.sqlite3-wal"]

    # Only the row in the WAL makes epoch 1 agree with the store.
    assert main(["--check-credentials"]) == 0
    assert capsys.readouterr().out.startswith("PASS credentials:")
    assert data_files(data) == before
    assert start_copy(data, tmp_path) == []


def test_check_credentials_prints_only_valid_satellite_ids(
    tmp_path, monkeypatch, capsys
):
    from assist_live_server.__main__ import main
    from assist_live_server.auth import Registry, fingerprint

    monkeypatch.setenv("ASSIST_LIVE_DATA", str(tmp_path))
    registry = Registry(tmp_path / "registry.json")
    registry.bind("device-a", "satellite-a", OLD)
    registry.bind("device-b", "satellite-b", b"synthetic-other-key")
    registry.bind("device-c", "satellite-c", b"synthetic-third-key")
    labels = {
        "device-a": "bad\x1b[31m\nlabel",
        "device-b": NEW.hex(),
        "device-c": "sk-" + "x" * 140,
    }
    for device, label in labels.items():
        registry.devices[device]["satellite"] = label
    registry.save()

    # The labels are not valid, so the check fails also without other faults.
    assert main(["--check-credentials"]) == 1
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert "invalid satellite id" in output
    for label in labels.values():
        assert label not in output
    assert "\x1b" not in output and output.count("\n") == 1
    for secret in (OLD, NEW, b"synthetic-other-key", b"synthetic-third-key"):
        assert secret.hex() not in output and fingerprint(secret) not in output


def test_check_credentials_hides_labels_with_credential_values(
    tmp_path, monkeypatch, capsys
):
    from assist_live_server.__main__ import main
    from assist_live_server.auth import Registry, fingerprint

    stored = b"synthetic-stored-key"
    token = "synthetic-token-value"
    monkeypatch.setenv("ASSIST_LIVE_DATA", str(tmp_path))
    registry = Registry(tmp_path / "registry.json")
    registry.bind("device-a", "satellite-a", OLD)
    registry.bind("device-b", "satellite-b", b"synthetic-other-key")
    registry.bind("device-c", "satellite-c", stored)
    registry.bind("device-d", "satellite-d", b"synthetic-fourth-key")
    registry.bind("device-e", "satellite-e", b"synthetic-fifth-key")
    registry.add_token(token, ["satellite-c"])
    # Each label is a valid identifier, and each of these devices is refused.
    labels = {
        "device-a": OLD.hex(),
        "device-b": fingerprint(stored),
        "device-e": token,
    }
    for device, label in labels.items():
        registry.devices[device]["satellite"] = label
        registry.devices[device]["epoch"] = 1
    registry.devices["device-d"]["epoch"] = 1
    registry.save()
    write_state_row(tmp_path, "device-c", 0, stored)

    assert main(["--check-credentials"]) == 1
    captured = capsys.readouterr()
    output = captured.out + captured.err
    for label in labels.values():
        assert label not in output and label.upper() not in output
    assert "satellite-d: credential_state_mismatch" in output
    for position in (1, 2, 5):
        assert f"device {position}: credential_state_mismatch" in output
    assert output.count("credential_state_mismatch") == 4


def test_check_credentials_uses_start_row_lookup_with_column_collation(
    tmp_path, monkeypatch, capsys
):
    from assist_live_server.__main__ import main
    from assist_live_server.auth import Registry, fingerprint
    from assist_live_server.store import SQLiteStore

    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("ASSIST_LIVE_DATA", str(data))
    registry = Registry(data / "registry.json")
    registry.bind("device-a", "satellite-a", OLD)
    # A restored database can have a different but compatible table schema.
    # The start keeps it, and SQLite compares device names without case.
    connection = sqlite3.connect(data / "turns.sqlite3", isolation_level=None)
    connection.execute(
        "CREATE TABLE credential_state (device TEXT COLLATE NOCASE PRIMARY KEY, "
        "epoch INTEGER NOT NULL, fingerprint TEXT NOT NULL)"
    )
    connection.execute(
        "INSERT INTO credential_state VALUES(?,?,?)", ("DEVICE-A", 1, fingerprint(NEW))
    )
    connection.close()
    SQLiteStore(data / "turns.sqlite3").close()
    before = data_files(data)

    blocked = start_copy(data, tmp_path)
    assert blocked == ["satellite-a"]
    assert main(["--check-credentials"]) == 1
    assert "satellite-a: credential_state_mismatch" in capsys.readouterr().out
    assert data_files(data) == before


def test_check_credentials_limits_printed_satellites(tmp_path, monkeypatch, capsys):
    from assist_live_server.__main__ import main
    from assist_live_server.auth import Registry

    monkeypatch.setenv("ASSIST_LIVE_DATA", str(tmp_path))
    registry = Registry(tmp_path / "registry.json")
    for index in range(25):
        registry.bind(
            f"device-{index:02}", f"satellite-{index:02}", b"key-%02d" % index
        )
        registry.devices[f"device-{index:02}"]["epoch"] = 1
    registry.save()

    assert main(["--check-credentials"]) == 1
    output = capsys.readouterr().out
    assert output.count(": credential_state_mismatch") == 20
    assert "satellite-19" in output and "satellite-20" not in output
    assert output.rstrip().endswith("5 more")


def test_restart_after_failed_start_write_resolves_device(tmp_path):
    from assist_live_server.auth import Registry
    from assist_live_server.store import SQLiteStore

    async def scenario():
        registry = Registry(tmp_path / "credentials")
        registry.bind("satellite-a", "satellite-a", OLD)
        store = SQLiteStore(tmp_path / "timing.db")
        try:
            await store.configure_registry(registry)
            registry._promote_pending = lambda device: (_ for _ in ()).throw(Crash())
            with pytest.raises(Crash):
                await store.rotate(registry, "satellite-a", NEW)
        finally:
            store.close()
        registry, store = await start(tmp_path, save_fails=True)
        try:
            assert registry.blocked_devices() == ["satellite-a"]
            # A blocked device refuses a new rotation, also with a repaired file.
            from assist_live_server.store import CredentialStateError

            with pytest.raises(CredentialStateError):
                await store.rotate(registry, "satellite-a", b"synthetic-third-key")
        finally:
            store.close()
        # After the cause of the write failure is repaired, a restart promotes.
        registry, store = await start(tmp_path)
        try:
            assert registry.device_snapshot("satellite-a") == ("satellite-a", NEW, 1)
        finally:
            store.close()

    asyncio.run(scenario())
    devices = json.loads((tmp_path / "credentials").read_text())["devices"]
    assert "pending" not in devices["satellite-a"]


def test_restore_of_matching_pair_resolves_mismatched_device(tmp_path):
    import shutil

    from assist_live_server.auth import Registry
    from assist_live_server.store import SQLiteStore

    backup = tmp_path / "backup"
    backup.mkdir()

    async def scenario():
        registry = Registry(tmp_path / "credentials")
        registry.bind("satellite-a", "satellite-a", OLD)
        store = SQLiteStore(tmp_path / "timing.db")
        try:
            await store.configure_registry(registry)
            await store.rotate(registry, "satellite-a", NEW)
        finally:
            store.close()
        # A backup of the pair at epoch 1, then a second rotation.
        for name in ("credentials", "timing.db"):
            shutil.copy2(tmp_path / name, backup / name)
        registry, store = await start(tmp_path)
        try:
            await store.rotate(registry, "satellite-a", b"synthetic-third-key")
        finally:
            store.close()
        # Only the database comes back from the backup: the pair does not agree.
        shutil.copy2(backup / "timing.db", tmp_path / "timing.db")
        registry, store = await start(tmp_path)
        try:
            assert registry.blocked_devices() == ["satellite-a"]
        finally:
            store.close()
        # The credential file from the same backup makes a matching pair again.
        shutil.copy2(backup / "credentials", tmp_path / "credentials")
        registry, store = await start(tmp_path)
        try:
            assert registry.blocked_devices() == []
            assert registry.device_snapshot("satellite-a") == ("satellite-a", NEW, 1)
        finally:
            store.close()

    asyncio.run(scenario())


def test_provisioning_does_not_reset_mismatched_device(tmp_path):
    from assist_live_server.auth import Registry
    from assist_live_server.provision import provision

    path = tmp_path / "credentials"
    registry = Registry(path)
    registry.bind("satellite-a", "satellite-a", OLD)
    registry.devices["satellite-a"]["epoch"] = 2
    registry.save()
    # The provisioning command refuses a satellite that the file already has.
    with pytest.raises(ValueError):
        provision(path, "satellite-a")


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

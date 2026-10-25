import json
import os
import sqlite3
from dataclasses import asdict
from pathlib import Path

from .auth import ReplayState


def connect(path: Path) -> sqlite3.Connection:
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    os.close(fd)
    connection = sqlite3.connect(path, isolation_level=None, timeout=0.05)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.execute("PRAGMA wal_autocheckpoint=0")
    return connection


class SQLiteReplayStore:
    def __init__(
        self, path: Path, *, connection: sqlite3.Connection | None = None
    ) -> None:
        self.connection = connection if connection is not None else connect(path)
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS replay "
            "(device TEXT PRIMARY KEY, state TEXT NOT NULL)"
        )

    def load_replay(self, device: str) -> ReplayState | None:
        row = self.connection.execute(
            "SELECT state FROM replay WHERE device=?", (device,)
        ).fetchone()
        if row is None:
            return None
        value = json.loads(row[0])
        value["seen_boots"] = frozenset(value["seen_boots"])
        return ReplayState(**value)

    def save_replay(self, device: str, state: ReplayState) -> None:
        value = asdict(state)
        value["seen_boots"] = sorted(state.seen_boots)
        try:
            # The writer includes this update in the device-event transaction.
            self.connection.execute(
                "INSERT INTO replay VALUES (?, ?) "
                "ON CONFLICT(device) DO UPDATE SET state=excluded.state",
                (device, json.dumps(value, separators=(",", ":"))),
            )
        except sqlite3.Error:
            raise OSError("replay persistence failed") from None

    def clear_replay(self, device: str) -> None:
        try:
            self.connection.execute("DELETE FROM replay WHERE device=?", (device,))
        except sqlite3.Error:
            raise OSError("replay persistence failed") from None

    def close(self) -> None:
        self.connection.close()

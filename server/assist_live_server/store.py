import asyncio
import json
import sqlite3
import time
import unicodedata
from collections import Counter
from collections.abc import AsyncIterator, Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from assist_live_client.models import (
    STAGES,
    TimingEvent,
    TurnOpen,
    TurnRecord,
    identifier,
)
from assist_live_client.protocol import MAX_CONTROL

from .auth import Registry
from .protocol import DeviceEvent, TurnExistsError
from .replay_store import SQLiteReplayStore, connect

SPEAKER_NAME_DROPS: Counter[str] = Counter(
    dict.fromkeys(
        ("stage", "type", "empty", "control", "whitespace", "bound", "encoding"), 0
    )
)


def safe_speaker_name(value: object, stage: str) -> str | None:
    reason = ""
    if stage != "speaker_result":
        reason = "stage"
    elif not isinstance(value, str):
        reason = "type"
    else:
        value = unicodedata.normalize("NFC", value)
        if not value:
            reason = "empty"
        elif any(unicodedata.category(c) == "Cc" for c in value):
            reason = "control"
        elif value != value.strip():
            reason = "whitespace"
        else:
            try:
                if len(value) > 64 or len(value.encode("utf-8")) > 256:
                    reason = "bound"
            except UnicodeEncodeError:
                reason = "encoding"
        if not reason:
            return value
    SPEAKER_NAME_DROPS[reason] += 1
    return None


def safe_data(event: TimingEvent, transcript: bool = False) -> dict[str, object]:
    data: dict[str, object] = {}
    if event.stage == "mic_open" and "pre_roll_ms" in event.data:
        data["pre_roll_ms"] = event.data["pre_roll_ms"]
    for key in ("part", "destination", "provider", "name", "original_stage"):
        value = event.data.get(key)
        if key == "name":
            if key in event.data:
                name = safe_speaker_name(value, event.stage)
                if name is not None:
                    data[key] = name
            continue
        if not isinstance(value, str):
            continue
        if key == "part" and value not in {"stt", "tts"}:
            continue
        if key == "destination" and value not in {"local", "cloud"}:
            continue
        if key == "original_stage" and value not in STAGES:
            continue
        try:
            identifier(value)
        except ValueError:
            continue
        data[key] = value
    distance = event.data.get("distance")
    if (
        isinstance(distance, (float, int))
        and not isinstance(distance, bool)
        and 0 <= distance <= 2
    ):
        data["distance"] = distance
    text = event.data.get("transcript")
    if transcript and event.stage == "stt_final" and isinstance(text, str):
        data["transcript"] = text
    return data


class SQLiteStore:
    def __init__(
        self,
        path: Path,
        *,
        record_transcripts: bool = False,
        retention_days: int = 30,
        now_ns: Callable[[], int] = time.time_ns,
    ) -> None:
        if type(retention_days) is not int or retention_days <= 0:
            raise ValueError("invalid retention")
        self.path = path
        self.record_transcripts = record_transcripts
        self.retention_ns = retention_days * 86_400_000_000_000
        self.now_ns = now_ns
        self.last_retention_ns: int | None = None
        self.changed = asyncio.Event()
        self.loop: asyncio.AbstractEventLoop | None = None
        self.writer = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="timing-writer"
        )
        self.writer.submit(self._initialize).result()

    def _initialize(self) -> None:
        self.connection = connect(self.path)
        self.replay = SQLiteReplayStore(self.path, connection=self.connection)
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS turns (
                turn_id TEXT PRIMARY KEY, satellite TEXT NOT NULL,
                payload TEXT NOT NULL, opened_ns INTEGER NOT NULL,
                status TEXT NOT NULL, closed_ns INTEGER
            );
            CREATE INDEX IF NOT EXISTS turns_satellite ON turns(satellite, status);
            CREATE INDEX IF NOT EXISTS turns_opened ON turns(opened_ns);
            CREATE INDEX IF NOT EXISTS turns_status ON turns(status, opened_ns);
            CREATE TABLE IF NOT EXISTS events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                satellite TEXT NOT NULL, turn_id TEXT, utc_ns INTEGER NOT NULL,
                payload TEXT NOT NULL, stored_ns INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS events_satellite ON events(satellite, event_id);
            CREATE INDEX IF NOT EXISTS events_stored ON events(stored_ns);
            CREATE INDEX IF NOT EXISTS events_turn ON events(turn_id, event_id);
            CREATE INDEX IF NOT EXISTS events_stage
                ON events(json_extract(payload, '$.stage'), satellite);
            CREATE TABLE IF NOT EXISTS counters (
                satellite TEXT PRIMARY KEY, events_total INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS pending (
                pending_id INTEGER PRIMARY KEY, satellite TEXT NOT NULL,
                arrival_ns INTEGER NOT NULL, payload TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS pending_satellite ON pending(satellite);
            CREATE INDEX IF NOT EXISTS pending_arrival ON pending(arrival_ns);
            CREATE TABLE IF NOT EXISTS device_events (
                device TEXT NOT NULL, boot TEXT NOT NULL, seq TEXT NOT NULL,
                payload TEXT NOT NULL, stored_ns INTEGER NOT NULL,
                UNIQUE(device, boot, seq)
            );
            CREATE INDEX IF NOT EXISTS device_events_stored ON device_events(stored_ns);
            UPDATE turns SET status='incomplete' WHERE status IN ('open','closing');
        """)

    async def _call[T](self, function: Callable[..., T], *args: object) -> T:
        self.loop = asyncio.get_running_loop()
        return await self.loop.run_in_executor(self.writer, function, *args)

    def _notify(self) -> None:
        if self.loop is not None:
            self.loop.call_soon_threadsafe(self.changed.set)

    async def configure_registry(self, registry: Registry) -> None:
        await self._call(self._configure_registry, registry)

    def _configure_registry(self, registry: Registry) -> None:
        registry.replay_store = self.replay
        for device in registry.devices:
            if state := self.replay.load_replay(device):
                registry.replay[device] = state

    async def rotate(self, registry: Registry, device: str, secret: bytes) -> None:
        await self._call(self._rotate, registry, device, secret)

    def _rotate(self, registry: Registry, device: str, secret: bytes) -> None:
        if registry.replay_store is not self.replay:
            raise ValueError("registry must use this store")
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            registry._rotate(device, secret)
            self.connection.execute(
                "DELETE FROM device_events WHERE device=?", (device,)
            )

    async def accept_device(
        self,
        registry: Registry,
        device: DeviceEvent,
        convert: Callable[[DeviceEvent], TimingEvent],
    ) -> bool:
        return await self._call(self._accept_device, registry, device, convert)

    def _accept_device(
        self,
        registry: Registry,
        device: DeviceEvent,
        convert: Callable[[DeviceEvent], TimingEvent],
    ) -> bool:
        name = device.event["device"]
        original = registry.replay[name]
        try:
            with self.connection:
                self.connection.execute("BEGIN IMMEDIATE")
                if not registry.accept_sequence(
                    name,
                    device.event["boot"],
                    device.event["seq"],
                    credential_epoch=device.credential_epoch,
                ):
                    return False
                self.connection.execute(
                    "INSERT INTO device_events VALUES(?,?,?,?,?)",
                    (
                        name,
                        str(device.event["boot"]),
                        str(device.event["seq"]),
                        "",
                        self.now_ns(),
                    ),
                )
                # Reserve the unique key before the clock accepts a sample.
                event = convert(
                    replace(device, new_boot=original.boot != device.event["boot"])
                )
                self.connection.execute(
                    "UPDATE device_events SET payload=? "
                    "WHERE device=? AND boot=? AND seq=?",
                    (
                        self._encode(event),
                        name,
                        str(device.event["boot"]),
                        str(device.event["seq"]),
                    ),
                )
                self._append_device_rows(event, device.arrival_ns)
        except Exception as error:
            registry.replay[name] = original
            if isinstance(error, (sqlite3.Error, ValueError, TypeError, OverflowError)):
                return False
            raise
        self._notify()
        return True

    def _encode(self, event: TimingEvent) -> str:
        row = json.dumps(asdict(event), separators=(",", ":"), allow_nan=False)
        # The envelope also has a cursor and version within the protocol bound.
        if len(row.encode()) > MAX_CONTROL - 128:
            raise ValueError("stored record too large")
        return row

    def _insert(self, event: TimingEvent) -> None:
        if event.turn_id is not None:
            self.connection.execute(
                "UPDATE turns SET status='incomplete' "
                "WHERE turn_id=? AND status='open' AND opened_ns<=?",
                (event.turn_id, self.now_ns() - 120_000_000_000),
            )
            self._refresh_status(event.turn_id)
        self.connection.execute(
            "INSERT INTO events(satellite,turn_id,utc_ns,payload,stored_ns) "
            "VALUES(?,?,?,?,?)",
            (
                event.satellite,
                event.turn_id,
                event.t_utc_ns,
                self._encode(event),
                self.now_ns(),
            ),
        )
        self.connection.execute(
            "INSERT INTO counters VALUES(?,1) ON CONFLICT(satellite) DO UPDATE "
            "SET events_total=events_total+1",
            (event.satellite,),
        )
        if event.turn_id and event.stage == "playback_end":
            self.connection.execute(
                "UPDATE turns SET status='closing',closed_ns=? "
                "WHERE turn_id=? AND status='open'",
                (self.now_ns(), event.turn_id),
            )
        if event.turn_id and event.stage == "turn_superseded":
            self.connection.execute(
                "UPDATE turns SET status='incomplete' WHERE turn_id=?", (event.turn_id,)
            )
        if event.turn_id:
            self._refresh_status(event.turn_id)

    def _refresh_status(self, turn_id: str) -> None:
        row = self.connection.execute(
            "SELECT payload,status,closed_ns FROM turns WHERE turn_id=?", (turn_id,)
        ).fetchone()
        if row is None or row[1] != "closing":
            return
        expected = {
            "tts_first_text",
            "tts_first_byte",
            "playback_start",
            "playback_end",
        }
        if json.loads(row[0])["turn_type"] == "voice":
            expected |= {
                "wake_detected",
                "mic_open",
                "speech_start",
                "end_decision",
                "stt_final",
            }
        stages = {
            json.loads(raw)["stage"]
            for (raw,) in self.connection.execute(
                "SELECT payload FROM events WHERE turn_id=?", (turn_id,)
            )
        }
        if expected <= stages:
            self.connection.execute(
                "UPDATE turns SET status='complete' WHERE turn_id=?", (turn_id,)
            )
        elif row[2] <= self.now_ns() - 5_000_000_000:
            self.connection.execute(
                "UPDATE turns SET status='incomplete' WHERE turn_id=?", (turn_id,)
            )

    def _maintain(self) -> None:
        now = self.now_ns()
        self.connection.execute(
            "UPDATE turns SET status='incomplete' WHERE status='open' AND opened_ns<=?",
            (now - 120_000_000_000,),
        )
        for (turn_id,) in self.connection.execute(
            "SELECT turn_id FROM turns WHERE status='closing'"
        ).fetchall():
            self._refresh_status(turn_id)
        expired = self.connection.execute(
            "SELECT pending_id,payload FROM pending WHERE arrival_ns<=? "
            "ORDER BY arrival_ns,pending_id",
            (now - 5_000_000_000,),
        ).fetchall()
        for pending_id, raw in expired:
            event = TimingEvent(**json.loads(raw))
            self._insert(
                replace(
                    event,
                    stage="unmatched",
                    data={**event.data, "original_stage": event.stage},
                )
            )
            self.connection.execute(
                "DELETE FROM pending WHERE pending_id=?", (pending_id,)
            )
        if expired:
            self._notify()

    async def maintain(self) -> None:
        await self._call(self._maintain_transaction)

    def _maintain_transaction(self) -> None:
        now = self.now_ns()
        retention_due = (
            self.last_retention_ns is None
            or now - self.last_retention_ns >= 60_000_000_000
        )
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            self._maintain()
            if retention_due:
                cutoff = now - self.retention_ns
                self.connection.execute(
                    "DELETE FROM events WHERE stored_ns<?", (cutoff,)
                )
                self.connection.execute(
                    "DELETE FROM turns WHERE opened_ns<?", (cutoff,)
                )
                self.connection.execute(
                    "DELETE FROM device_events WHERE stored_ns<?", (cutoff,)
                )
        if retention_due:
            self.connection.execute("PRAGMA wal_checkpoint(PASSIVE)")
            self.last_retention_ns = now

    async def open_turn(self, turn: TurnOpen) -> None:
        await self._call(self._open_turn, turn)

    def _open_turn(self, turn: TurnOpen) -> None:
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            self._maintain()
            if self.connection.execute(
                "SELECT 1 FROM turns WHERE turn_id=?", (turn.turn_id,)
            ).fetchone():
                raise TurnExistsError("turn already exists")
            if turn.parent_turn_id is not None:
                parent = self.connection.execute(
                    "SELECT satellite FROM turns WHERE turn_id=?",
                    (turn.parent_turn_id,),
                ).fetchone()
                if parent is None or parent[0] != turn.satellite:
                    raise ValueError("parent satellite mismatch")
            self.connection.execute(
                "UPDATE turns SET status='incomplete' "
                "WHERE satellite=? AND status IN ('open','closing')",
                (turn.satellite,),
            )
            self.connection.execute(
                "INSERT INTO turns VALUES(?,?,?,?, 'open',NULL)",
                (turn.turn_id, turn.satellite, json.dumps(asdict(turn)), self.now_ns()),
            )
            pending = self.connection.execute(
                "SELECT pending_id,payload FROM pending WHERE satellite=? "
                "ORDER BY pending_id",
                (turn.satellite,),
            ).fetchall()
            for pending_id, raw in pending:
                self._insert(
                    replace(TimingEvent(**json.loads(raw)), turn_id=turn.turn_id)
                )
                self.connection.execute(
                    "DELETE FROM pending WHERE pending_id=?", (pending_id,)
                )
        self._notify()

    def _sanitize(self, event: TimingEvent) -> TimingEvent:
        enabled = False
        if event.turn_id is not None:
            row = self.connection.execute(
                "SELECT satellite,payload FROM turns WHERE turn_id=?", (event.turn_id,)
            ).fetchone()
            if row is None or row[0] != event.satellite:
                raise ValueError("turn satellite mismatch")
            enabled = (
                self.record_transcripts and json.loads(row[1])["record_transcripts"]
            )
        return replace(event, data=safe_data(event, enabled))

    async def append(self, event: TimingEvent) -> None:
        await self._call(self._append, event)

    def _append(self, event: TimingEvent) -> None:
        if event.source == "device" and event.turn_id is None:
            self._append_device(event, self.now_ns())
            return
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            self._insert(self._sanitize(event))
        self._notify()

    async def append_device(self, event: TimingEvent, arrival_ns: int) -> None:
        await self._call(self._append_device, event, arrival_ns)

    def _append_device(self, event: TimingEvent, arrival_ns: int) -> None:
        if event.source != "device" or event.turn_id is not None:
            raise ValueError("unattached device event required")
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            self._append_device_rows(event, arrival_ns)
        self._notify()

    def _append_device_rows(self, event: TimingEvent, arrival_ns: int) -> None:
        row = self.connection.execute(
            "SELECT turn_id,opened_ns FROM turns "
            "WHERE satellite=? AND ((status='open' AND opened_ns>?) "
            "OR (status='closing' AND closed_ns>?)) "
            "ORDER BY opened_ns DESC LIMIT 1",
            (
                event.satellite,
                self.now_ns() - 120_000_000_000,
                self.now_ns() - 5_000_000_000,
            ),
        ).fetchone()
        event = replace(event, data=safe_data(event))
        if row is not None and row[1] < arrival_ns + 5_000_000_000:
            self._insert(replace(event, turn_id=row[0]))
        elif arrival_ns <= self.now_ns() - 5_000_000_000:
            self._insert(
                replace(
                    event,
                    stage="unmatched",
                    data={**event.data, "original_stage": event.stage},
                )
            )
        else:
            self.connection.execute(
                "INSERT INTO pending(satellite,arrival_ns,payload) VALUES(?,?,?)",
                (event.satellite, arrival_ns, self._encode(event)),
            )

    async def get_turn(self, turn_id: str) -> TurnRecord | None:
        return await self._call(self._get_turn, turn_id)

    def _get_turn(self, turn_id: str) -> TurnRecord | None:
        self._maintain_transaction()
        row = self.connection.execute(
            "SELECT payload,status FROM turns WHERE turn_id=?", (turn_id,)
        ).fetchone()
        if row is None:
            return None
        value: TurnRecord = json.loads(row[0])
        value["status"] = "open" if row[1] == "closing" else row[1]
        value["events"] = [
            json.loads(raw)
            for (raw,) in self.connection.execute(
                "SELECT payload FROM events WHERE turn_id=? ORDER BY event_id",
                (turn_id,),
            )
        ]
        return value

    async def export(
        self, satellite: str, since_utc_ns: int
    ) -> AsyncIterator[dict[str, Any]]:
        await self.maintain()
        cursor = 0
        while row := await self._call(
            self._next_event, satellite, cursor, since_utc_ns
        ):
            cursor = row[0]
            yield {"version": 1, "event_id": cursor, "event": json.loads(row[1])}

    async def events(
        self, satellite: str, after_event_id: int
    ) -> AsyncIterator[dict[str, Any]]:
        while True:
            self.changed.clear()
            while row := await self._call(
                self._next_event, satellite, after_event_id, 0
            ):
                after_event_id = row[0]
                yield {
                    "version": 1,
                    "event_id": after_event_id,
                    "event": json.loads(row[1]),
                }
            await self.changed.wait()

    def _next_event(
        self, satellite: str, cursor: int, since: int
    ) -> tuple[int, str] | None:
        return self.connection.execute(
            "SELECT event_id,payload FROM events WHERE satellite=? AND utc_ns>=? "
            "AND event_id>? ORDER BY event_id LIMIT 1",
            (satellite, since, cursor),
        ).fetchone()

    async def diagnostics(
        self, satellites: frozenset[str] | None = None
    ) -> dict[str, int]:
        return await self._call(self._diagnostics, satellites)

    def _diagnostics(self, satellites: frozenset[str] | None = None) -> dict[str, int]:
        clause = ""
        parameters: tuple[str, ...] = ()
        if satellites is not None:
            parameters = tuple(sorted(satellites))
            placeholders = ",".join("?" for _ in parameters)
            clause = f" AND satellite IN ({placeholders})"
        return {
            "events_total": self.connection.execute(
                "SELECT coalesce(sum(events_total),0) FROM counters WHERE 1=1" + clause,
                parameters,
            ).fetchone()[0],
            "pending_events": self.connection.execute(
                "SELECT count(*) FROM pending WHERE 1=1" + clause,
                parameters,
            ).fetchone()[0],
            "unmatched_events": self.connection.execute(
                "SELECT count(*) FROM events "
                "WHERE json_extract(payload, '$.stage')='unmatched'" + clause,
                parameters,
            ).fetchone()[0],
            "incomplete_turns": self.connection.execute(
                "SELECT count(*) FROM turns WHERE status='incomplete'" + clause,
                parameters,
            ).fetchone()[0],
        }

    def close(self) -> None:
        self.writer.submit(self.connection.close).result()
        self.writer.shutdown(wait=True)

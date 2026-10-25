import hashlib
import hmac
import json
import logging
import os
import struct
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol

from assist_live_client.models import identifier, unsigned

DEVICE_FIELDS = {"version", "device", "boot", "seq", "device_ms", "stage", "data"}
DEVICE_STAGES = {
    "wake_detected",
    "mic_open",
    "playback_start",
    "playback_end",
    "barge_in",
}
REPLAY_WINDOW = 4096
MAX_BOOT_IDS = 65536
REPLAY_MASK = (1 << REPLAY_WINDOW) - 1
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReplayState:
    boot: int | None = None
    high: int = -1
    bitmap: int = 0
    received: int = 0
    seen_boots: frozenset[int] = frozenset()
    duplicates: int = 0
    stale: int = 0
    previous_boot: int = 0
    boot_limit_errors: int = 0
    last_result: str = "none"


class ReplayStore(Protocol):
    def load_replay(self, device: str) -> ReplayState | None: ...
    def save_replay(self, device: str, state: ReplayState) -> None: ...
    def clear_replay(self, device: str) -> None: ...


def canonical_event(event: dict[str, Any]) -> bytes:
    if event.keys() != DEVICE_FIELDS or type(event["version"]) is not int:
        raise ValueError("invalid device fields")
    if event["version"] != 1 or event["stage"] not in DEVICE_STAGES:
        raise ValueError("invalid device version or stage")
    identifier(event["device"])
    if not isinstance(event["data"], dict):
        raise ValueError("invalid device data")
    data = json.dumps(
        event["data"],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")

    def field(value: bytes) -> bytes:
        return struct.pack("!I", len(value)) + value

    return (
        struct.pack("!B", event["version"])
        + field(event["device"].encode("utf-8"))
        + struct.pack(
            "!QQQ",
            unsigned(event["boot"]),
            unsigned(event["seq"]),
            unsigned(event["device_ms"]),
        )
        + field(event["stage"].encode("utf-8"))
        + field(data)
    )


def sign_event(event: dict[str, Any], secret: bytes) -> str:
    return hmac.new(secret, canonical_event(event), hashlib.sha256).hexdigest()


def verify_event(event: dict[str, Any], tag: object, secret: bytes) -> bool:
    try:
        return isinstance(tag, str) and hmac.compare_digest(
            sign_event(event, secret), tag
        )
    except ValueError, TypeError, KeyError, OverflowError, RecursionError:
        return False


class Registry:
    def __init__(
        self,
        path: Path | None = None,
        *,
        replay_store: ReplayStore | None = None,
        boot_limit: int = MAX_BOOT_IDS,
    ) -> None:
        if not 1 <= boot_limit <= MAX_BOOT_IDS:
            raise ValueError("invalid boot limit")
        self.path = path
        self.boot_limit = boot_limit
        self.replay_store = replay_store
        self.replay: dict[str, ReplayState] = {}
        self.credential_epochs: dict[str, int] = {}
        self.credentials: dict[str, tuple[str, bytes, int]] = {}
        self.devices: dict[str, dict[str, Any]] = {}
        self.tokens: dict[str, list[str]] = {}

    def bind(self, device: str, satellite: str, secret: bytes) -> None:
        identifier(device)
        identifier(satellite)
        if device in self.devices or any(
            row["satellite"] == satellite for row in self.devices.values()
        ):
            raise ValueError("duplicate or mismatched mapping")
        if not secret or any(
            hmac.compare_digest(t.encode(), secret) for t in self.tokens
        ):
            raise ValueError("invalid device credential")
        self.devices[device] = {
            "satellite": satellite,
            "secret": secret.hex(),
        }
        self.replay[device] = ReplayState()
        self.credential_epochs[device] = 0
        self.credentials[device] = (satellite, secret, 0)
        self.save()

    def add_token(self, token: str, satellites: list[str]) -> None:
        if (
            not token
            or not satellites
            or any(
                s not in {row["satellite"] for row in self.devices.values()}
                for s in satellites
            )
        ):
            raise ValueError("invalid integration credential")
        if any(
            hmac.compare_digest(token.encode(), bytes.fromhex(d["secret"]))
            for d in self.devices.values()
        ):
            raise ValueError("credential authorities must differ")
        self.tokens[token] = list(set(satellites))
        self.save()

    def authorize(self, token: str) -> frozenset[str] | None:
        for stored, satellites in self.tokens.items():
            if hmac.compare_digest(stored.encode(), token.encode()):
                return frozenset(satellites)
        return None

    def device(self, device: str) -> tuple[str, bytes] | None:
        row = self.credentials.get(device)
        return (row[0], row[1]) if row else None

    def device_snapshot(self, device: str) -> tuple[str, bytes, int] | None:
        return self.credentials.get(device)

    def rotate(self, device: str, secret: bytes) -> None:
        raise ValueError("use SQLiteStore.rotate for credential rotation")

    def _rotate(self, device: str, secret: bytes) -> None:
        row = self.devices[device]
        if hmac.compare_digest(bytes.fromhex(row["secret"]), secret):
            raise ValueError("new device credential required")
        if (
            not secret
            or any(hmac.compare_digest(t.encode(), secret) for t in self.tokens)
            or any(
                other != device and hmac.compare_digest(value[1], secret)
                for other, value in self.credentials.items()
            )
        ):
            raise ValueError("invalid device credential")
        previous = self.replay[device]
        if self.replay_store is not None:
            self.replay_store.clear_replay(device)
        old_secret = row["secret"]
        row.update(secret=secret.hex())
        try:
            self.save()
        except OSError:
            row["secret"] = old_secret
            if self.replay_store is not None:
                self.replay_store.save_replay(device, previous)
            raise
        self.replay[device] = ReplayState()
        self.credential_epochs[device] = self.credential_epochs.get(device, 0) + 1
        self.credentials[device] = (
            row["satellite"],
            secret,
            self.credential_epochs[device],
        )

    def accept_sequence(
        self, device: str, boot: int, seq: int, *, credential_epoch: int | None = None
    ) -> bool:
        if (
            credential_epoch is not None
            and credential_epoch != self.credential_epochs.get(device, 0)
        ):
            return False
        unsigned(boot)
        unsigned(seq)
        state = self.replay[device]
        if boot != state.boot and boot in state.seen_boots:
            updated = replace(
                state,
                previous_boot=state.previous_boot + 1,
                last_result="previous_boot",
            )
        elif boot != state.boot and len(state.seen_boots) >= self.boot_limit:
            if state.boot_limit_errors == 0:
                LOGGER.error("device reboot limit reached; rotate device credentials")
            updated = replace(
                state,
                boot_limit_errors=state.boot_limit_errors + 1,
                last_result="boot_limit",
            )
        else:
            if boot != state.boot:
                state = replace(
                    state,
                    boot=boot,
                    high=-1,
                    bitmap=0,
                    received=0,
                    seen_boots=state.seen_boots | {boot},
                )
            distance = state.high - seq
            if distance >= REPLAY_WINDOW:
                updated = replace(state, stale=state.stale + 1, last_result="stale")
            elif distance >= 0 and state.bitmap & (1 << distance):
                updated = replace(
                    state, duplicates=state.duplicates + 1, last_result="duplicate"
                )
            else:
                if seq > state.high:
                    advance = seq - state.high
                    bitmap = (
                        1
                        if advance >= REPLAY_WINDOW
                        else (((state.bitmap << advance) | 1) & REPLAY_MASK)
                    )
                else:
                    bitmap = state.bitmap | (1 << distance)
                updated = replace(
                    state,
                    high=max(seq, state.high),
                    bitmap=bitmap,
                    received=state.received + 1,
                    last_result="accepted",
                )
        try:
            if self.replay_store is not None:
                self.replay_store.save_replay(device, updated)
        except OSError:
            LOGGER.error("replay state persistence failed")
            return False
        self.replay[device] = updated
        return updated.last_result == "accepted"

    def missing(self, device: str) -> int:
        state = self.replay[device]
        return state.high + 1 - state.received

    def save(self) -> None:
        if self.path is None:
            return
        fd, name = tempfile.mkstemp(prefix=".credentials-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w") as output:
                os.fchmod(output.fileno(), 0o600)
                json.dump({"devices": self.devices, "tokens": self.tokens}, output)
                output.flush()
                os.fsync(output.fileno())
            os.replace(name, self.path)
            directory = os.open(self.path.parent, os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    @classmethod
    def load(cls, path: Path, *, replay_store: ReplayStore | None = None) -> Registry:
        if path.stat().st_mode & 0o077:
            raise ValueError("credential file must have mode 600")
        registry = cls(path, replay_store=replay_store)
        value = json.loads(path.read_text())
        registry.devices = value["devices"]
        registry.tokens = value["tokens"]
        for device in registry.devices:
            row = registry.devices[device]
            registry.credentials[device] = (
                row["satellite"],
                bytes.fromhex(row["secret"]),
                0,
            )
            state = (
                replay_store.load_replay(device) if replay_store is not None else None
            )
            registry.replay[device] = state or ReplayState()
        return registry

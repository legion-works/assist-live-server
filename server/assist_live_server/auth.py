import hashlib
import hmac
import json
import logging
import os
import struct
import tempfile
from collections.abc import Iterator, MutableMapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol

from assist_live_client.models import event_data, identifier, unsigned

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
    event_data(event["stage"], event["data"])
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


def fingerprint(secret: bytes) -> str:
    # The store keeps this value to identify the active key. It never keeps the key.
    return hashlib.sha256(b"assist-live device credential\0" + secret).hexdigest()


@dataclass(frozen=True)
class Credential:
    satellite: str
    secret: bytes
    epoch: int
    replay: ReplayState
    blocked: bool = False


class ReplayView(MutableMapping[str, ReplayState]):
    """Read and replace the replay state inside each credential record."""

    def __init__(self, credentials: dict[str, Credential]) -> None:
        self._credentials = credentials

    def __getitem__(self, device: str) -> ReplayState:
        return self._credentials[device].replay

    def __setitem__(self, device: str, state: ReplayState) -> None:
        self._credentials[device] = replace(self._credentials[device], replay=state)

    def __delitem__(self, device: str) -> None:
        raise TypeError("replay state belongs to its credential")

    def __iter__(self) -> Iterator[str]:
        return iter(self._credentials)

    def __len__(self) -> int:
        return len(self._credentials)


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
        # Each device has one immutable record. A rotation replaces the key, the
        # epoch, and the replay state with one assignment.
        self.credentials: dict[str, Credential] = {}
        self.replay = ReplayView(self.credentials)
        self.devices: dict[str, dict[str, Any]] = {}
        self.tokens: dict[str, list[str]] = {}

    @property
    def credential_epochs(self) -> dict[str, int]:
        return {device: row.epoch for device, row in self.credentials.items()}

    def bind(
        self, device: str, satellite: str, secret: bytes, *, save: bool = True
    ) -> None:
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
        self.credentials[device] = Credential(satellite, secret, 0, ReplayState())
        if save:
            self.save()

    def add_token(
        self, token: str, satellites: list[str], *, save: bool = True
    ) -> None:
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
        if save:
            self.save()

    def authorize(self, token: str) -> frozenset[str] | None:
        for stored, satellites in self.tokens.items():
            if hmac.compare_digest(stored.encode(), token.encode()):
                return frozenset(satellites)
        return None

    def device(self, device: str) -> tuple[str, bytes] | None:
        row = self.credentials.get(device)
        return (row.satellite, row.secret) if row and not row.blocked else None

    def device_snapshot(self, device: str) -> tuple[str, bytes, int] | None:
        row = self.credentials.get(device)
        if row is None or row.blocked:
            return None
        return row.satellite, row.secret, row.epoch

    def blocked_devices(self) -> list[str]:
        return sorted(device for device, row in self.credentials.items() if row.blocked)

    def pending_satellites(self) -> list[str]:
        return sorted(
            row["satellite"] for row in self.devices.values() if "pending" in row
        )

    def rotate(self, device: str, secret: bytes) -> None:
        raise ValueError("use SQLiteStore.rotate for credential rotation")

    def _check_rotation(self, device: str, secret: bytes) -> None:
        row = self.devices[device]
        if hmac.compare_digest(bytes.fromhex(row["secret"]), secret):
            raise ValueError("new device credential required")
        if (
            not secret
            or any(hmac.compare_digest(t.encode(), secret) for t in self.tokens)
            or any(
                other != device and hmac.compare_digest(value.secret, secret)
                for other, value in self.credentials.items()
            )
        ):
            raise ValueError("invalid device credential")

    def _write_pending(self, device: str, secret: bytes, epoch: int) -> None:
        # Readers that know only "satellite" and "secret" keep the active key.
        row = self.devices[device]
        row["pending"] = {"secret": secret.hex(), "epoch": epoch}
        try:
            self.save()
        except BaseException:
            row.pop("pending", None)
            raise

    def _drop_pending(self, device: str) -> None:
        if self.devices[device].pop("pending", None) is not None:
            self.save()

    def _promote_pending(self, device: str) -> None:
        row = self.devices[device]
        pending = row.pop("pending")
        row["secret"] = pending["secret"]
        row["epoch"] = pending["epoch"]
        self.save()

    def _publish(self, device: str, secret: bytes, epoch: int) -> None:
        satellite = self.credentials[device].satellite
        self.credentials[device] = Credential(satellite, secret, epoch, ReplayState())

    def _block(self, device: str) -> None:
        self.credentials[device] = replace(self.credentials[device], blocked=True)

    def accept_sequence(
        self, device: str, boot: int, seq: int, *, credential_epoch: int | None = None
    ) -> bool:
        credential = self.credentials[device]
        if credential.blocked or (
            credential_epoch is not None and credential_epoch != credential.epoch
        ):
            return False
        unsigned(boot)
        unsigned(seq)
        state = credential.replay
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
            epoch = row.get("epoch", 0)
            if type(epoch) is not int or epoch < 0:
                raise ValueError("invalid credential epoch")
            state = (
                replay_store.load_replay(device) if replay_store is not None else None
            )
            registry.credentials[device] = Credential(
                row["satellite"],
                bytes.fromhex(row["secret"]),
                epoch,
                state or ReplayState(),
            )
        return registry

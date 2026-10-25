"""Replay frozen partial trajectories with local audio and turn models.

TRAJECTORY_SCHEMA is the producer contract for JSON Lines partials. Each row
joins a label by the corpus label's id. Labels carry split, audio_sha256,
speaker_type (or a speaker_id prefix), and set. Trajectory rows carry the STT
identity, which must agree with the frozen declaration. Audio replay uses
audio_path. Pause rows need pause_intervals_ms. Executable-prefix rows also
need executable_prefix_intervals_ms. Replay rows need no interval fields. Intervals are
[start_ms, end_ms] pairs. A pause error is an endpoint inside a pause interval.
An executable-prefix error is an endpoint inside a prefix interval.

The command declares the frozen STT identity with --stt-model-id,
--stt-revision, and --provider-config-sha256. Turn-model pins come from the
asset manifest. Tuning consumes tuning rows only. Freeze requires at least
300 distinct held-out human pause recordings, but does not score them.
"""

import argparse
import hashlib
import json
import math
import os
import sys
import wave
from collections.abc import Callable
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from assist_live_server.turn.detector import (
    ModelRuntime,
    TurnDetector,
    fetch_models,
    model_records,
)
from assist_live_server.turn.profiles import PROFILES, Profile

ROOT = Path(__file__).resolve().parents[1]
FROZEN = ("stt_model_id", "stt_revision", "provider_config_sha256")
TRAJECTORY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["recording_id", "split", "audio_sha256", *FROZEN, "partials"],
    "additionalProperties": False,
    "properties": {
        "recording_id": {"type": "string", "minLength": 1},
        "split": {"enum": ["tuning", "held_out"]},
        "audio_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        "stt_model_id": {"type": "string", "minLength": 1},
        "stt_revision": {"type": "string", "minLength": 1},
        "provider_config_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        "partials": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["audio_ms", "text"],
                "properties": {
                    "audio_ms": {"type": "number", "minimum": 0},
                    "text": {"type": "string"},
                },
            },
        },
    },
}
Evidence = dict[str, Any]
Replay = Callable[[Evidence, Profile], list[dict[str, Any]]]


def validate_trajectory(
    row: Evidence, labels: dict[str, Evidence], frozen: Evidence
) -> None:
    if set(row) != set(TRAJECTORY_SCHEMA["required"]):
        raise ValueError("trajectory fields differ from schema")
    if row["recording_id"] not in labels:
        raise ValueError("trajectory recording missing from labels")
    label = labels[row["recording_id"]]
    if row["split"] not in ("tuning", "held_out") or row["split"] != label["split"]:
        raise ValueError("trajectory split differs from labels")
    for field, description in (
        ("audio_sha256", "audio hash"),
        ("stt_model_id", "STT model"),
        ("stt_revision", "STT revision"),
        ("provider_config_sha256", "provider configuration"),
    ):
        value = row[field]
        if (
            not isinstance(value, str)
            or not value
            or (field not in FROZEN and field not in label)
            or (field not in FROZEN and value != label.get(field))
            or (field in FROZEN and value != frozen[field])
        ):
            raise ValueError(
                f"trajectory {description} differs from labels or frozen declaration"
            )
        if field.endswith("sha256") and (
            len(value) != 64 or any(c not in "0123456789abcdef" for c in value)
        ):
            raise ValueError(f"invalid {description}")
    previous = -1.0
    if not isinstance(row["partials"], list) or not row["partials"]:
        raise ValueError("trajectory needs partials")
    for partial in row["partials"]:
        if set(partial) != {"audio_ms", "text"} or not isinstance(partial["text"], str):
            raise ValueError("invalid partial fields")
        timestamp = partial["audio_ms"]
        if (
            type(timestamp) not in (int, float)
            or not math.isfinite(timestamp)
            or timestamp < 0
        ):
            raise ValueError("invalid partial timestamp")
        if timestamp < previous:
            raise ValueError("partial timestamps must be monotonic")
        previous = timestamp


def read_rows(path: Path) -> list[Evidence]:
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    if not rows:
        raise ValueError("evidence file is empty")
    for row in rows:
        if "id" in row:
            if "recording_id" in row and row["recording_id"] != row["id"]:
                raise ValueError("conflicting recording id fields")
            row["recording_id"] = row["id"]
        if not isinstance(row.get("recording_id"), str) or not row["recording_id"]:
            raise ValueError("missing recording id")
    if len({row["recording_id"] for row in rows}) != len(rows):
        raise ValueError("duplicate recording id")
    return rows


def validate_partials(labels: Path, partials: Path, frozen: Evidence) -> list[Evidence]:
    label_rows = read_rows(labels)
    trajectories = read_rows(partials)
    indexed = {row["recording_id"]: row for row in label_rows}
    if {row["recording_id"] for row in trajectories} != set(indexed):
        raise ValueError("trajectory recording missing from labels or partials")
    result = []
    for row in trajectories:
        validate_trajectory(row, indexed, frozen)
        result.append(policy_labels({**indexed[row["recording_id"]], **row}))
    return result


def speaker_kind(row: Evidence) -> str:
    speaker = row.get("speaker_id", "")
    if not isinstance(speaker, str):
        raise ValueError("invalid speaker identifier")
    inferred = (
        "human"
        if speaker.startswith("speaker-")
        else "synthetic"
        if speaker.startswith("synthetic-")
        else None
    )
    kind = row.get("speaker_type", inferred)
    if kind not in ("human", "synthetic") or (
        inferred is not None and kind != inferred
    ):
        raise ValueError("invalid or missing speaker kind")
    return str(kind)


def policy_labels(row: Evidence, duration_ms: float | None = None) -> Evidence:
    row["speaker_type"] = speaker_kind(row)
    if row.get("set") not in ("replay", "pause"):
        raise ValueError("invalid or missing corpus set")
    prefix = row.get("executable_prefix", False)
    if type(prefix) is not bool or (prefix and row["set"] != "pause"):
        raise ValueError("executable prefix must belong to a pause row")
    for field, required, description in (
        ("pause_intervals_ms", row["set"] == "pause", "pause"),
        ("executable_prefix_intervals_ms", prefix, "prefix"),
    ):
        intervals = row.get(field, [])
        if required and not intervals:
            raise ValueError(f"{description} interval labels missing")
        if not isinstance(intervals, list) or (not required and intervals):
            raise ValueError(f"invalid {description} interval labels")
        for interval in intervals:
            if (
                not isinstance(interval, list)
                or len(interval) != 2
                or any(
                    type(v) not in (int, float) or not math.isfinite(v)
                    for v in interval
                )
                or not 0 <= interval[0] < interval[1]
                or (duration_ms is not None and interval[1] > duration_ms)
            ):
                raise ValueError(f"invalid {description} interval labels")
        row[field] = intervals
    return row


def load_evidence(
    labels: Path, audio_root: Path, partials: Path, frozen: Evidence
) -> list[Evidence]:
    result = []
    for row in validate_partials(labels, partials, frozen):
        label = row
        path = (audio_root / label["audio_path"]).resolve()
        if not path.is_relative_to(audio_root.resolve()):
            raise ValueError("audio path must stay inside audio root")
        if hashlib.sha256(path.read_bytes()).hexdigest() != row["audio_sha256"]:
            raise ValueError("audio hash differs from labels")
        with wave.open(str(path), "rb") as wav:
            if (
                wav.getnchannels(),
                wav.getsampwidth(),
                wav.getframerate(),
                wav.getcomptype(),
            ) != (1, 2, 16000, "NONE"):
                raise ValueError("audio must be mono 16 kHz int16 PCM WAV")
            pcm = wav.readframes(wav.getnframes())
        duration = len(pcm) / 32
        if row["partials"][-1]["audio_ms"] > duration:
            raise ValueError("partial timestamp exceeds audio duration")
        policy_labels(label, duration)
        result.append({**label, "partials": row["partials"], "pcm": pcm})
    return result


def replay(
    row: Evidence, profile: Profile, cache: Path, manifest: Path
) -> list[dict[str, Any]]:
    detector = TurnDetector(profile, models=ModelRuntime(cache, manifest))
    partials = iter(row["partials"])
    upcoming = next(partials, None)
    partial = ""
    pcm = row["pcm"]
    for offset in range(0, len(pcm), 1024):
        # A partial is visible only when its audio timestamp has arrived.
        start_ms = offset / 32
        while upcoming is not None and upcoming["audio_ms"] <= start_ms:
            partial = upcoming["text"]
            upcoming = next(partials, None)
        events = detector.push(pcm[offset : offset + 1024], partial)
        if events:
            return events
    return []


def errors(
    rows: list[Evidence], profile: Profile, run: Replay, field: str
) -> tuple[int, int]:
    relevant = [row for row in rows if row[field]]
    count = sum(
        any(
            start <= event["audio_ms"] < end
            for event in run(row, profile)
            for start, end in row[field]
        )
        for row in relevant
    )
    return count, len(relevant)


def tune(rows: list[Evidence], names: list[str], run: Replay) -> dict[str, Profile]:
    if not rows or any(row["split"] != "tuning" for row in rows):
        raise ValueError("tuning accepts tuning rows only")
    selected = {}
    for name in names:
        if name not in PROFILES:
            raise ValueError("unknown profile")
        candidates = [
            replace(PROFILES[name], min_silence_ms=minimum, smart_turn_limit=limit)
            for minimum in (400, 600, 800, 1000)
            for limit in (0.8, 0.9, 0.95)
        ]

        def score(profile: Profile, name: str = name) -> tuple[float, ...]:
            pause, _ = errors(rows, profile, run, "pause_intervals_ms")
            prefix, _ = errors(rows, profile, run, "executable_prefix_intervals_ms")
            return (
                pause,
                prefix if name == "safe" else 0,
                profile.min_silence_ms,
                profile.smart_turn_limit,
            )

        selected[name] = min(candidates, key=score)
        if (
            name == "safe"
            and errors(rows, selected[name], run, "executable_prefix_intervals_ms")[0]
        ):
            selected[name] = replace(selected[name], early_enabled=False)
    return selected


def pause_denominator(rows: list[Evidence]) -> int:
    return len(
        {
            row["recording_id"]
            for row in rows
            if row["split"] == "held_out"
            and row.get("set") == "pause"
            and speaker_kind(row) == "human"
            and row.get("pause_intervals_ms")
        }
    )


def freeze_profiles(
    profiles: dict[str, Profile],
    rows: list[Evidence],
    output: Path,
    frozen: Evidence,
    turn_models: Evidence,
) -> None:
    if pause_denominator(rows) < 300:
        raise ValueError(
            "freeze needs at least 300 distinct held_out human pause recordings"
        )
    document = {
        "schema": 1,
        "frozen_stt": frozen,
        "turn_models": turn_models,
        "held_out_evidence_sha256": evidence_digest(
            [row for row in rows if row["split"] == "held_out"]
        ),
        "profiles": {name: asdict(profile) for name, profile in profiles.items()},
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(document, indent=2) + "\n")


def evidence_digest(rows: list[Evidence]) -> str:
    fields = [
        *TRAJECTORY_SCHEMA["required"],
        "speaker_id",
        "speaker_type",
        "set",
        "executable_prefix",
        "pause_intervals_ms",
        "executable_prefix_intervals_ms",
    ]
    identities = [
        {field: row.get(field) for field in fields}
        for row in sorted(rows, key=lambda row: row["recording_id"])
    ]
    return hashlib.sha256(
        json.dumps(identities, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def upper95(count: int, total: int) -> float:
    z = 1.959963984540054
    rate = count / total
    return (
        rate
        + z * z / (2 * total)
        + z * math.sqrt(rate * (1 - rate) / total + z * z / (4 * total * total))
    ) / (1 + z * z / total)


def check_held_out(
    rows: list[Evidence],
    profiles_file: Path,
    run: Replay,
    frozen: Evidence,
    turn_models: Evidence,
) -> list[str]:
    if not rows or any(row["split"] != "held_out" for row in rows):
        raise ValueError("gate accepts held_out rows only")
    if pause_denominator(rows) < 300:
        raise ValueError("gate needs 300 held_out human pause recordings")
    document = json.loads(profiles_file.read_text())
    if document["frozen_stt"] != frozen or document["turn_models"] != turn_models:
        raise ValueError("frozen model revision or configuration differs")
    if document["held_out_evidence_sha256"] != evidence_digest(rows):
        raise ValueError("held_out evidence differs from frozen recording identities")
    profiles = {name: Profile(**value) for name, value in document["profiles"].items()}
    if set(profiles) != {"safe", "fast"}:
        raise ValueError("gate needs safe and fast profiles")
    human = [row for row in rows if speaker_kind(row) == "human"]
    for name, profile in profiles.items():
        count, total = errors(human, profile, run, "pause_intervals_ms")
        if count / total > 0.03 or upper95(count, total) > 0.06:
            raise ValueError(f"FAIL {name} pause rate or upper95 exceeds limit")
    count, total = errors(rows, profiles["safe"], run, "executable_prefix_intervals_ms")
    if not total or count:
        raise ValueError(
            "FAIL safe prefix errors; disable safe early endpointing "
            "and rerun constant policy"
        )
    return ["PASS pause rate<=3% upper95<=6%", "PASS safe prefix errors=0"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path(
            os.environ.get("ASSIST_LIVE_ASSET_MANIFEST", ROOT / "assets/manifest.json")
        ),
    )
    parser.add_argument(
        "--model-cache",
        type=Path,
        default=Path(
            os.environ.get(
                "ASSIST_LIVE_MODEL_CACHE", Path.home() / ".cache/assist-live/turn"
            )
        ),
    )
    parser.add_argument("--fetch-models", action="store_true")
    parser.add_argument("--validate-partials", action="store_true")
    parser.add_argument("--labels", type=Path)
    parser.add_argument("--audio-root", type=Path)
    parser.add_argument("--partials", type=Path)
    parser.add_argument("--split", choices=("tuning", "held_out"))
    parser.add_argument("--profile", action="append", choices=("safe", "fast"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--profiles-file", type=Path)
    parser.add_argument("--freeze", action="store_true")
    parser.add_argument("--disable-safe-early", action="store_true")
    parser.add_argument("--check-held-out", action="store_true")
    parser.add_argument("--stt-model-id")
    parser.add_argument("--stt-revision")
    parser.add_argument("--provider-config-sha256")
    args = parser.parse_args(argv)
    try:
        if args.disable_safe_early:
            if (
                not args.profiles_file
                or not args.output
                or any(
                    (
                        args.freeze,
                        args.check_held_out,
                        args.fetch_models,
                        args.validate_partials,
                        args.split,
                        args.profile,
                        args.labels,
                        args.partials,
                        args.audio_root,
                    )
                )
            ):
                raise ValueError(
                    "constant safety fallback needs only profiles file and output"
                )
            document = json.loads(args.profiles_file.read_text())
            for value in document["profiles"].values():
                Profile(**value)
            document["profiles"]["safe"]["early_enabled"] = False
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(document, indent=2) + "\n")
            print(
                "PASS constant safety fallback: safe early_enabled=false; "
                "rerun held_out gate"
            )
            return 0
        if args.validate_partials:
            frozen = {field: getattr(args, field) for field in FROZEN}
            if not all(frozen.values()) or not args.labels or not args.partials:
                raise ValueError(
                    "partial validation needs labels, partials, and frozen STT identity"
                )
            if any(
                (
                    args.fetch_models,
                    args.freeze,
                    args.check_held_out,
                    args.output,
                    args.profiles_file,
                    args.profile,
                    args.split,
                    args.audio_root,
                )
            ):
                raise ValueError(
                    "partial validation cannot tune, replay, or write profiles"
                )
            rows = validate_partials(args.labels, args.partials, frozen)
            print(
                f"PASS partial trajectories rows={len(rows)} "
                f"tuning={sum(row['split'] == 'tuning' for row in rows)} "
                f"held_out={sum(row['split'] == 'held_out' for row in rows)}"
            )
            return 0
        if args.fetch_models:
            fetch_models(args.model_cache, args.manifest)
            print("PASS pinned turn models fetched with SHA-256 checks")
            return 0
        frozen = {field: getattr(args, field) for field in FROZEN}
        if not all(frozen.values()) or not all(
            (args.labels, args.audio_root, args.partials, args.split)
        ):
            raise ValueError(
                "labels, audio root, partials, split, and frozen STT identity required"
            )
        if args.check_held_out:
            if (
                args.split != "held_out"
                or args.freeze
                or args.output
                or args.profile
                or not args.profiles_file
            ):
                raise ValueError(
                    "held_out check cannot tune, freeze, or write profiles"
                )
        elif args.split != "tuning" or args.profiles_file:
            raise ValueError("tuning accepts tuning split only")
        rows = load_evidence(args.labels, args.audio_root, args.partials, frozen)
        selected_rows = [row for row in rows if row["split"] == args.split]
        pins = {
            name: {key: record[key] for key in ("revision", "sha256")}
            for name, record in model_records(args.manifest).items()
        }

        def run(row: Evidence, profile: Profile) -> list[dict[str, Any]]:
            return replay(row, profile, args.model_cache, args.manifest)

        if args.check_held_out:
            for line in check_held_out(
                selected_rows, args.profiles_file, run, frozen, pins
            ):
                print(line)
        else:
            if args.freeze and pause_denominator(rows) < 300:
                raise ValueError(
                    "freeze needs at least 300 distinct held_out human pause recordings"
                )
            profiles = tune(selected_rows, args.profile or ["safe", "fast"], run)
            if args.freeze:
                if not args.output:
                    raise ValueError("freeze requires output")
                freeze_profiles(profiles, rows, args.output, frozen, pins)
            elif args.output:
                raise ValueError(
                    "output requires freeze; unfrozen values go to stdout only"
                )
            print(
                json.dumps(
                    {name: asdict(profile) for name, profile in profiles.items()},
                    sort_keys=True,
                )
            )
        return 0
    except (ValueError, KeyError, OSError, TypeError, wave.Error) as error:
        print(f"FAIL profiles: {error}")
        return 1


if __name__ == "__main__":
    sys.exit(main())

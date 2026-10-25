import copy
import hashlib
import json
import wave
from dataclasses import replace

import pytest
from assist_live_server.turn.detector import TurnDetector
from assist_live_server.turn.profiles import PROFILES, Profile

from tools.tune_profiles import (
    FROZEN,
    TRAJECTORY_SCHEMA,
    check_held_out,
    freeze_profiles,
    load_evidence,
    tune,
    validate_trajectory,
)


@pytest.fixture
def evidence(tmp_path):
    path = tmp_path / "sample.wav"
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(bytes(32000))
    label = {
        "recording_id": "synthetic-1",
        "split": "tuning",
        "audio_path": "sample.wav",
        "audio_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "stt_model_id": "test-stt",
        "stt_revision": "frozen-revision",
        "provider_config_sha256": "a" * 64,
        "human": False,
        "pause_intervals_ms": [[100, 900]],
        "executable_prefix_intervals_ms": [[100, 900]],
    }
    row = {
        key: label[key]
        for key in (
            "recording_id",
            "split",
            "audio_sha256",
            "stt_model_id",
            "stt_revision",
            "provider_config_sha256",
        )
    }
    row["partials"] = [
        {"audio_ms": 0, "text": "turn on the"},
        {"audio_ms": 950, "text": "turn on lights"},
    ]
    frozen = {key: row[key] for key in FROZEN}
    return label, row, frozen


def test_profiles_keep_defaults_and_safe_fallback():
    for profile in PROFILES.values():
        assert profile.min_silence_ms == 400
        assert profile.max_silence_ms == 1500
        assert not TurnDetector(
            profile, vad_probability=lambda _: 0, turn_probability=lambda _: 1
        ).candidate(500, 1, "the")
        disabled = replace(profile, early_enabled=False)
        assert not TurnDetector(
            disabled, vad_probability=lambda _: 0, turn_probability=lambda _: 1
        ).candidate(500, 1, "lights")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"min_silence_ms": 0},
        {"max_silence_ms": 15001},
        {"min_silence_ms": 1501},
        {"smart_turn_limit": float("nan")},
        {"smart_turn_limit": 2},
        {"early_enabled": "yes"},
    ],
)
def test_invalid_profile_is_rejected(kwargs):
    with pytest.raises(ValueError):
        Profile(**kwargs)


def test_declared_schema_and_valid_trajectory(evidence):
    label, row, frozen = evidence
    assert set(FROZEN) <= set(TRAJECTORY_SCHEMA["required"])
    validate_trajectory(row, {label["recording_id"]: label}, frozen)


@pytest.mark.parametrize(
    "field,value,error",
    [
        ("split", "test", "split"),
        ("split", "held_out", "split"),
        ("audio_sha256", "b" * 64, "hash"),
        ("stt_revision", "other", "revision"),
        ("stt_model_id", "other", "model"),
        ("provider_config_sha256", "b" * 64, "configuration"),
        ("recording_id", "absent", "missing"),
        (
            "partials",
            [{"audio_ms": 10, "text": "the"}, {"audio_ms": 5, "text": "lights"}],
            "monotonic",
        ),
        ("partials", [{"audio_ms": -1, "text": "the"}], "timestamp"),
    ],
)
def test_rejects_bad_trajectory(evidence, field, value, error):
    label, row, frozen = evidence
    row[field] = value
    with pytest.raises(ValueError, match=error):
        validate_trajectory(row, {label["recording_id"]: label}, frozen)


def test_declared_revision_must_match_labels(evidence):
    label, row, frozen = evidence
    frozen["stt_revision"] = "different"
    with pytest.raises(ValueError, match="revision"):
        validate_trajectory(row, {label["recording_id"]: label}, frozen)


def test_load_checks_actual_audio_and_duplicate_rows(tmp_path, evidence):
    label, row, frozen = evidence
    labels = tmp_path / "labels.jsonl"
    partials = tmp_path / "partials.jsonl"
    labels.write_text(json.dumps(label) + "\n")
    partials.write_text(json.dumps(row) + "\n")
    rows = load_evidence(labels, tmp_path, partials, frozen)
    assert len(rows) == 1
    (tmp_path / "sample.wav").write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="hash"):
        load_evidence(labels, tmp_path, partials, frozen)
    partials.write_text((json.dumps(row) + "\n") * 2)
    with pytest.raises(ValueError, match="duplicate"):
        load_evidence(labels, tmp_path, partials, frozen)


def test_tuning_rejects_held_out_without_running_model():
    with pytest.raises(ValueError, match="tuning"):
        tune(
            [
                {
                    "split": "held_out",
                    "pause_intervals_ms": [],
                    "executable_prefix_intervals_ms": [],
                }
            ],
            ["safe"],
            lambda *_: [],
        )


def test_freeze_requires_held_out_human_pause_denominator(tmp_path):
    output = tmp_path / "profiles.json"
    with pytest.raises(ValueError, match="300"):
        freeze_profiles(PROFILES, [], output, {}, {})
    assert not output.exists()


def test_synthetic_and_duplicate_rows_cannot_fill_denominator(tmp_path):
    rows = [
        {
            "recording_id": str(i),
            "split": "held_out",
            "human": False,
            "pause_intervals_ms": [[1, 2]],
        }
        for i in range(300)
    ]
    with pytest.raises(ValueError, match="300"):
        freeze_profiles(PROFILES, rows, tmp_path / "profiles.json", {}, {})
    rows = [
        {
            "recording_id": "same",
            "split": "held_out",
            "human": True,
            "pause_intervals_ms": [[1, 2]],
        }
    ] * 300
    with pytest.raises(ValueError, match="300"):
        freeze_profiles(PROFILES, rows, tmp_path / "profiles.json", {}, {})


def test_tune_never_consumes_held_out_and_disables_unsafe_safe():
    def replay(row, profile):
        return [{"audio_ms": 500}] if profile.early_enabled else []

    rows = [
        {
            "split": "tuning",
            "pause_intervals_ms": [],
            "executable_prefix_intervals_ms": [[400, 600]],
        }
    ]
    profiles = tune(rows, ["safe", "fast"], replay)
    assert not profiles["safe"].early_enabled
    assert profiles["fast"].early_enabled


def test_held_out_does_not_rewrite_or_tune(tmp_path):
    output = tmp_path / "profiles.json"
    rows = [
        {
            "recording_id": str(i),
            "split": "held_out",
            "human": True,
            "pause_intervals_ms": [[100, 200]],
            "executable_prefix_intervals_ms": [[100, 200]],
        }
        for i in range(300)
    ]
    freeze_profiles(PROFILES, rows, output, {"stt_revision": "pin"}, {"model": "pin"})
    before = output.read_bytes()
    assert check_held_out(
        rows, output, lambda *_: [], {"stt_revision": "pin"}, {"model": "pin"}
    ) == ["PASS pause rate<=3% upper95<=6%", "PASS safe prefix errors=0"]
    assert output.read_bytes() == before
    with pytest.raises(ValueError, match="revision"):
        check_held_out(
            rows, output, lambda *_: [], {"stt_revision": "changed"}, {"model": "pin"}
        )
    with pytest.raises(ValueError, match="held_out"):
        check_held_out(
            [{"split": "tuning"}],
            output,
            lambda *_: [],
            {"stt_revision": "pin"},
            {"model": "pin"},
        )
    bad = copy.deepcopy(rows)
    with pytest.raises(ValueError, match="pause|prefix"):
        check_held_out(
            bad,
            output,
            lambda *_: [{"audio_ms": 150}],
            {"stt_revision": "pin"},
            {"model": "pin"},
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("recording_id", "substituted"),
        ("audio_sha256", "changed"),
        ("partials", [{"audio_ms": 10, "text": "changed"}]),
    ],
)
def test_frozen_gate_rejects_evidence_substitution(tmp_path, field, value):
    rows = [
        {
            "recording_id": str(i),
            "split": "held_out",
            "human": True,
            "audio_sha256": "a" * 64,
            "partials": [{"audio_ms": 0, "text": "the"}],
            "pause_intervals_ms": [[100, 200]],
            "executable_prefix_intervals_ms": [[100, 200]],
        }
        for i in range(300)
    ]
    output = tmp_path / "profiles.json"
    freeze_profiles(PROFILES, rows, output, {}, {})
    rows[0][field] = value
    with pytest.raises(ValueError, match="evidence"):
        check_held_out(rows, output, lambda *_: [], {}, {})


def test_cli_refuses_tuning_on_held_out(tmp_path, capsys):
    from tools.tune_profiles import main

    assert (
        main(
            [
                "--labels",
                "labels.jsonl",
                "--audio-root",
                str(tmp_path),
                "--partials",
                "partials.jsonl",
                "--split",
                "held_out",
                "--stt-model-id",
                "test",
                "--stt-revision",
                "pin",
                "--provider-config-sha256",
                "a" * 64,
            ]
        )
        == 1
    )
    assert "tuning accepts tuning split only" in capsys.readouterr().out


def test_prefix_fallback_changes_only_safe_early_flag(tmp_path):
    from tools.tune_profiles import main

    source = tmp_path / "frozen.json"
    output = tmp_path / "fallback.json"
    rows = [
        {
            "recording_id": str(i),
            "split": "held_out",
            "human": True,
            "pause_intervals_ms": [[1, 2]],
        }
        for i in range(300)
    ]
    freeze_profiles(PROFILES, rows, source, {"stt_revision": "pin"}, {"model": "pin"})
    before = source.read_bytes()
    assert (
        main(
            [
                "--disable-safe-early",
                "--profiles-file",
                str(source),
                "--output",
                str(output),
            ]
        )
        == 0
    )
    assert source.read_bytes() == before
    expected = json.loads(before)
    expected["profiles"]["safe"]["early_enabled"] = False
    assert json.loads(output.read_bytes()) == expected

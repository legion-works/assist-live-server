import copy
import hashlib
import json
import wave
from dataclasses import replace
from pathlib import Path

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
        "speaker_type": "synthetic",
        "speaker_id": "synthetic-fixture",
        "set": "pause",
        "executable_prefix": True,
        "pause_intervals_ms": [[100, 900]],
        "executable_prefix_intervals_ms": [[100, 900]],
    }
    row = {
        "recording_id": label["recording_id"],
        "split": label["split"],
        "audio_sha256": label["audio_sha256"],
        "stt_model_id": "test-stt",
        "stt_revision": "frozen-revision",
        "provider_config_sha256": "a" * 64,
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


def test_declared_revision_must_match_trajectory(evidence):
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
            "speaker_type": "synthetic",
            "set": "pause",
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
            "speaker_type": "human",
            "set": "pause",
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
            "speaker_type": "human",
            "set": "pause",
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
            "speaker_type": "human",
            "set": "pause",
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
            "speaker_type": "human",
            "set": "pause",
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


def test_metadata_validation_accepts_corpus_label_ids_without_policy_annotations(
    tmp_path, evidence, capsys
):
    from tools.tune_profiles import main

    label, row, frozen = evidence
    label = {
        "id": label["recording_id"],
        "split": label["split"],
        "audio_sha256": label["audio_sha256"],
        "set": "replay",
        "speaker_type": "synthetic",
        "speaker_id": "synthetic-fixture",
    }
    labels = tmp_path / "labels.jsonl"
    partials = tmp_path / "partials.jsonl"
    labels.write_text(json.dumps(label) + "\n")
    partials.write_text(json.dumps(row) + "\n")
    before = (labels.read_bytes(), partials.read_bytes())
    assert (
        main(
            [
                "--validate-partials",
                "--labels",
                str(labels),
                "--partials",
                str(partials),
                "--stt-model-id",
                frozen["stt_model_id"],
                "--stt-revision",
                frozen["stt_revision"],
                "--provider-config-sha256",
                frozen["provider_config_sha256"],
            ]
        )
        == 0
    )
    assert (
        "PASS partial trajectories rows=1 tuning=1 held_out=0"
        in capsys.readouterr().out
    )
    assert (labels.read_bytes(), partials.read_bytes()) == before


def test_stt_identity_comes_from_trajectory_not_corpus_label(evidence):
    label, row, frozen = evidence
    label["stt_revision"] = "different"
    validate_trajectory(row, {label["recording_id"]: label}, frozen)


def test_conflicting_label_id_aliases_are_rejected(tmp_path):
    from tools.tune_profiles import read_rows

    path = tmp_path / "labels.jsonl"
    path.write_text(json.dumps({"id": "one", "recording_id": "another"}) + "\n")
    with pytest.raises(ValueError, match="recording id"):
        read_rows(path)


def test_real_corpus_label_shape_replays_without_invented_fields(tmp_path, evidence):
    original, row, frozen = evidence
    label = {
        "id": "tas-v1-7c3006048df48ee90b38",
        "speaker_id": "synthetic-fixture",
        "speaker_type": "synthetic",
        "set": "replay",
        "split": "tuning",
        "audio_path": original["audio_path"],
        "audio_sha256": original["audio_sha256"],
        "source_uri": "zenodo:4662102/timers-and-such-real.zip#test-real/sample.wav",
        "source_revision": (
            "c92772c830109a6d685b2106fa3f28fc7b1184bc74722045282ed7efb0cf135a"
        ),
        "sample_rate": 16000,
        "last_voice_sample": 15999,
        "executable_prefix": False,
        "license": "CC0-1.0",
        "attribution": "Timers and Such v1.0, real subset, Zenodo 4662102 (CC0-1.0).",
        "consent_id": None,
    }
    row["recording_id"] = label["id"]
    labels = tmp_path / "labels.jsonl"
    partials = tmp_path / "partials.jsonl"
    labels.write_text(json.dumps(label) + "\n")
    partials.write_text(json.dumps(row) + "\n")
    loaded = load_evidence(labels, tmp_path, partials, frozen)
    assert len(loaded) == 1
    assert loaded[0]["pause_intervals_ms"] == []
    assert loaded[0]["executable_prefix_intervals_ms"] == []
    assert loaded[0]["speaker_type"] == "synthetic"
    assert "human" not in loaded[0]


@pytest.mark.parametrize("prefix", [False, True])
def test_pause_rows_without_intervals_have_named_failure(tmp_path, evidence, prefix):
    label, row, frozen = evidence
    label.pop("pause_intervals_ms")
    label["executable_prefix"] = prefix
    labels = tmp_path / "labels.jsonl"
    partials = tmp_path / "partials.jsonl"
    labels.write_text(json.dumps(label) + "\n")
    partials.write_text(json.dumps(row) + "\n")
    with pytest.raises(ValueError, match="^pause interval labels missing$"):
        load_evidence(labels, tmp_path, partials, frozen)


def test_prefix_rows_without_intervals_have_named_failure(tmp_path, evidence):
    label, row, frozen = evidence
    label.pop("executable_prefix_intervals_ms")
    labels = tmp_path / "labels.jsonl"
    partials = tmp_path / "partials.jsonl"
    labels.write_text(json.dumps(label) + "\n")
    partials.write_text(json.dumps(row) + "\n")
    with pytest.raises(ValueError, match="^prefix interval labels missing$"):
        load_evidence(labels, tmp_path, partials, frozen)


def test_synthetic_kind_cannot_be_overridden_by_legacy_human_boolean(tmp_path):
    rows = [
        {
            "recording_id": str(i),
            "split": "held_out",
            "set": "pause",
            "speaker_type": "synthetic",
            "human": True,
            "pause_intervals_ms": [[1, 2]],
        }
        for i in range(300)
    ]
    with pytest.raises(ValueError, match="300"):
        freeze_profiles(PROFILES, rows, tmp_path / "profiles.json", {}, {})


@pytest.mark.parametrize(
    "speaker_id,kind,expected",
    [
        ("speaker-fixture", None, "human"),
        ("synthetic-fixture", None, "synthetic"),
        ("speaker-fixture", "human", "human"),
        ("speaker-fixture", "synthetic", None),
        ("speaker-fixture", "unknown", None),
        (None, "human", None),
    ],
)
def test_corpus_speaker_kind(speaker_id, kind, expected):
    from tools.tune_profiles import speaker_kind

    row = {"speaker_id": speaker_id}
    if kind is not None:
        row["speaker_type"] = kind
    if expected is None:
        with pytest.raises(ValueError, match="speaker"):
            speaker_kind(row)
    else:
        assert speaker_kind(row) == expected


def test_replay_rows_cannot_fill_pause_denominator(tmp_path):
    rows = [
        {
            "recording_id": str(i),
            "split": "held_out",
            "set": "replay",
            "speaker_type": "human",
            "pause_intervals_ms": [[1, 2]],
        }
        for i in range(300)
    ]
    with pytest.raises(ValueError, match="300"):
        freeze_profiles(PROFILES, rows, tmp_path / "profiles.json", {}, {})


@pytest.mark.parametrize(
    "options",
    [
        ["--freeze"],
        ["--check-held-out"],
        ["--validate-partials"],
        ["--disable-safe-early"],
        ["--profile", "safe"],
        ["--split", "tuning"],
        ["--labels", "unused.jsonl"],
    ],
)
def test_fetch_models_refuses_other_actions_before_side_effects(
    tmp_path, options, capsys
):
    from tools.tune_profiles import main

    cache = tmp_path / "unused-cache"
    assert main(["--fetch-models", "--model-cache", str(cache), *options]) == 1
    assert "model fetch cannot combine" in capsys.readouterr().out
    assert not cache.exists()


def test_profile_documentation_records_particle_cost_without_fixed_ref_counts():
    import re

    root = Path(__file__).resolve().parents[2]
    documentation = root / "docs/profiles.md"
    assert documentation.is_file()
    assert "turn the lights on" in documentation.read_text()
    record = (
        (root / "docs/verification.md")
        .read_text()
        .split("## Guarded turn detection\n", 1)[1]
    )
    assert not re.search(r"commits=\d+", record)
    for value in (
        "assist-live-server/docs/reports/q6-partials.jsonl",
        "750c5aa56d8ec7ace237b8a5816f1f9a7fa3f9f7ef6a937ff4435813dc0ae186",
        "assist-live/tests/data/labels.jsonl",
        "nvidia/parakeet-tdt-0.6b-v2",
        "0bbb45a3365852604aef28b538a8f066f4ccaa85",
        "b4ede9e1a2e15e6dafd8e40680691d40760ad11904a9496edc2afe653a331eab",
    ):
        assert value in record


def test_replay_flushes_received_final_silence():
    import os

    from tools.tune_profiles import replay

    root = Path(__file__).resolve().parents[2]
    cache = os.environ.get("ASSIST_LIVE_MODEL_CACHE")
    assert cache, "FAIL model cache: set ASSIST_LIVE_MODEL_CACHE"
    row = {"pcm": bytes(1500 * 32), "partials": [{"audio_ms": 0, "text": "the"}]}
    assert (
        replay(row, Profile(), Path(cache), root / "assets/manifest.json")[0][
            "audio_ms"
        ]
        == 1500
    )

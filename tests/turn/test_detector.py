import hashlib
import json
import os

import numpy as np
import pytest
from assist_live_server.turn.detector import (
    ModelRuntime,
    TurnDetector,
    fetch_models,
    model_records,
    whisper_features,
)

from tests.turn.conftest import ROOT


def pcm(ms):
    return bytes(ms * 32)


def test_speech_onset_property_is_once_per_turn_and_uses_existing_vad():
    calls = []

    def vad(samples):
        calls.append(True)
        return float(np.any(samples))

    d = TurnDetector(vad_probability=vad, turn_probability=lambda _: 0)
    assert d.speech_started_at_ms is None
    d.push(pcm(32), "")
    assert d.speech_started_at_ms is None
    d.push(np.ones(512, dtype="<i2").tobytes(), "")
    assert d.speech_started_at_ms == 32.0
    d.push(pcm(32), "")
    d.push(np.ones(512, dtype="<i2").tobytes(), "")
    assert d.speech_started_at_ms == 32.0
    assert len(calls) == 4


def test_incomplete_transcript_blocks_early_end(detector):
    assert not detector.candidate(
        silence_ms=500, probability=0.99, partial="turn on the"
    )


@pytest.mark.parametrize("profile", ["safe", "fast"])
def test_three_conditions(profile):
    detector = TurnDetector(
        profile, vad_probability=lambda _: 0, turn_probability=lambda _: 1
    )
    assert not detector.candidate(399, 1, "turn on lights")
    assert not detector.candidate(500, 0.1, "turn on lights")
    assert not detector.candidate(500, 1, "turn on the")
    assert detector.candidate(400, 0.99, "turn on lights")
    for probability in (float("nan"), float("inf"), -1, 2):
        assert not detector.candidate(500, probability, "turn on lights")


def test_early_event_is_terminal_and_chunk_independent():
    def run(chunks):
        d = TurnDetector(
            vad_probability=lambda a: float(np.any(a)), turn_probability=lambda _: 1
        )
        events = []
        for chunk in chunks:
            events.extend(d.push(chunk, "turn on lights"))
        assert d.push(pcm(2000), "turn on lights") == []
        return events

    data = (np.ones(512, dtype="<i2").tobytes()) + pcm(1000)
    result = run([data])
    assert result == run([data[i : i + 74] for i in range(0, len(data), 74)])
    assert len(result) == 1
    assert result[0]["reason"] == "early"
    assert 400 <= result[0]["silence_ms"] <= 432


@pytest.mark.parametrize("profile", ["safe", "fast"])
def test_maximum_silence_ends_incomplete_partial(profile):
    d = TurnDetector(profile, vad_probability=lambda _: 0, turn_probability=lambda _: 1)
    assert d.push(pcm(1499), "turn on the") == []
    events = d.push(pcm(100), "turn on the")
    assert len(events) == 1 and events[0]["reason"] == "max_silence"


@pytest.mark.parametrize("profile", ["safe", "fast"])
def test_hard_endpoint_bounds_continuous_speech(profile):
    d = TurnDetector(profile, vad_probability=lambda _: 1, turn_probability=lambda _: 0)
    assert not d.push(pcm(14999), "and")
    events = d.push(pcm(500), "and")
    assert events == [
        {
            "type": "end_decision",
            "reason": "hard",
            "audio_ms": 15000.0,
            "silence_ms": 0.0,
        }
    ]


def test_speech_resets_silence_and_invalid_pcm_is_rejected():
    d = TurnDetector(
        vad_probability=lambda a: float(np.any(a)), turn_probability=lambda _: 1
    )
    assert not d.push(pcm(384), "lights")
    assert not d.push(np.ones(512, dtype="<i2").tobytes(), "lights")
    assert not d.push(pcm(384), "lights")
    assert d.push(pcm(32), "lights")[0]["reason"] == "early"
    with pytest.raises(ValueError, match="PCM"):
        d.push(b"x", "lights")


@pytest.mark.parametrize("count", [0, 8000, 128000, 160000])
def test_whisper_features_match_locked_probe(count):
    from tools.probe_smart_turn import build_extractor, extract_features

    samples = np.random.default_rng(17).normal(0, 0.05, count).astype(np.float32)
    expected = extract_features(build_extractor(), samples)
    actual = whisper_features(samples)
    assert actual.shape == (1, 80, 800) and actual.dtype == np.float32
    np.testing.assert_allclose(actual, expected, atol=2e-5, rtol=2e-5)


def test_fetch_plan_hash_checks_without_network(tmp_path):
    data = b"synthetic model bytes"
    source = tmp_path / "source"
    source.write_bytes(data)
    assets = [
        {
            "id": name,
            "url": source.as_uri(),
            "revision": "pin",
            "sha256": hashlib.sha256(data).hexdigest(),
            "status": "included",
            "path": f"models/{name}.onnx",
        }
        for name in ("silero-vad", "smart-turn-v3.2-cpu")
    ]
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "download_plan": [
                    {k: a[k] for k in ("id", "url", "sha256", "path")} for a in assets
                ],
                "assets": assets,
            }
        )
    )
    cache = tmp_path / "cache"
    fetch_models(cache, manifest)
    assert all((cache / a["path"]).read_bytes() == data for a in assets)
    (cache / "models/silero-vad.onnx").write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="SHA-256"):
        ModelRuntime(cache, manifest)
    assets[0]["sha256"] = "0" * 64
    manifest.write_text(
        json.dumps(
            {
                "download_plan": [
                    {k: a[k] for k in ("id", "url", "sha256", "path")} for a in assets
                ],
                "assets": assets,
            }
        )
    )
    with pytest.raises(ValueError, match="SHA-256"):
        fetch_models(tmp_path / "bad-cache", manifest)


def test_fetch_rejects_model_outside_download_plan(tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"download_plan": [], "assets": []}))
    with pytest.raises(ValueError, match="download plan"):
        model_records(manifest)


def test_real_models_from_required_cache():
    cache = os.environ.get("ASSIST_LIVE_MODEL_CACHE")
    assert cache, (
        "FAIL model cache: set ASSIST_LIVE_MODEL_CACHE and fetch pinned models"
    )
    runtime = ModelRuntime(
        cache,
        os.environ.get("ASSIST_LIVE_ASSET_MANIFEST", ROOT / "assets/manifest.json"),
    )
    silence = np.zeros(512, dtype=np.float32)
    assert 0 <= runtime.vad_probability(silence) < 0.5
    assert 0 <= runtime.turn_probability(np.zeros(16000, dtype=np.float32)) <= 1
    detector = TurnDetector(models=runtime)
    assert detector.push(pcm(1600), "turn on the")[0]["reason"] == "max_silence"


def test_server_ignores_tool_manifest_environment(monkeypatch):
    import assist_live_server.turn.detector as module

    monkeypatch.setenv("ASSIST_LIVE_ASSET_MANIFEST", "absent-tool-override.json")
    monkeypatch.setenv("ASSIST_LIVE_MODEL_CACHE", "absent-model-cache")
    with pytest.raises(ValueError, match="download plan|FAIL model cache"):
        module.TurnDetector()


def test_landed_download_entries_are_the_runtime_pins():
    document = json.loads((ROOT / "assets/manifest.json").read_text())
    records = model_records(ROOT / "assets/manifest.json")
    for name, record in records.items():
        entry = next(row for row in document["download_plan"] if row["id"] == name)
        assert all(record[key] == entry[key] for key in ("id", "url", "sha256", "path"))
        assert record["revision"]


@pytest.mark.parametrize(
    "change", ["duplicate", "sha256", "url", "absolute_path", "traversal_path"]
)
def test_malformed_download_plan_is_rejected(tmp_path, change):
    document = json.loads((ROOT / "assets/manifest.json").read_text())
    entry = next(row for row in document["download_plan"] if row["id"] == "silero-vad")
    if change == "duplicate":
        document["download_plan"].append(dict(entry))
    elif change == "sha256":
        entry["sha256"] = "0" * 64
    elif change == "url":
        entry["url"] = "https://example.invalid/changed-model.onnx"
    elif change == "absolute_path":
        entry["path"] = str(tmp_path / "outside.onnx")
    else:
        entry["path"] = "../outside.onnx"
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="download plan|pin|path"):
        model_records(manifest)


def test_cache_symlink_cannot_escape(tmp_path):
    from assist_live_server.turn.detector import verified_model

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "model.onnx").write_bytes(b"synthetic model")
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "models").symlink_to(outside, target_is_directory=True)
    record = {
        "id": "synthetic",
        "path": "models/model.onnx",
        "sha256": hashlib.sha256(b"synthetic model").hexdigest(),
    }
    with pytest.raises(ValueError, match="path"):
        verified_model(cache, record)


def test_unchanged_partial_limits_full_smart_turn_inferences():
    calls = []
    d = TurnDetector(
        vad_probability=lambda _: 0,
        turn_probability=lambda audio: calls.append(len(audio)) or 0,
    )
    assert d.push(pcm(1472), "set volume to 50") == []
    assert calls == [ms * 16 for ms in (416, 576, 736, 896, 1056, 1216, 1376)]


def test_changed_partial_bypasses_audio_growth_throttle():
    calls = []
    d = TurnDetector(
        vad_probability=lambda _: 0,
        turn_probability=lambda audio: calls.append(len(audio)) or 0,
    )
    assert d.push(pcm(416), "turn on lights") == []
    assert d.push(pcm(32), "stop") == []
    assert calls == [416 * 16, 448 * 16]
    assert d.push(pcm(128), "stop") == []
    assert len(calls) == 2
    assert d.push(pcm(32), "stop") == []
    assert calls[-1] == 608 * 16


def test_unknown_profile_raises_value_error():
    with pytest.raises(ValueError, match="profile"):
        TurnDetector(
            "unknown", vad_probability=lambda _: 0, turn_probability=lambda _: 0
        )


def test_flush_handles_residual_silence_without_adding_audio():
    sizes = []
    d = TurnDetector(
        vad_probability=lambda audio: sizes.append(len(audio)) or 0,
        turn_probability=lambda _: 0,
    )
    assert d.push(pcm(1500), "turn on the") == []
    events = d.flush("turn on the")
    assert events == [
        {
            "type": "end_decision",
            "reason": "max_silence",
            "audio_ms": 1500.0,
            "silence_ms": 1500.0,
        }
    ]
    assert set(sizes) == {512}
    assert d.flush("turn on the") == []


def test_empty_flush_does_not_invent_silence():
    d = TurnDetector(vad_probability=lambda _: 0, turn_probability=lambda _: 0)
    assert d.push(pcm(1499), "the") == []
    assert d.flush("the") == []
    assert d.samples == 1499 * 16
    assert d.flush("the") == []
    assert d.samples == 1499 * 16
    assert d.push(pcm(1), "the") == []
    assert d.flush("the")[0]["audio_ms"] == 1500


def test_sequential_turns_reset_shared_real_vad_runtime():
    cache = os.environ.get("ASSIST_LIVE_MODEL_CACHE")
    assert cache, "FAIL model cache: set ASSIST_LIVE_MODEL_CACHE"
    shared = ModelRuntime(cache)
    rng = np.random.default_rng(123)
    audio = [rng.integers(-12000, 12000, 512, dtype=np.int16).tobytes(), pcm(1600)]

    def run(runtime):
        d = TurnDetector(models=runtime)
        assert not runtime.state.any()
        assert not runtime.context.any()
        events = [event for block in audio for event in d.push(block, "the")]
        return events, runtime.state.copy(), runtime.context.copy()

    for _ in range(2):
        actual = run(shared)
        expected = run(ModelRuntime(cache))
        assert len(actual[0]) == 1 and actual[0][0]["reason"] == "max_silence"
        assert actual[0] == expected[0]
        np.testing.assert_array_equal(actual[1], expected[1])
        np.testing.assert_array_equal(actual[2], expected[2])


@pytest.mark.parametrize("name", ["silero-vad", "piper-runtime"])
def test_duplicate_asset_records_are_rejected(tmp_path, name):
    document = json.loads((ROOT / "assets/manifest.json").read_text())
    document["assets"].append(
        next(dict(row) for row in document["assets"] if row["id"] == name)
    )
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="duplicate asset"):
        model_records(path)

import hashlib
import json
import shutil
import subprocess
import sys
import wave
from pathlib import Path

import pytest

from tools.benchmark_stt import (
    export_trajectories,
    paired_bootstrap,
    run_comparison,
    select_candidate,
    verified_audio,
    word_error_rate,
)

ROOT = Path(__file__).resolve().parents[1]


def test_first_word_loss_counts_as_error():
    assert word_error_rate("turn on test light", "on test light") == 0.25


@pytest.mark.parametrize(
    ("reference", "hypothesis", "expected"),
    [("Hello, WORLD!", "hello world", 0), ("a b", "a c d", 1), ("", "word", 1)],
)
def test_word_errors_include_substitution_insertion_and_case(
    reference, hypothesis, expected
):
    assert word_error_rate(reference, hypothesis) == expected


def test_selection_refuses_any_held_out_row():
    with pytest.raises(ValueError, match="tuning only"):
        select_candidate(
            [
                {
                    "split": "held_out",
                    "candidate": "parakeet",
                    "errors": 0,
                    "words": 4,
                    "exact": True,
                    "first_word": True,
                    "first_partial_ms": 500,
                    "final_latency_ms": 10,
                }
            ]
        )


def test_audio_digest_mismatch_stops_before_decode(tmp_path):
    path = tmp_path / "sample.wav"
    with wave.open(str(path), "wb") as wav:
        wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        wav.writeframes(b"\0\0" * 160)
    label = {"audio_path": "sample.wav", "audio_sha256": "0" * 64}
    with pytest.raises(ValueError, match="audio SHA-256 mismatch"):
        verified_audio(label, tmp_path)
    label["audio_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    assert len(verified_audio(label, tmp_path)) == 160


def test_audio_path_cannot_escape_root(tmp_path):
    with pytest.raises(ValueError, match="audio root"):
        verified_audio({"audio_path": "../outside.wav"}, tmp_path)


def test_non_monotonic_trajectory_is_not_written(tmp_path):
    target = tmp_path / "partials.jsonl"
    row = {
        "recording_id": "sample",
        "split": "tuning",
        "audio_sha256": "a" * 64,
        "stt_model_id": "test",
        "stt_revision": "revision",
        "provider_config_sha256": "b" * 64,
        "partials": [
            {"audio_ms": 200, "text": "turn"},
            {"audio_ms": 100, "text": "on"},
        ],
    }
    with pytest.raises(ValueError, match="monotonic"):
        export_trajectories([row], target)
    assert not target.exists()


def test_export_field_contract(tmp_path):
    import json

    target = tmp_path / "partials.jsonl"
    row = {
        "recording_id": "sample",
        "split": "held_out",
        "audio_sha256": "a" * 64,
        "stt_model_id": "test",
        "stt_revision": "revision",
        "provider_config_sha256": "b" * 64,
        "partials": [{"audio_ms": 0, "text": ""}, {"audio_ms": 100, "text": "turn"}],
    }
    export_trajectories([row], target)
    assert json.loads(target.read_text()) == row


def test_selection_uses_tuning_then_freezes_before_held_out(tmp_path):
    events = []
    labels = [{"id": "held", "split": "held_out"}, {"id": "tune", "split": "tuning"}]

    def decode(label, candidate):
        events.append((label["split"], candidate))
        if label["split"] == "held_out":
            assert (tmp_path / "freeze.json").is_file()
        return {
            "split": label["split"],
            "candidate": candidate,
            "errors": 0,
            "words": 4,
            "first_word": True,
            "first_partial_ms": 500,
            "final_latency_ms": 10,
            "exact": True,
        }

    frozen, results = run_comparison(
        labels,
        ["parakeet", "sherpa-zipformer", "fastconformer"],
        decode,
        tmp_path / "freeze.json",
        {"label_sha256": "a" * 64},
    )
    assert frozen["candidate"] == "parakeet"
    assert len(results) == 6
    assert [split for split, _ in events] == ["tuning"] * 3 + ["held_out"] * 3
    with pytest.raises(ValueError, match="freeze exists"):
        run_comparison(labels, ["parakeet"], decode, tmp_path / "freeze.json", {})


def test_numerals_and_spoken_numbers_have_equal_word_tokens():
    assert (
        word_error_rate(
            "set timer for 123 seconds",
            "set timer for one hundred twenty three seconds",
        )
        == 0
    )


def test_streaming_selection_requires_accuracy_and_latency():
    rows = []
    for candidate, errors, partial in [
        ("parakeet", 1, 600),
        ("sherpa-zipformer", 1, 300),
        ("fastconformer", 2, 100),
    ]:
        rows.append(
            {
                "candidate": candidate,
                "split": "tuning",
                "errors": errors,
                "words": 10,
                "first_word": True,
                "first_partial_ms": partial,
                "final_latency_ms": 20,
                "exact": False,
            }
        )
    assert select_candidate(rows) == "sherpa-zipformer"


def test_noninferiority_margin_is_one_percentage_point():
    baseline = {
        "candidate": "parakeet",
        "split": "tuning",
        "errors": 0,
        "words": 100,
        "first_word": True,
        "first_partial_ms": 600,
        "final_latency_ms": 20,
        "exact": False,
    }
    stream = {
        **baseline,
        "candidate": "sherpa-zipformer",
        "errors": 1,
        "first_partial_ms": 300,
    }
    assert select_candidate([baseline, stream]) == "sherpa-zipformer"
    assert select_candidate([baseline, {**stream, "errors": 2}]) == "parakeet"


def test_paired_bootstrap_uses_same_recordings_and_word_denominator():
    baseline = [{"recording_id": "a", "errors": 1, "words": 4}]
    candidate = [{"recording_id": "a", "errors": 0, "words": 4}]
    assert paired_bootstrap(candidate, baseline) == {
        "difference_pp": -25.0,
        "low_pp": -25.0,
        "high_pp": -25.0,
    }
    with pytest.raises(ValueError, match="paired"):
        paired_bootstrap([{**candidate[0], "recording_id": "b"}], baseline)


def test_paired_bootstrap_weights_by_reference_words():
    baseline = [
        {"recording_id": "a", "errors": 1, "words": 2},
        {"recording_id": "b", "errors": 1, "words": 8},
    ]
    candidate = [
        {"recording_id": "b", "errors": 2, "words": 8},
        {"recording_id": "a", "errors": 0, "words": 2},
    ]
    assert paired_bootstrap(candidate, baseline) == {
        "difference_pp": 0,
        "low_pp": -50,
        "high_pp": 12.5,
    }


def test_partial_latency_carries_decode_backlog(monkeypatch):
    import numpy as np

    from tools.benchmark_stt import Decoder, runtime_configuration

    class Model:
        def recognize(self, samples, sample_rate):
            assert sample_rate == 16000
            return "turn"

    decoder = Decoder.__new__(Decoder)
    decoder.name = "parakeet"
    candidate = json.loads((ROOT / "assets/manifest.json").read_text())[
        "stt_benchmark"
    ]["parakeet"]
    decoder.config = runtime_configuration(candidate, 4)
    decoder.model = Model()
    clock = iter([0.0, 0.6, 1.0, 1.6])
    monkeypatch.setattr("tools.benchmark_stt.time.perf_counter", lambda: next(clock))
    result = decoder.decode(np.zeros(12800, dtype=np.float32))
    assert result["first_partial_ms"] == pytest.approx(1000)
    assert result["final_latency_ms"] == pytest.approx(800)
    assert [p["audio_ms"] for p in result["partials"]] == [400, 800]


def test_streaming_final_flush_uses_frozen_660ms_tail():
    import numpy as np

    from tools.benchmark_stt import Decoder, runtime_configuration

    class Stream:
        finished = False
        drained = False
        tail = 0

        def accept_waveform(self, rate, samples):
            assert rate == 16000
            self.tail = len(samples)

        def input_finished(self):
            self.finished = True

    class Model:
        def create_stream(self):
            return Stream()

        def is_ready(self, stream):
            return stream.finished and not stream.drained

        def decode_stream(self, stream):
            stream.drained = True

        def get_result(self, stream):
            return "turn on" if stream.drained and stream.tail == 10560 else "turn"

    decoder = Decoder.__new__(Decoder)
    decoder.name = "sherpa-zipformer"
    decoder.candidate = json.loads((ROOT / "assets/manifest.json").read_text())[
        "stt_benchmark"
    ]["sherpa-zipformer"]
    decoder.config = runtime_configuration(decoder.candidate, 4)
    decoder.model = Model()
    result = decoder.decode(np.zeros(1600, dtype=np.float32))
    assert result["partials"][-1] == {"audio_ms": 100, "text": "turn on"}


@pytest.fixture
def benchmark_copy(tmp_path):
    tree = tmp_path / "tree"
    shutil.copytree(
        ROOT,
        tree,
        ignore=shutil.ignore_patterns(
            ".git",
            ".venv",
            "__pycache__",
            ".pytest_cache",
            ".mypy_cache",
            ".ruff_cache",
        ),
    )
    audio = tmp_path / "audio"
    audio.mkdir()
    wav_path = audio / "sample.wav"
    with wave.open(str(wav_path), "wb") as wav:
        wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        wav.writeframes(b"\0\0" * 12800)
    digest = hashlib.sha256(wav_path.read_bytes()).hexdigest()
    labels = [
        {
            "id": f"sample-{i}",
            "split": "tuning" if i < 50 else "held_out",
            "transcript": "turn on",
            "audio_path": "sample.wav",
            "audio_sha256": digest,
            "alignment_words": [{"start_sample": 160}],
        }
        for i in range(250)
    ]
    label_path = tmp_path / "labels.jsonl"
    label_path.write_text("".join(json.dumps(row) + "\n" for row in labels))
    models = tmp_path / "models"
    manifest_path = tree / "assets/manifest.json"
    manifest = json.loads(manifest_path.read_text())
    for name, candidate in manifest["stt_benchmark"].items():
        directory = models / name
        directory.mkdir(parents=True)
        for record in candidate["files"]:
            data = b"synthetic model fixture"
            (directory / record["file"]).write_bytes(data)
            record["sha256"] = hashlib.sha256(data).hexdigest()
    manifest_path.write_text(json.dumps(manifest))
    (tree / "onnx_asr.py").write_text(
        "class Model:\n"
        "    def recognize(self, samples, sample_rate):\n"
        "        return 'turn on'\n"
        "def load_model(*args, **kwargs):\n"
        "    return Model()\n"
    )
    (tree / "sherpa_onnx.py").write_text(
        "class Stream:\n"
        "    def accept_waveform(self, rate, samples): pass\n"
        "    def input_finished(self): pass\n"
        "class OnlineRecognizer:\n"
        "    @classmethod\n"
        "    def from_transducer(cls, **kwargs): return cls()\n"
        "    def create_stream(self): return Stream()\n"
        "    def is_ready(self, stream): return False\n"
        "    def get_result(self, stream): return 'turn on'\n"
    )
    command = [
        sys.executable,
        "tools/benchmark_stt.py",
        "--labels",
        str(label_path),
        "--audio-root",
        str(audio),
        "--selection-split",
        "tuning",
        "--gate-split",
        "held_out",
        "--partials-output",
        "docs/reports/q6-partials.jsonl",
        "--candidates",
        "parakeet,sherpa-zipformer,fastconformer",
        "--report",
        "docs/research/q6-streaming-stt.md",
        "--model-root",
        str(models),
    ]
    return tree, command, labels, manifest


def test_documented_command_preserves_disclosures_on_copied_tree(benchmark_copy):
    tree, command, _, _ = benchmark_copy
    process = subprocess.run(
        command, cwd=tree, capture_output=True, text=True, timeout=30
    )
    assert process.returncode == 0, process.stdout + process.stderr
    report = (tree / "docs/research/q6-streaming-stt.md").read_text()
    for heading in [
        "Initial test and corrected tail",
        "Harness checks",
        "License scope",
        "Source records",
    ]:
        assert f"## {heading}" in report
    for disclosure in ["136/394", "507/1551", "256/1551", "7/50", "8/50", "MINUTES"]:
        assert disclosure in report
    assert "First reference-word audio ms (coverage)" in report
    assert "## Provider follow-up" in report
    assert "gate full-buffer decoding on `VAD speech onset`" in report


@pytest.mark.parametrize("split", ["tuning", "held_out"])
def test_split_command_keeps_complete_public_export(benchmark_copy, split):
    from tools.benchmark_stt import provider_identity

    tree, command, _, manifest = benchmark_copy
    export = tree / "docs/reports/q6-partials.jsonl"
    before = export.read_bytes()
    if split == "held_out":
        models = Path(command[-1])
        label_path = Path(command[command.index("--labels") + 1])
        frozen = {
            "candidate": "parakeet",
            "label_sha256": hashlib.sha256(label_path.read_bytes()).hexdigest(),
            "providers": {
                name: provider_identity(c, 4)
                for name, c in manifest["stt_benchmark"].items()
            },
            "selection_rule": {
                "wer_margin_pp": 1.0,
                "first_word": "no worse",
                "first_partial": "lower median",
                "final_latency": "no worse median",
            },
        }
        (models / "selection.json").write_text(json.dumps(frozen))
    process = subprocess.run(
        command + ["--split", split],
        cwd=tree,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert process.returncode == 0, process.stdout + process.stderr
    assert export.read_bytes() == before


def test_empty_reference_has_named_cli_failure(benchmark_copy):
    tree, command, labels, _ = benchmark_copy
    labels[0]["transcript"] = ""
    label_path = Path(command[command.index("--labels") + 1])
    label_path.write_text("".join(json.dumps(row) + "\n" for row in labels))
    process = subprocess.run(
        command, cwd=tree, capture_output=True, text=True, timeout=30
    )
    assert process.returncode == 1
    assert "FAIL Q6 comparison: empty reference transcript: sample-0" in process.stdout
    assert "Traceback" not in process.stderr


def test_first_reference_word_latency_does_not_count_silence_filler():
    from tools.benchmark_stt import recognition_metrics

    result = recognition_metrics(
        "turn on",
        [
            {"audio_ms": 400, "text": "Okay."},
            {"audio_ms": 800, "text": "mm turn"},
            {"audio_ms": 1200, "text": "turn on"},
        ],
    )
    assert result["first_reference_word_audio_ms"] == 800
    assert result["initial_nonreference"] is True
    assert (
        recognition_metrics("turn on", [{"audio_ms": 400, "text": "other"}])[
            "first_reference_word_audio_ms"
        ]
        is None
    )


def test_effective_digest_binds_model_bytes_not_descriptive_text():
    import copy

    from tools.benchmark_stt import provider_identity

    candidate = json.loads((ROOT / "assets/manifest.json").read_text())[
        "stt_benchmark"
    ]["parakeet"]
    initial = provider_identity(candidate, 4)["provider_config_sha256"]
    changed = copy.deepcopy(candidate)
    changed["files"][0]["sha256"] = "0" * 64
    assert provider_identity(changed, 4)["provider_config_sha256"] != initial
    changed = copy.deepcopy(candidate)
    changed["configuration"]["decode_mode"] = "different description"
    assert provider_identity(changed, 4)["provider_config_sha256"] == initial
    assert provider_identity(candidate, 1)["provider_config_sha256"] != initial


def test_manifest_records_upstream_660ms_streaming_tail():
    manifest = json.loads((ROOT / "assets/manifest.json").read_text())
    for name in ("sherpa-zipformer", "fastconformer"):
        assert manifest["stt_benchmark"][name]["configuration"]["tail_samples"] == 10560


@pytest.mark.parametrize("mutation", range(11))
def test_historical_evidence_mutations_fail_before_public_output(
    benchmark_copy, mutation
):
    tree, command, _, _ = benchmark_copy
    path = tree / "assets/manifest.json"
    manifest = json.loads(path.read_text())
    evidence = manifest["stt_benchmark_evidence"]
    if mutation == 0:
        evidence["initial_results"][1]["errors"] = 135
    elif mutation == 1:
        evidence["flush_audit"]["zipformer_1000_changes"] = 9
    elif mutation == 2:
        evidence["initial_results"][3]["errors"] = 80
    elif mutation == 3:
        evidence["initial_results"][2]["errors"] = 78
    elif mutation == 4:
        evidence["flush_audit"]["fastconformer_changes"] = 5
    elif mutation == 5:
        evidence["flush_audit"]["whole_buffer_changes"] = 9
    elif mutation == 6:
        evidence["flush_audit"]["residual_after"] = (
            "STOPPED I WENT FOR TWO HOURS SEVEN MIN"
        )
    elif mutation == 7:
        evidence["upstream_wavs"][0]["errors"] = 3
    elif mutation == 8:
        evidence["reference_first_words"]["parakeet"] = 1
    elif mutation == 9:
        evidence["source_cache_sha256"]["models/measurements-both.json"] = "0" * 64
    else:
        del evidence["source_cache_sha256"]["models/measurements-both.json"]
    path.write_text(json.dumps(manifest))
    report = tree / "docs/research/q6-streaming-stt.md"
    before = report.read_bytes()
    result = subprocess.run(
        command, cwd=tree, capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "FAIL Q6 comparison: historical evidence digest mismatch" in result.stdout
    assert report.read_bytes() == before


def test_removed_inert_check_flag_is_not_silently_accepted(benchmark_copy):
    tree, command, _, _ = benchmark_copy
    process = subprocess.run(
        command + ["--check"], cwd=tree, capture_output=True, text=True, timeout=30
    )
    assert process.returncode == 2
    assert "unrecognized arguments: --check" in process.stderr


def test_effective_interval_controls_decode_and_digest():
    import numpy as np

    from tools.benchmark_stt import Decoder, provider_identity, runtime_configuration

    candidate = json.loads((ROOT / "assets/manifest.json").read_text())[
        "stt_benchmark"
    ]["parakeet"]
    before = provider_identity(candidate, 4)
    candidate["configuration"]["interval_ms"] = 200
    lengths = []

    class Model:
        def recognize(self, samples, sample_rate):
            lengths.append(len(samples))
            return "turn"

    decoder = Decoder.__new__(Decoder)
    decoder.name = "parakeet"
    decoder.config = runtime_configuration(candidate, 4)
    decoder.model = Model()
    decoder.decode(np.zeros(6400, dtype=np.float32))
    assert lengths == [3200, 6400]
    assert provider_identity(candidate, 4) != before


def test_runtime_cannot_mutate_recorded_preprocessor_parameters(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from tools import benchmark_stt as benchmark

    candidate = json.loads((ROOT / "assets/manifest.json").read_text())[
        "stt_benchmark"
    ]["parakeet"]
    original_import = benchmark.importlib.import_module

    def load_model(*args, **kwargs):
        kwargs["preprocessor_config"].pop("use_numpy_preprocessors")
        kwargs["preprocessor_config"].pop("max_concurrent_workers")
        return object()

    def import_module(name):
        return (
            SimpleNamespace(load_model=load_model)
            if name == "onnx_asr"
            else original_import(name)
        )

    monkeypatch.setattr(benchmark, "fetch_files", lambda *args: None)
    monkeypatch.setattr(benchmark.importlib, "import_module", import_module)
    decoder = benchmark.Decoder("parakeet", candidate, tmp_path, 4)
    assert decoder.config["preprocessor"] == {
        "use_numpy_preprocessors": True,
        "max_concurrent_workers": 1,
    }

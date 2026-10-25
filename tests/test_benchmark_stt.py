import hashlib
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

    from tools.benchmark_stt import Decoder

    class Model:
        def recognize(self, samples, sample_rate):
            assert sample_rate == 16000
            return "turn"

    decoder = Decoder.__new__(Decoder)
    decoder.name = "parakeet"
    decoder.model = Model()
    clock = iter([0.0, 0.6, 1.0, 1.6])
    monkeypatch.setattr("tools.benchmark_stt.time.perf_counter", lambda: next(clock))
    result = decoder.decode(np.zeros(12800, dtype=np.float32))
    assert result["first_partial_ms"] == pytest.approx(1000)
    assert result["final_latency_ms"] == pytest.approx(800)
    assert [p["audio_ms"] for p in result["partials"]] == [400, 800]


def test_streaming_final_flush_uses_frozen_660ms_tail():
    import numpy as np

    from tools.benchmark_stt import Decoder

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
    decoder.candidate = {"configuration": {"tail_samples": 10560}}
    decoder.model = Model()
    result = decoder.decode(np.zeros(1600, dtype=np.float32))
    assert result["partials"][-1] == {"audio_ms": 100, "text": "turn on"}

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_smart_turn_input_window():
    import numpy as np

    from tools.probe_smart_turn import prepare_samples

    assert prepare_samples(np.ones(16000)).shape == (128000,)


def test_padding_is_on_left_and_long_input_keeps_the_end():
    import numpy as np

    from tools.probe_smart_turn import prepare_samples

    short = prepare_samples(np.ones(16000))
    assert short.dtype == np.float32
    assert np.all(short[:112000] == 0)
    assert np.all(short[112000:] == 1)
    long = np.arange(144000, dtype=np.float32)
    np.testing.assert_array_equal(prepare_samples(long), long[16000:])
    assert np.all(prepare_samples(np.array([])) == 0)


@pytest.mark.parametrize("samples", [[[1.0]], [float("nan")], [float("inf")]])
def test_rejects_non_mono_or_nonfinite_samples(samples):
    import numpy as np

    from tools.probe_smart_turn import prepare_samples

    with pytest.raises(ValueError):
        prepare_samples(np.asarray(samples))


def test_real_numpy_extractor_contract():
    import numpy as np

    from tools.probe_smart_turn import build_extractor, extract_features

    features = extract_features(build_extractor(), np.zeros(16000))
    assert features.shape == (1, 80, 800)
    assert features.dtype == np.float32
    assert np.isfinite(features).all()


def test_extractor_receives_the_prototype_arguments():
    import numpy as np

    from tools.probe_smart_turn import extract_features

    class Extractor:
        def __call__(self, samples, **kwargs):
            assert samples.shape == (128000,)
            assert kwargs == {
                "sampling_rate": 16000,
                "return_tensors": "np",
                "padding": "max_length",
                "max_length": 128000,
                "truncation": True,
                "do_normalize": True,
            }
            return {"input_features": np.zeros((1, 80, 800))}

    assert extract_features(Extractor(), np.ones(16000)).shape == (1, 80, 800)


def test_cpu_and_wall_clocks_and_phases_stay_separate():
    from tools.probe_smart_turn import measure_candidates, summarize

    cpu = iter([0.0, 0.002, 0.007])
    wall = iter([0.0, 0.003, 0.011])
    rows = measure_candidates(
        lambda samples: samples,
        lambda features: 0.75,
        candidates=1,
        cpu_clock=lambda: next(cpu),
        wall_clock=lambda: next(wall),
    )
    assert len(rows) == 1
    assert rows[0]["probability"] == 0.75
    assert rows[0]["features_cpu_ms"] == pytest.approx(2)
    assert rows[0]["inference_cpu_ms"] == pytest.approx(5)
    assert rows[0]["total_cpu_ms"] == pytest.approx(7)
    assert rows[0]["features_wall_ms"] == pytest.approx(3)
    assert rows[0]["inference_wall_ms"] == pytest.approx(8)
    assert rows[0]["total_wall_ms"] == pytest.approx(11)
    assert summarize(rows)["total_cpu_ms"] == pytest.approx({"p50": 7, "p90": 7})


def test_percentiles_use_linear_interpolation():
    from tools.probe_smart_turn import summarize

    rows = [{"total_cpu_ms": value} for value in [1.0, 2.0, 3.0, 4.0]]
    assert summarize(rows)["total_cpu_ms"] == pytest.approx({"p50": 2.5, "p90": 3.7})


def test_digest_mismatch_is_not_usable(tmp_path):
    from tools.probe_smart_turn import verified_model

    path = tmp_path / "model.onnx"
    path.write_bytes(b"wrong model")
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        verified_model(
            path, {"url": "https://example.invalid/unused", "sha256": "0" * 64}
        )
    assert path.read_bytes() == b"wrong model"


def test_manifest_decision_runs_release_gate(tmp_path):
    import json
    from copy import deepcopy

    from tools.probe_smart_turn import check_decision

    manifest = json.loads((ROOT / "assets/manifest.json").read_text())
    original = deepcopy(manifest)
    manifest["release_root"] = "."
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    assert check_decision(path, allow_blocked=True)["status"] in {
        "confirmed_included",
        "confirmed_blocked_absent",
    }
    record = next(a for a in manifest["assets"] if a["id"] == "smart-turn-v3.2-cpu")
    record["status"] = "unknown"
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="confirmed investigation status"):
        check_decision(path, allow_blocked=True)
    manifest = original
    manifest["release_root"] = "."
    record = next(a for a in manifest["assets"] if a["id"] == "smart-turn-v3.2-cpu")
    record.update(status="confirmed_blocked_absent", download="none")
    manifest["download_plan"] = []
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="blocked asset needs"):
        check_decision(path, allow_blocked=False)
    assert (
        check_decision(path, allow_blocked=True)["status"] == "confirmed_blocked_absent"
    )
    (tmp_path / record["paths"][0]).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / record["paths"][0]).write_bytes(b"blocked")
    with pytest.raises(ValueError, match="blocked asset path"):
        check_decision(path, allow_blocked=True)


def test_cli_refuses_fewer_than_100_candidates(capsys):
    from tools.probe_smart_turn import main

    assert main(["--candidates", "99"]) == 1
    assert "FAIL Smart Turn: at least 100 candidates" in capsys.readouterr().out


def test_report_keeps_cpu_and_wall_columns_and_no_image_claim():
    import json

    from tools.probe_smart_turn import render_report

    manifest = json.loads((ROOT / "assets/manifest.json").read_text())
    record = next(a for a in manifest["assets"] if a["id"] == "smart-turn-v3.2-cpu")
    result = {
        "percentiles": {
            f"{stage}_{clock}_ms": {"p50": value, "p90": value + 1}
            for stage in ["features", "inference", "total"]
            for clock, value in [("cpu", 2.0), ("wall", 9.0)]
        },
        "candidates": 100,
        "numpy_version": "test",
        "onnxruntime_version": "test",
        "transformers_version": "test",
        "started_at": "2026-10-24T00:00:00+00:00",
        "finished_at": "2026-10-24T00:00:01+00:00",
    }
    report = render_report(record, result)
    assert "| `total` | 2.000 | 3.000 | 9.000 | 10.000 |" in report
    assert "The measured count is 100." in report
    assert "Image inventory: `NOT CHECKED`" in report
    assert record["sha256"] in report
    assert record["upstream_project"]["license_url"] in report

"""Measure local Smart Turn cost without a server or an audio recording."""

import argparse
import hashlib
import importlib
import importlib.metadata
import json
import os
import sys
import tempfile
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.request import urlopen

# Set numerical-library limits before NumPy loads a BLAS runtime.
os.environ.update(OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1")

import numpy as np
from numpy.typing import NDArray

ROOT = Path(__file__).resolve().parents[1]
SCRATCH = Path(
    os.environ.get(
        "ASSIST_LIVE_PROBE_DIRECTORY",
        str(Path(tempfile.gettempdir()) / "assist-live-smart-turn"),
    )
)
MODEL_ID = "smart-turn-v3.2-cpu"
WINDOW = 128000

if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))


def prepare_samples(samples: NDArray[Any]) -> NDArray[np.float32]:
    samples = np.asarray(samples, dtype=np.float32)
    if samples.ndim != 1 or not np.isfinite(samples).all():
        raise ValueError("finite mono samples required")
    samples = samples[-WINDOW:]
    return np.pad(samples, (WINDOW - len(samples), 0))


def build_extractor() -> Any:
    # No framework model is used. Only the NumPy feature extractor is necessary.
    os.environ["TRANSFORMERS_NO_ADVISORY_WARNINGS"] = "1"
    transformers = importlib.import_module("transformers")
    return transformers.WhisperFeatureExtractor(chunk_length=8)


def extract_features(extractor: Any, samples: NDArray[Any]) -> NDArray[np.float32]:
    inputs = extractor(
        prepare_samples(samples),
        sampling_rate=16000,
        return_tensors="np",
        padding="max_length",
        max_length=WINDOW,
        truncation=True,
        do_normalize=True,
    )
    features = np.asarray(inputs["input_features"], dtype=np.float32)
    if features.shape != (1, 80, 800) or not np.isfinite(features).all():
        raise ValueError(
            "expected finite float32 input_features with shape (1, 80, 800)"
        )
    return features


def verified_model(path: Path, record: dict[str, Any]) -> Path:
    if (
        not path.exists()
        or hashlib.sha256(path.read_bytes()).hexdigest() != record["sha256"]
    ):
        with urlopen(record["url"], timeout=120) as response:
            data = response.read()
        if hashlib.sha256(data).hexdigest() != record["sha256"]:
            raise ValueError("downloaded model SHA-256 mismatch")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    if hashlib.sha256(path.read_bytes()).hexdigest() != record["sha256"]:
        raise ValueError("cached model SHA-256 mismatch")
    return path


def check_decision(manifest: Path, *, allow_not_selected: bool) -> dict[str, Any]:
    from tools.check_assets import check_assets

    errors = check_assets(manifest, release=True)
    if errors:
        raise ValueError("; ".join(errors))
    document = json.loads(manifest.read_text())
    record = next(item for item in document["assets"] if item["id"] == MODEL_ID)
    if record["status"] == "not_selected" and not allow_not_selected:
        raise ValueError("not-selected asset needs --allow-not-selected")
    return dict(record)


def synthetic_candidate(index: int) -> NDArray[np.float32]:
    count = (1 + index % 10) * 16000
    seconds = np.arange(count, dtype=np.float32) / 16000
    samples = 0.1 * np.sin(2 * np.pi * (110 + index % 50 * 11) * seconds)
    samples[-3200:] = 0
    return np.asarray(samples, dtype=np.float32)


def measure_candidates(
    features: Callable[[NDArray[np.float32]], NDArray[np.float32]],
    inference: Callable[[NDArray[np.float32]], float],
    candidates: int,
    *,
    cpu_clock: Callable[[], float] = time.process_time,
    wall_clock: Callable[[], float] = time.perf_counter,
) -> list[dict[str, float]]:
    rows = []
    for index in range(candidates):
        samples = synthetic_candidate(index)
        wall_start = wall_clock()
        cpu_start = cpu_clock()
        inputs = features(samples)
        cpu_features = cpu_clock()
        wall_features = wall_clock()
        probability = inference(inputs)
        cpu_end = cpu_clock()
        wall_end = wall_clock()
        if not np.isfinite(probability) or not 0 <= probability <= 1:
            raise ValueError("expected a finite turn probability in [0, 1]")
        rows.append(
            {
                "candidate": float(index),
                "probability": probability,
                "features_cpu_ms": (cpu_features - cpu_start) * 1000,
                "inference_cpu_ms": (cpu_end - cpu_features) * 1000,
                "total_cpu_ms": (cpu_end - cpu_start) * 1000,
                "features_wall_ms": (wall_features - wall_start) * 1000,
                "inference_wall_ms": (wall_end - wall_features) * 1000,
                "total_wall_ms": (wall_end - wall_start) * 1000,
            }
        )
    return rows


def summarize(rows: list[dict[str, float]]) -> dict[str, dict[str, float]]:
    return {
        key: dict(
            zip(
                ("p50", "p90"),
                map(float, np.percentile([row[key] for row in rows], [50, 90])),
                strict=True,
            )
        )
        for key in rows[0]
        if key.endswith("_ms")
    }


def render_report(record: dict[str, Any], result: dict[str, Any] | None = None) -> str:
    from tools.asset_rights import license_sources, render_record, render_sources

    if record.get("id") != MODEL_ID or result is None:
        return render_record(record)
    metrics = result["percentiles"]
    table = "\n".join(
        f"| `{stage}` | {metrics[stage + '_cpu_ms']['p50']:.3f} | "
        f"{metrics[stage + '_cpu_ms']['p90']:.3f} | "
        f"{metrics[stage + '_wall_ms']['p50']:.3f} | "
        f"{metrics[stage + '_wall_ms']['p90']:.3f} |"
        for stage in ("features", "inference", "total")
    )
    sources = license_sources(record)
    license_text = next(s for s in sources if s["kind"] == "license_text")
    project_scope = next(
        s for s in sources if "github.com" in s["url"] and "README" in s["url"]
    )
    status = record["status"]
    decision = (
        "Use the pinned model for local cost tests. "
        "Its rights let the server download it at startup."
        if status == "included"
        else "Do not select this model. "
        "The release tree contains no not-selected asset."
    )
    return f"""# Smart Turn rights and cost

## Decision

{decision}
Status: `{status}`. The project is experimental and has no release.

No model file or audio file is in this repository.
Image inventory: `NOT CHECKED`. There is no image.
A target-host cost test is necessary before production turn detection.
Tests for safe turn endings are also necessary.

## File rights

- Model repository: {record["url"].split("/resolve/")[0]}
- Revision: `{record["revision"]}`.
- File: `{record["url"].rsplit("/", 1)[-1]}`.
- Download: {record["url"]}
- SHA-256 of the downloaded bytes: `{record["sha256"]}`.
- `Model card`: {sources[0]["url"]}
- Project revision: `{project_scope["revision_or_version"]}`.
- Project scope: {project_scope["url"]}
- Full license text: {license_text["url"]}
- License-text SHA-256: `{hashlib.sha256(license_text["quote"].encode()).hexdigest()}`.
- Access date for these sources: 2026-10-24.

{render_sources(record)}

These statements connect the model and pinned file to the full license text.
This decision does not use a badge as a license source.
The project page has the title `Smart Turn v3.2`.
The selected file is `smart-turn-v3.2-cpu.onnx`.

BSD-2-Clause lets users use and redistribute the model with or without changes.
Source copies must keep the copyright notice, conditions, and disclaimer.
Binary copies must reproduce them in the supplied documents or other material.
`THIRD_PARTY_NOTICES.md` has the full text.
The planned startup downloader must use the pinned URL and check SHA-256 before use.
There is no startup downloader or server provider at this time.

## Runtime parts

The ONNX session uses one file and the `CPUExecutionProvider`.
It accepts `input_features` with shape `(1, 80, 800)` and dtype `float32`.
No external tensor file, tokenizer, or voice is necessary.
There is no downloaded feature-extractor configuration.
`WhisperFeatureExtractor(chunk_length=8)` uses the locked library defaults.

It uses 80 `features`, a 400-sample FFT, a 160-sample hop, and `dither=0.0`.
NumPy supplies `feature extraction`. The probe loads no PyTorch model.

The probe uses these development dependencies:

- NumPy `{result["numpy_version"]}`.
- ONNX Runtime `{result["onnxruntime_version"]}`.
- Transformers `{result["transformers_version"]}`.

The client has no model-runtime dependency.
The package versions and wheel hashes are in `uv.lock`.
Package licenses and Python floors are in `docs/verification.md`.

## Input and clocks

The probe uses the last 128000 mono samples at 16 kHz (eight seconds).
Short inputs get `zero padding` on the left. Long inputs discard samples at the start.
The extractor receives these arguments:

- `padding="max_length"` and `max_length=128000`.
- `truncation=True` and `do_normalize=True`.

It gives NumPy `features` as `float32`. The ONNX input name is `input_features`.

The probe makes sine-wave candidates locally.
It does not test correct model decisions or human voices.
Lengths cycle from one to ten seconds. Frequencies cycle from 110 to 649 Hz.
Peak amplitude is 0.1. Each signal ends with `3200 zero samples`.

The measurement does not include signal generation and imports.
It also does not include model download and session construction.
Five warm-up candidates run before the measurement.
The measured count is {result["candidates"]}.
`Feature cost` includes sample input processing, normalization, and the tensor cast.
Inference cost includes the ONNX call and scalar extraction.

CPU time uses `time.process_time`, summed across process `threads`.
Wall time uses `time.perf_counter`. It includes time when the process does not run.
The probe measures each phase independently.
Total percentiles use per-candidate totals, not sums of percentiles.
`numpy.percentile` uses linear interpolation.

Raw per-candidate values stay in scratch storage, external to the repository.
`ASSIST_LIVE_PROBE_DIRECTORY` can select this directory.
The default is `assist-live-smart-turn` in the system temporary directory.

## Development-host cost

Hardware class: AMD Ryzen AI Max+ 395 class, 16 cores / 32 `threads`, x86-64.
ONNX intra-op `threads`: 1. ONNX inter-op `threads`: 1. Execution mode: sequential.

The OpenMP, OpenBLAS, and MKL `thread limits` are 1 before NumPy import.
Representativeness for the eventual target host: `unverified`.
A target-host measurement is a follow-up.
These values are not a release performance claim.

Measurement start: `{result["started_at"]}`. End: `{result["finished_at"]}`.
All values below are milliseconds for each candidate.

| Phase | CPU p50 | CPU p90 | Wall p50 | Wall p90 |
|---|---|---|---|---|
{table}

## Reproduction

```sh
uv run pytest tests/test_probe_smart_turn.py -q
uv run python tools/probe_smart_turn.py --record-decision \\
  --allow-not-selected --candidates 100 --report docs/research/q5-smart-turn.md
uv run python tools/check_assets.py --manifest assets/manifest.json --release
```

The decision command checks release-asset rights before the measurement.
It refuses an unknown status, a hash mismatch, or a not-selected release-tree asset.
An accepted rights record does not claim a built-image check.
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record-decision", action="store_true")
    parser.add_argument("--allow-not-selected", action="store_true")
    parser.add_argument("--candidates", type=int, default=100)
    parser.add_argument("--reuse-measurements", type=Path)
    parser.add_argument(
        "--report", type=Path, default=ROOT / "docs/research/q5-smart-turn.md"
    )
    args = parser.parse_args(argv)
    if args.candidates < 100:
        print("FAIL Smart Turn: at least 100 candidates required")
        return 1
    try:
        record = check_decision(
            ROOT / "assets/manifest.json", allow_not_selected=args.allow_not_selected
        )
        SCRATCH.mkdir(parents=True, exist_ok=True)
        if args.reuse_measurements:
            result = json.loads(args.reuse_measurements.read_text())
            if result["sha256"] != record["sha256"] or result["candidates"] < 100:
                raise ValueError(
                    "saved measurement model digest or candidate count mismatch"
                )
        else:
            model = verified_model(SCRATCH / "smart-turn-v3.2-cpu.onnx", record)
            ort = importlib.import_module("onnxruntime")
            options = ort.SessionOptions()
            options.intra_op_num_threads = 1
            options.inter_op_num_threads = 1
            options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
            session = ort.InferenceSession(
                str(model), sess_options=options, providers=["CPUExecutionProvider"]
            )
            extractor = build_extractor()

            def features(samples: NDArray[np.float32]) -> NDArray[np.float32]:
                return extract_features(extractor, samples)

            def inference(inputs: NDArray[np.float32]) -> float:
                return float(session.run(None, {"input_features": inputs})[0][0].item())

            measure_candidates(features, inference, 5)
            started = datetime.now(UTC).isoformat()
            rows = measure_candidates(features, inference, args.candidates)
            result = {
                "started_at": started,
                "finished_at": datetime.now(UTC).isoformat(),
                "candidates": args.candidates,
                "threads": 1,
                "numpy_version": np.__version__,
                "onnxruntime_version": ort.__version__,
                "transformers_version": importlib.metadata.version("transformers"),
                "percentiles": summarize(rows),
                "rows": rows,
                "status": record["status"],
                "sha256": record["sha256"],
            }
            (SCRATCH / "measurements.json").write_text(
                json.dumps(result, indent=2) + "\n"
            )
        if args.record_decision:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(render_report(record, result))
            print("PASS decision recorded")
        print(
            "PASS included rights"
            if record["status"] == "included"
            else "PASS not-selected asset absent"
        )
        return 0
    except (OSError, ValueError, KeyError, StopIteration) as error:
        print(f"FAIL Smart Turn: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

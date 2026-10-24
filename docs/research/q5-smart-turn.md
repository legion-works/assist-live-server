# Smart Turn rights and cost

## Decision

Use the pinned model for local cost tests. Its rights let the server download it at startup.
Status: `confirmed_included`. The project is experimental and has no release.

No model file or audio file is in this repository.
Image inventory: `NOT CHECKED`. There is no image.
A target-host cost test is necessary before production turn detection.
Tests for safe turn endings are also necessary.

## File rights

- Model repository: https://huggingface.co/pipecat-ai/smart-turn-v3
- Revision: `f766f81d3cfdf7737ac64aad813d91bbfd56bf93`.
- File: `smart-turn-v3.2-cpu.onnx`.
- Download: https://huggingface.co/pipecat-ai/smart-turn-v3/resolve/f766f81d3cfdf7737ac64aad813d91bbfd56bf93/smart-turn-v3.2-cpu.onnx
- SHA-256 of the downloaded bytes: `2bb026316b14a660486a75b1733cd3fbab8c2fd0314dc9af7be49f8cca967e4f`.
- `Model card`: https://huggingface.co/pipecat-ai/smart-turn-v3/blob/f766f81d3cfdf7737ac64aad813d91bbfd56bf93/README.md
- Project revision: `4786657e242dfe77dd138699ac564ee074a2a543`.
- Project scope: https://github.com/pipecat-ai/smart-turn/blob/4786657e242dfe77dd138699ac564ee074a2a543/README.md
- Full license text: https://github.com/pipecat-ai/smart-turn/blob/4786657e242dfe77dd138699ac564ee074a2a543/LICENSE
- License-text SHA-256: `0d66364067f678c08586ebb60a16a2aed4fa081ec11057df35585759ce0e774f`.
- Access date for these sources: 2026-10-24.

The `model card` declares `bsd-2-clause`.
The project page has the title `Smart Turn v3.2`.
This sentence sets the rights decision:
“This is a truly open model (BSD 2-clause license).”
The same page states:
“The datasets, training script, and model weights are all open source.”
Its model-weights URL points to the Hugging Face repository above.
These statements connect the `model card` and pinned file to the full license text.
This decision does not use a badge as a license source.

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

- NumPy `2.5.4`.
- ONNX Runtime `1.31.0`.
- Transformers `5.20.2`.

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
The measured count is 100.
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

Measurement start: `2026-10-24T19:50:48.296306+00:00`. End: `2026-10-24T19:50:51.550785+00:00`.
All values below are milliseconds for each candidate.

| Phase | CPU p50 | CPU p90 | Wall p50 | Wall p90 |
|---|---|---|---|---|
| `features` | 5.121 | 7.192 | 5.140 | 7.216 |
| `inference` | 23.792 | 30.169 | 23.935 | 30.660 |
| `total` | 28.993 | 37.218 | 29.164 | 37.972 |

## Reproduction

```sh
uv run pytest tests/test_probe_smart_turn.py -q
uv run python tools/probe_smart_turn.py --record-decision \
  --allow-blocked-absent --candidates 100 --report docs/research/q5-smart-turn.md
uv run python tools/check_assets.py --manifest assets/manifest.json --release
```

The decision command checks release-asset rights before the measurement.
It refuses an unknown status, a hash mismatch, or a blocked asset in the release tree.
An accepted rights record does not claim a built-image check.

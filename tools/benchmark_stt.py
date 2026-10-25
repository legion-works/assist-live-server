"""Compare local STT on disjoint tuning and held-out audio."""

import argparse
import hashlib
import importlib
import json
import math
import os
import re
import statistics
import subprocess
import sys
import time
import wave
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.request import urlopen

import numpy as np
from numpy.typing import NDArray

ROOT = Path(__file__).resolve().parents[1]
SAMPLE_RATE = 16000
PCM_SCALE = 32768
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))

Row = dict[str, Any]
TRAJECTORY_FIELDS = {
    "recording_id",
    "split",
    "audio_sha256",
    "stt_model_id",
    "stt_revision",
    "provider_config_sha256",
    "partials",
}
SMALL = (
    "zero one two three four five six seven eight nine ten eleven twelve "
    "thirteen fourteen fifteen sixteen seventeen eighteen nineteen"
).split()
TENS = "zero ten twenty thirty forty fifty sixty seventy eighty ninety".split()


def spoken_integer(value: int) -> str:
    if value < 20:
        return SMALL[value]
    if value < 100:
        return TENS[value // 10] + (" " + SMALL[value % 10] if value % 10 else "")
    for scale, name in ((1000, "thousand"), (100, "hundred")):
        if value >= scale:
            return (
                spoken_integer(value // scale)
                + " "
                + name
                + (" " + spoken_integer(value % scale) if value % scale else "")
            )
    raise ValueError("invalid integer")


def words(text: str) -> list[str]:
    tokens = re.findall(r"[a-z]+(?:'[a-z]+)?|[0-9]+", text.lower())
    return [
        part
        for token in tokens
        for part in (
            spoken_integer(int(token)).split()
            if token.isdigit() and len(token) <= 6
            else [token]
        )
    ]


def word_errors(reference: str, hypothesis: str) -> tuple[int, int]:
    from tools.probe_tts import word_error_rate as pooled_rate

    expected, actual = words(reference), words(hypothesis)
    if not expected:
        return len(actual), 0
    count = len(expected)
    rate = pooled_rate([(" ".join(expected), " ".join(actual))])
    return round(rate * count), count


def word_error_rate(reference: str, hypothesis: str) -> float:
    errors, count = word_errors(reference, hypothesis)
    return errors / max(1, count)


def verified_audio(label: Row, audio_root: Path) -> NDArray[np.float32]:
    relative = Path(label["audio_path"])
    path = (audio_root / relative).resolve()
    if (
        relative.is_absolute()
        or ".." in relative.parts
        or not path.is_relative_to(audio_root.resolve())
    ):
        raise ValueError("audio path must stay inside audio root")
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != label["audio_sha256"]:
        raise ValueError("audio SHA-256 mismatch")
    with wave.open(str(path), "rb") as wav:
        if (
            wav.getnchannels(),
            wav.getsampwidth(),
            wav.getframerate(),
            wav.getcomptype(),
        ) != (1, 2, SAMPLE_RATE, "NONE"):
            raise ValueError("audio must be mono 16 kHz int16 PCM WAV")
        pcm = wav.readframes(wav.getnframes())
        if len(pcm) != wav.getnframes() * 2 or not pcm:
            raise ValueError("truncated or empty audio")
    return np.frombuffer(pcm, dtype="<i2").astype(np.float32) / np.float32(PCM_SCALE)


def recognition_metrics(reference: str, partials: list[Row]) -> Row:
    expected = words(reference)
    if not expected:
        raise ValueError("empty reference transcript")
    actual = words(partials[-1]["text"])
    errors, count = word_errors(reference, partials[-1]["text"])
    bearing = [p["audio_ms"] for p in partials if expected[0] in words(p["text"])]
    initial = next((words(p["text"]) for p in partials if words(p["text"])), [])
    return {
        "errors": errors,
        "words": count,
        "first_word": bool(actual and actual[0] == expected[0]),
        "exact": actual == expected,
        "first_reference_word_audio_ms": bearing[0] if bearing else None,
        "initial_nonreference": bool(initial and initial[0] != expected[0]),
    }


def runtime_configuration(candidate: Row, threads: int) -> Row:
    declared = candidate["configuration"]
    compatible = candidate["model_id"] == "nvidia/parakeet-tdt-0.6b-v2"
    milliseconds = declared["interval_ms" if compatible else "feed_ms"]
    step = milliseconds * SAMPLE_RATE / 1000
    if threads < 1 or step < 1 or not float(step).is_integer():
        raise ValueError("positive integer thread count and audio step required")
    config: Row = {
        "sample_rate": SAMPLE_RATE,
        "pcm_scale": PCM_SCALE,
        "sample_dtype": "float32",
        "threads": threads,
        "step_samples": int(step),
        "model_files": sorted(
            (
                {key: record[key] for key in ("file", "url", "sha256")}
                for record in candidate["files"]
            ),
            key=lambda r: r["file"],
        ),
    }
    if compatible:
        config.update(
            runtime="onnx-asr==0.12.0",
            onnxruntime="1.31.0",
            model="nemo-parakeet-tdt-0.6b-v2",
            quantization="int8",
            inter_op_threads=1,
            provider="CPUExecutionProvider",
            preprocessor={"use_numpy_preprocessors": True, "max_concurrent_workers": 1},
            tail_samples=0,
        )
    else:
        tail = declared["tail_samples"]
        if type(tail) is not int or tail < 0:
            raise ValueError("nonnegative integer tail required")
        config.update(
            runtime="sherpa-onnx==2.0.0",
            core="sherpa-onnx-core==2.0.0",
            tail_samples=tail,
            feature_dim=declared["feature_dim"],
            decoding_method=declared["decoding_method"],
            endpoint_detection=declared["endpoint_detection"],
            provider=declared["provider"],
        )
    return config


def export_trajectories(rows: list[Row], target: Path) -> None:
    seen = set()
    for row in rows:
        if (
            set(row) != TRAJECTORY_FIELDS
            or not row["recording_id"]
            or row["recording_id"] in seen
        ):
            raise ValueError("invalid or duplicate trajectory identity")
        seen.add(row["recording_id"])
        if row["split"] not in ("tuning", "held_out"):
            raise ValueError("invalid trajectory split")
        for key in ("audio_sha256", "provider_config_sha256"):
            if not re.fullmatch(r"[0-9a-f]{64}", row[key]):
                raise ValueError("invalid trajectory digest")
        if not row["stt_model_id"] or not row["stt_revision"] or not row["partials"]:
            raise ValueError("missing frozen identity or partials")
        previous = -1.0
        for partial in row["partials"]:
            if set(partial) != {"audio_ms", "text"} or not isinstance(
                partial["text"], str
            ):
                raise ValueError("invalid partial fields")
            stamp = partial["audio_ms"]
            if type(stamp) not in (int, float) or not math.isfinite(stamp) or stamp < 0:
                raise ValueError("invalid partial timestamp")
            if stamp < previous:
                raise ValueError("partial timestamps must be monotonic")
            previous = stamp
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows))


def summarize(rows: list[Row]) -> Row:
    if not rows:
        raise ValueError("no measurements")
    partials = [
        r["first_partial_ms"] for r in rows if r["first_partial_ms"] is not None
    ]
    return {
        "recordings": len(rows),
        "errors": sum(r["errors"] for r in rows),
        "words": sum(r["words"] for r in rows),
        "exact": sum(r["exact"] for r in rows),
        "first_word": sum(r["first_word"] for r in rows),
        "partial_count": len(partials),
        "first_partial_ms": statistics.median(partials) if partials else math.inf,
        "final_latency_ms": statistics.median(r["final_latency_ms"] for r in rows),
    }


def select_candidate(rows: list[Row]) -> str:
    if not rows or any(r["split"] != "tuning" for r in rows):
        raise ValueError("selection requires tuning only")
    summaries = {
        name: summarize([r for r in rows if r["candidate"] == name])
        for name in sorted({r["candidate"] for r in rows})
    }
    baseline = summaries["parakeet"]
    eligible = []
    for name, score in summaries.items():
        if name == "parakeet":
            continue
        if (
            score["recordings"] != baseline["recordings"]
            or score["words"] != baseline["words"]
        ):
            raise ValueError("candidate denominators differ")
        if (
            score["errors"] <= baseline["errors"] + 0.01 * baseline["words"]
            and score["first_word"] >= baseline["first_word"]
            and score["partial_count"] == score["recordings"]
            and score["first_partial_ms"] < baseline["first_partial_ms"]
            and score["final_latency_ms"] <= baseline["final_latency_ms"]
        ):
            eligible.append(name)
    return (
        min(
            eligible,
            key=lambda name: (
                summaries[name]["errors"],
                summaries[name]["first_partial_ms"],
                name,
            ),
        )
        if eligible
        else "parakeet"
    )


def paired_bootstrap(candidate: list[Row], baseline: list[Row]) -> Row:
    a = {r["recording_id"]: r for r in candidate}
    b = {r["recording_id"]: r for r in baseline}
    if not a or len(a) != len(candidate) or set(a) != set(b):
        raise ValueError("bootstrap requires paired recordings")
    ids = sorted(a)
    if any(a[key]["words"] != b[key]["words"] for key in ids):
        raise ValueError("bootstrap requires paired word denominators")
    denominators = np.array([a[key]["words"] for key in ids])
    delta = np.array([a[key]["errors"] - b[key]["errors"] for key in ids])
    rng = np.random.default_rng(20261025)
    indices = rng.integers(0, len(ids), size=(5000, len(ids)))
    samples = delta[indices].sum(axis=1) / denominators[indices].sum(axis=1) * 100
    low, high = np.percentile(samples, [2.5, 97.5])
    return {
        "difference_pp": float(delta.sum() / denominators.sum() * 100),
        "low_pp": float(low),
        "high_pp": float(high),
    }


def run_comparison(
    labels: list[Row],
    candidates: list[str],
    decode: Callable[[Row, str], Row],
    freeze: Path,
    identity: Row,
) -> tuple[Row, list[Row]]:
    if freeze.exists():
        raise ValueError("freeze exists; evaluation must use --split held_out")
    if any(row["split"] not in ("tuning", "held_out") for row in labels):
        raise ValueError("unknown split")
    tuning = [row for row in labels if row["split"] == "tuning"]
    if not tuning:
        raise ValueError("no tuning rows")
    results = [decode(row, name) for name in candidates for row in tuning]
    selected = select_candidate(results)
    frozen = {**identity, "candidate": selected}
    freeze.parent.mkdir(parents=True, exist_ok=True)
    with freeze.open("x") as stream:
        json.dump(frozen, stream, sort_keys=True, indent=2)
    held_out = [row for row in labels if row["split"] == "held_out"]
    results.extend(decode(row, name) for name in candidates for row in held_out)
    return frozen, results


def fetch_files(candidate: Row, root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for record in candidate["files"]:
        target = root / record["file"]
        if not target.exists():
            with urlopen(record["url"], timeout=240) as response:
                data = response.read()
            if hashlib.sha256(data).hexdigest() != record["sha256"]:
                raise ValueError("model download SHA-256 mismatch")
            target.write_bytes(data)
        if hashlib.sha256(target.read_bytes()).hexdigest() != record["sha256"]:
            raise ValueError("model cache SHA-256 mismatch")


class Decoder:
    def __init__(self, name: str, candidate: Row, root: Path, threads: int) -> None:
        self.name, self.candidate = name, candidate
        self.config = runtime_configuration(candidate, threads)
        fetch_files(candidate, root)
        if name == "parakeet":
            ort = importlib.import_module("onnxruntime")
            options = ort.SessionOptions()
            options.intra_op_num_threads = self.config["threads"]
            options.inter_op_num_threads = self.config["inter_op_threads"]
            self.model = importlib.import_module("onnx_asr").load_model(
                self.config["model"],
                root,
                quantization=self.config["quantization"],
                sess_options=options,
                providers=[self.config["provider"]],
                preprocessor_config=dict(self.config["preprocessor"]),
            )
        else:
            files = self.config["model_files"]

            def path(prefix: str) -> str:
                return str(
                    root
                    / next(r["file"] for r in files if r["file"].startswith(prefix))
                )

            self.model = importlib.import_module(
                "sherpa_onnx"
            ).OnlineRecognizer.from_transducer(
                encoder=path("encoder"),
                decoder=path("decoder"),
                joiner=path("joiner"),
                tokens=path("tokens"),
                num_threads=self.config["threads"],
                sample_rate=self.config["sample_rate"],
                feature_dim=self.config["feature_dim"],
                decoding_method=self.config["decoding_method"],
                enable_endpoint_detection=self.config["endpoint_detection"],
                provider=self.config["provider"],
            )

    def decode(self, samples: NDArray[np.float32]) -> Row:
        partials = []
        first = None
        virtual_ms = 0.0
        rate = self.config["sample_rate"]
        duration_ms = len(samples) / (rate / 1000)
        step = self.config["step_samples"]
        stream = None if self.name == "parakeet" else self.model.create_stream()
        total_compute = 0.0
        for offset in range(0, len(samples), step):
            end = min(offset + step, len(samples))
            audio_ms = end / (rate / 1000)
            start = time.perf_counter()
            if stream is None:
                text = self.model.recognize(samples[:end], sample_rate=rate)
            else:
                stream.accept_waveform(rate, samples[offset:end])
                while self.model.is_ready(stream):
                    self.model.decode_stream(stream)
                text = self.model.get_result(stream)
            elapsed = (time.perf_counter() - start) * 1000
            total_compute += elapsed
            virtual_ms = max(virtual_ms, audio_ms) + elapsed
            if text.strip() and first is None:
                first = virtual_ms
            partials.append({"audio_ms": audio_ms, "text": text})
        if stream is not None:
            start = time.perf_counter()
            # Tail padding flushes cached frames. It is not source audio.
            stream.accept_waveform(
                rate,
                np.zeros(self.config["tail_samples"], dtype=np.float32),
            )
            stream.input_finished()
            while self.model.is_ready(stream):
                self.model.decode_stream(stream)
            text = self.model.get_result(stream)
            elapsed = (time.perf_counter() - start) * 1000
            total_compute += elapsed
            virtual_ms = max(virtual_ms, duration_ms) + elapsed
            if text.strip() and first is None:
                first = virtual_ms
            partials.append({"audio_ms": duration_ms, "text": text})
        return {
            "partials": partials,
            "first_partial_ms": first,
            "final_latency_ms": max(0, virtual_ms - duration_ms),
            "compute_ms": total_compute,
        }


def provider_identity(candidate: Row, threads: int) -> Row:
    config = runtime_configuration(candidate, threads)
    return {
        "stt_model_id": candidate["model_id"],
        "stt_revision": candidate["revision"],
        "provider_config_sha256": hashlib.sha256(
            json.dumps(config, sort_keys=True).encode()
        ).hexdigest(),
    }


def render_report(
    frozen: Row, results: list[Row], candidates: Row, environment: Row, manifest: Row
) -> str:
    from tools.asset_rights import render_sources

    evidence = manifest["stt_benchmark_evidence"]
    table = []
    intervals = []
    for split in ("tuning", "held_out"):
        for name in candidates:
            rows = [
                r for r in results if r["split"] == split and r["candidate"] == name
            ]
            if not rows:
                continue
            s = summarize(rows)
            bearing = [
                r["first_reference_word_audio_ms"]
                for r in rows
                if r["first_reference_word_audio_ms"] is not None
            ]
            bearing_text = f"{statistics.median(bearing):.2f}" if bearing else "N/A"
            table.append(
                f"| {split} | {name} | {s['recordings']} | "
                f"{s['errors']}/{s['words']} ({s['errors'] / s['words']:.4%}) | "
                f"{s['exact']}/{s['recordings']} | "
                f"{s['first_word']}/{s['recordings']} | "
                f"{s['first_partial_ms']:.2f} "
                f"({s['partial_count']}/{s['recordings']}) | "
                f"{bearing_text} ({len(bearing)}/{s['recordings']}) | "
                f"{s['final_latency_ms']:.2f} |"
            )
            if split == "held_out" and name != "parakeet":
                ci = paired_bootstrap(
                    rows,
                    [
                        r
                        for r in results
                        if r["split"] == split and r["candidate"] == "parakeet"
                    ],
                )
                intervals.append(
                    f"- `{name}` minus Parakeet: {ci['difference_pp']:.3f} "
                    f"percentage points, 95% interval "
                    f"[{ci['low_pp']:.3f}, {ci['high_pp']:.3f}]."
                )
    decision = frozen["candidate"]
    decision_text = (
        "No streaming default. Parakeet stays the local compatible default."
        if decision == "parakeet"
        else "The streaming selection uses tuning measurements only."
    )
    header = (
        "| Split | Candidate | Recordings | Word errors/reference words | "
        "Exact transcripts | First word | First partial median ms (coverage) | "
        "First reference-word audio ms (coverage) | "
        "Final median ms |"
    )
    onset_table = []
    for split in ("tuning", "held_out"):
        for name in candidates:
            for band in ("<150 ms", "150 to 300 ms", ">300 ms"):
                matching = []
                for row in results:
                    onset = row.get("leading_silence_ms")
                    if (
                        row["split"] != split
                        or row["candidate"] != name
                        or onset is None
                    ):
                        continue
                    row_band = (
                        "<150 ms"
                        if onset < 150
                        else "150 to 300 ms"
                        if onset <= 300
                        else ">300 ms"
                    )
                    if row_band == band:
                        matching.append(row)
                onset_table.append(
                    f"| {split} | {name} | {band} | "
                    f"{sum(r['first_word'] for r in matching)}/{len(matching)} |"
                )
    modes = "\n".join(
        f"- `{name}`: `{c['configuration']['decode_mode']}`. "
        f"Revision: `{c['revision']}`."
        for name, c in candidates.items()
    )
    initial_table = []
    for split in ("tuning", "held_out"):
        counts = []
        for name in candidates:
            initial = next(
                r
                for r in evidence["initial_results"]
                if r["split"] == split and r["candidate"] == name
            )
            counts.append(f"{initial['errors']}/{initial['words']}")
        initial_table.append(f"| {split} | " + " | ".join(counts) + " |")
    audit = evidence["flush_audit"]
    upstream_table = "\n".join(
        f"| {r['candidate']} | `{r['sample']}.wav` | {r['errors']}/{r['words']} |"
        for r in evidence["upstream_wavs"]
    )
    indexed = {(r["recording_id"], r["candidate"]): r for r in results}
    examples = []
    for row in [
        r for r in results if r["split"] == "tuning" and r["candidate"] == "parakeet"
    ][:5]:
        hypotheses = [
            indexed[row["recording_id"], name]["trajectory"]["partials"][-1]["text"]
            for name in candidates
        ]
        examples.append(
            "| `" + row["reference"] + "` | `" + "` | `".join(hypotheses) + "` |"
        )
    license_records = [
        r
        for r in manifest["assets"]
        if r["id"] in ("stt-parakeet", "stt-sherpa-zipformer", "stt-fastconformer")
    ]
    licenses = "\n\n".join(
        f"### `{r['id']}`\n\nLicense: `{r['license']}`.\n\n"
        + (f"```text\n{r['license_detail']}\n```" if r.get("license_detail") else "")
        + "\n\n"
        + render_sources(r)
        for r in license_records
    )
    parakeet = [r for r in results if r["candidate"] == "parakeet"]
    fillers = sum(r["initial_nonreference"] for r in parakeet)
    return f"""# Local STT measurements

Date: 2026-10-25. The project is experimental and has no release.

## Decision

Selected provider: `{decision}`.
{decision_text}
The selection does not change after the held-out test.
Pause and prefix sets have 0 rows. They supply no acceptance result.

The HA harness must measure `intent accuracy`.
`Transcript accuracy` is not `intent accuracy`.

## Method

All candidates decode the same audio, without corpus transcripts as decoder inputs.
Selection uses 50 tuning rows. The held-out test uses 200 different rows.
The tool checks audio paths and SHA-256 before decode.
The tuning WER limit is Parakeet WER plus 1.0 percentage point absolute.
This margin limits selection noise from single-word errors in the small tuning set.

First-word preservation must be no lower than Parakeet.
Median first-partial latency must be lower than Parakeet.
Median `final latency` must be no higher than Parakeet.
Each recording must have a nonempty transcript.
Equal scores use WER, then first-partial latency, then candidate name.
No candidate uses a license as a selection rule.

WER uses lowercase words without punctuation.
Integer digits become English words before the test.
A missing first word counts as a deletion.
The first-word metric compares the first normalized word.
`Exact transcript accuracy` compares the complete normalized word sequences.

First-partial latency starts at audio start.
`Final latency` starts at the end of the source WAV.
An arrival-time simulation adds each measured decode cost.
It adds the backlog to the next chunk.
The tool does not sleep between chunks. Measurements do not include model load time.

Streaming decoders keep their caches.
Their `final flush` adds 660 ms of 0 samples, without a 660 ms wait.
The flush cost counts in `final latency`.
Its trajectory timestamp stays at source-audio end.
These CPU measurements are not target-device or live microphone acceptance.

The full-buffer decoder usually gives filler text before voice onset.
For {fillers}/{len(parakeet)} recordings, its first nonempty transcript does not start
with the corpus first word. Examples include `Okay.` and `Mm.`.
This is not the time to the first correct command word.

The second latency column uses the first transcript with the corpus first word.
It uses normalized word tokens and source-audio time, without decode CPU cost.
Its denominator counts recordings with that word.
This information and the filler count do not change the frozen selection rule.

{modes}

## Environment

```json
{json.dumps(environment, indent=2, sort_keys=True)}
```

The two benchmark runtimes are development dependencies with CPython 3.14 wheels.
No scratch interpreter or server runtime dependency is necessary.
The clock uses UTC.
`OMP_NUM_THREADS`, `OPENBLAS_NUM_THREADS`, and `MKL_NUM_THREADS` each have value `1`.

## Results

{header}
|---|---|---:|---|---|---|---|---|---:|
{chr(10).join(table)}

## Held-out paired WER differences

The paired bootstrap uses 5000 recording-level resamples and seed `20261025`.
Each resample uses the same recording indices for the two models.
Its denominator is the sum of reference-word counts.
The interval uses the 2.5th and 97.5th percentiles.
It supplies information only and cannot change selection.

{chr(10).join(intervals)}

## First words by onset time

These counts supply information only. They cannot change selection.
The onset is `alignment_words[0].start_sample / 16` in ms.
The onset comes from automatic corpus `alignment_words`, not human onset records.
No left padding or other audio change occurs for this test.

| Split | Candidate | Leading silence | First word/recordings |
|---|---|---|---|
{chr(10).join(onset_table)}

## Frozen selection

```json
{json.dumps(frozen, indent=2, sort_keys=True)}
```

## Transcript trajectory contract

`docs/reports/q6-partials.jsonl` has one selected-provider row for each recording.
Each row has these fields and no other fields:

| Field | Contract |
|---|---|
| `recording_id` | The corpus `id` |
| `split` | `tuning` or `held_out` |
| `audio_sha256` | The source WAV SHA-256 |
| `stt_model_id` | The selected model identifier |
| `stt_revision` | The frozen model revision |
| `provider_config_sha256` | SHA-256 of sorted JSON runtime parameters |
| `partials` | A nonempty array of objects with `audio_ms` and `text` only |

`audio_ms` is finite, nonnegative, monotonic, and at most the source-audio length.
`text` is a string. Empty strings record observations before the first word.
The consumer must align each corpus record with the frozen identity before replay.
All-candidate measurements stay in the external benchmark cache.
Only a complete two-split command writes the public report and export.
Commands with `--split` write their external measurement cache only.

## Initial test and corrected tail

The initial test used a 500 ms streaming tail from the runtime class example.
The upstream file-decoding example uses 660 ms.
An audit found missing Zipformer word endings at 500 ms.
The corrected test uses 660 ms and a different external freeze record.
The input audio, model files, selection rule, and 1.0 percentage-point WER margin
stay the same. Each complete test selects on tuning before held-out scores.

Initial word-error counts:

| Split | Parakeet | Zipformer | FastConformer |
|---|---|---|---|
{chr(10).join(initial_table)}

The 660 ms audit changes {audit["zipformer_660_changes"]}/{audit["tuning_recordings"]}
Zipformer tuning transcripts against the 500 ms test.
A 1000 ms diagnostic changes
{audit["zipformer_1000_changes"]}/{audit["tuning_recordings"]}.
One more recording changes `MIN` to `MINUTES` at 1000 ms, but not at 660 ms.
The configuration keeps the approved upstream-example 660 ms value.
This remaining tail sensitivity does not change the configuration
after the held-out test.

FastConformer has {audit["fastconformer_changes"]} transcript changes
in 150 audit tests.
Whole-buffer input and 80 ms input have {audit["whole_buffer_changes"]} transcript
differences at the same 500 ms tail.
These are diagnostic tests, not a new selection rule.

The source-cache hashes below identify the stored historical summaries and audit data.
The tool reads these records from `assets/manifest.json`.
It does not read a prior Markdown report to collect disclosures.

```json
{json.dumps(evidence["source_cache_sha256"], indent=2, sort_keys=True)}
```

## Harness checks

The two models' supplied WAVs have the same hashes across their repositories.
Each uses its supplied transcript file. The corrected harness gives these counts:

| Model | Sample | Word errors/words |
|---|---|---|
{upstream_table}

The single error is `DISHONOURED` versus `DISHONORED` for each model.
No model-specific spelling correction occurs.
The Parakeet tutorial sample has first-word preservation for each model.
The stored first-word counts are:

```json
{json.dumps(evidence["reference_first_words"], indent=2, sort_keys=True)}
```

These files stay external to the repository.
LibriSpeech supplies them with the CC-BY-4.0 license.
Credit: the LibriSpeech and OpenSLR projects.

Five tuning rows show raw model output, without model-specific text normalization:

| Corpus transcript | Parakeet | Zipformer | FastConformer |
|---|---|---|---|
{chr(10).join(examples)}

The shared normalization uses lowercase words, removes punctuation, and expands digits.
For example, `949` and `nine hundred forty nine` become the same four words.
It does not remove a spoken `and` or correct `SAID` to `set`.

The input checker accepts mono 16 kHz int16 PCM WAV only.
Samples use float32 values divided by 32768.
There is no added left padding, as in the upstream file example.
The tail is 10560 samples at 16 kHz, or 660 ms.
Each stream receives `input_finished()` and decodes while `is_ready()` gives `True`.

Endpoint detection stays off. No `reset` or endpoint cuts source audio.

The larger Zipformer encoder and joiner use int8, with the fp32 decoder from
the same `epoch-99-avg-1-chunk-16-left-128` set. This is not the rejected 20M model.
FastConformer uses the int8 encoder, decoder, and joiner from the same 80 ms model.
The encoder metadata is:

```json
{json.dumps(audit["metadata"], indent=2, sort_keys=True)}
```

The difference applies to these configurations and this timer corpus.
It is not a claim about all read English or other model variants.
First-word errors also occur with more than 300 ms of source lead-in.
The small earlier-onset groups do not show an onset effect.

## Provider follow-up

Full-buffer decoding before voice onset gives filler text.
The provider must gate full-buffer decoding on `VAD speech onset`.
This is a provider follow-up, not a change to this frozen benchmark rule or decision.

## License scope

License terms do not control technical selection.
The two streaming models have status `not_selected` for their measured results.
FastConformer upstream weights use NVIDIA NGC Terms of Use.
Its ONNX repository has no README or other license statement.
`NOASSERTION` records no SPDX assertion for those bytes, not a claim of no license.

{licenses}

## Source records

Access date: 2026-10-25. File pins and license quotations are in `assets/manifest.json`.

- Parakeet: https://huggingface.co/istupakov/parakeet-tdt-0.6b-v2-onnx
- Zipformer: https://huggingface.co/csukuangfj/sherpa-onnx-streaming-zipformer-en-2023-06-26
- FastConformer: https://huggingface.co/csukuangfj/sherpa-onnx-nemo-streaming-fast-conformer-transducer-en-80ms-int8
- Runtime examples: https://github.com/k2-fsa/sherpa-onnx/blob/master/python-api-examples/online-decode-files.py
- Runtime API: https://istupakov.github.io/onnx-asr/usage/
- Pinned file-decoding example: {evidence["upstream_example"]}
- Zipformer sample transcripts: https://huggingface.co/csukuangfj/sherpa-onnx-streaming-zipformer-en-2023-06-26/blob/672fbf1b30579d6585301139bb363f42a0ad4a24/test_wavs/trans.txt
- FastConformer sample transcripts: https://huggingface.co/csukuangfj/sherpa-onnx-nemo-streaming-fast-conformer-transducer-en-80ms-int8/blob/3fafd319033af1c552e7bc8394f7258afdce91b0/test_wavs/trans.txt
- Sample audio license: https://www.openslr.org/12/ (statement: `License: CC BY 4.0`).
- Parakeet tutorial sample: https://dldata-public.s3.us-east-2.amazonaws.com/2086-149220-0033.wav
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--audio-root", type=Path, required=True)
    parser.add_argument("--selection-split", choices=["tuning"], default="tuning")
    parser.add_argument("--gate-split", choices=["held_out"], default="held_out")
    parser.add_argument("--split", choices=["tuning", "held_out"])
    parser.add_argument("--partials-output", type=Path, required=True)
    parser.add_argument(
        "--candidates", default="parakeet,sherpa-zipformer,fastconformer"
    )
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument(
        "--model-root",
        type=Path,
        default=Path(
            os.environ.get(
                "STT_MODEL_ROOT", str(Path.home() / ".cache" / "assist-live" / "stt")
            )
        ),
    )
    parser.add_argument("--threads", type=int, default=4)
    args = parser.parse_args(argv)
    try:
        from tools.check_assets import check_assets

        if errors := check_assets(ROOT / "assets/manifest.json", release=True):
            raise ValueError("; ".join(errors))
        print("PASS asset licenses", flush=True)
        if args.threads < 1:
            raise ValueError("threads must be positive")
        manifest = json.loads((ROOT / "assets/manifest.json").read_text())
        candidates = {
            name: manifest["stt_benchmark"][name] for name in args.candidates.split(",")
        }
        if set(candidates) != {"parakeet", "sherpa-zipformer", "fastconformer"}:
            raise ValueError("comparison requires the three declared candidates")
        labels = [
            json.loads(line)
            for line in args.labels.read_text().splitlines()
            if line.strip()
        ]
        if len({r["id"] for r in labels}) != len(labels):
            raise ValueError("duplicate recording id")
        if {
            split: sum(r["split"] == split for r in labels)
            for split in ("tuning", "held_out")
        } != {"tuning": 50, "held_out": 200}:
            raise ValueError("comparison needs 50 tuning and 200 held-out rows")
        for label in labels:
            if not words(label["transcript"]):
                raise ValueError(f"empty reference transcript: {label['id']}")
        identity: Row = {
            "label_sha256": hashlib.sha256(args.labels.read_bytes()).hexdigest(),
            "selection_rule": {
                "wer_margin_pp": 1.0,
                "first_word": "no worse",
                "first_partial": "lower median",
                "final_latency": "no worse median",
            },
            "providers": {
                name: provider_identity(c, args.threads)
                for name, c in candidates.items()
            },
        }
        freeze = args.model_root / "selection.json"
        environment = {
            "cpu": next(
                (
                    line.partition(":")[2].strip()
                    for line in Path("/proc/cpuinfo").read_text().splitlines()
                    if line.startswith("model name")
                ),
                "unknown",
            ),
            "logical_threads": os.cpu_count(),
            "threads": args.threads,
            "load_average_start": list(os.getloadavg()),
            "uptime": subprocess.check_output(["uptime"], text=True).strip(),
            "python": sys.version.split()[0],
            "runtimes": {
                "onnx-asr": "0.12.0",
                "sherpa-onnx": "2.0.0",
                "onnxruntime": "1.31.0",
            },
            "effective_provider_configurations": {
                name: runtime_configuration(candidate, args.threads)
                for name, candidate in candidates.items()
            },
        }
        decoders: dict[str, Decoder] = {}

        def decode(label: Row, name: str) -> Row:
            samples = verified_audio(label, args.audio_root)
            if name not in decoders:
                decoders[name] = Decoder(
                    name, candidates[name], args.model_root / name, args.threads
                )
            result = decoders[name].decode(samples)
            metrics = recognition_metrics(label["transcript"], result["partials"])
            trajectory = {
                "recording_id": label["id"],
                "split": label["split"],
                "audio_sha256": label["audio_sha256"],
                **identity["providers"][name],
                "partials": result.pop("partials"),
            }
            return {
                **result,
                "recording_id": label["id"],
                "split": label["split"],
                "candidate": name,
                **metrics,
                "reference": label["transcript"],
                "trajectory": trajectory,
                "leading_silence_ms": label["alignment_words"][0]["start_sample"] / 16,
            }

        if args.split == "held_out":
            frozen = json.loads(freeze.read_text())
            if any(frozen[k] != v for k, v in identity.items()):
                raise ValueError("frozen input or provider identity mismatch")
            results = [
                decode(row, name)
                for name in candidates
                for row in labels
                if row["split"] == "held_out"
            ]
            tuning_cache = args.model_root / "measurements-tuning.json"
            if tuning_cache.is_file():
                tuning_results = json.loads(tuning_cache.read_text())
                if any(r["split"] != "tuning" for r in tuning_results):
                    raise ValueError("tuning cache contains non-tuning results")
                results = tuning_results + results
        else:
            chosen_labels = (
                [r for r in labels if r["split"] == "tuning"]
                if args.split == "tuning"
                else labels
            )
            frozen, results = run_comparison(
                chosen_labels, list(candidates), decode, freeze, identity
            )
        args.model_root.mkdir(parents=True, exist_ok=True)
        (
            args.model_root / ("measurements-" + (args.split or "both") + ".json")
        ).write_text(json.dumps(results, sort_keys=True))
        if args.split is None:
            if any(
                sum(r["candidate"] == name and r["split"] == split for r in results)
                != count
                for name in candidates
                for split, count in (("tuning", 50), ("held_out", 200))
            ):
                raise ValueError(
                    "complete export needs both splits for every candidate"
                )
            report = render_report(
                frozen,
                results,
                candidates,
                environment,
                manifest,
            )
            export_trajectories(
                [
                    r["trajectory"]
                    for r in results
                    if r["candidate"] == frozen["candidate"]
                ],
                args.partials_output,
            )
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(report)
        else:
            print(f"PASS Q6 split: {args.split}; public report and export unchanged")
        for split in ("tuning", "held_out"):
            for name in candidates:
                rows = [
                    r for r in results if r["split"] == split and r["candidate"] == name
                ]
                if rows:
                    print(
                        split,
                        name,
                        json.dumps(summarize(rows), sort_keys=True),
                        flush=True,
                    )
        print(
            f"PASS Q6 comparison: {frozen['candidate']}"
            + ("; no streaming default" if frozen["candidate"] == "parakeet" else "")
        )
        return 0
    except (ValueError, OSError, KeyError) as error:
        print(f"FAIL Q6 comparison: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Read local TTS measurements offline. Synthesis needs --live and existing models."""

import argparse
import csv
import hashlib
import importlib
import importlib.metadata
import json
import math
import os
import resource
import statistics
import sys
import tempfile
import time
import wave
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))


def summarize(rows: list[dict[str, Any]]) -> dict[str, float | int]:
    if not rows:
        raise ValueError("measurement rows required")
    values = []
    for row in rows:
        numbers = {
            key: float(row[key]) for key in ("first_s", "total_s", "audio_s", "rss_kib")
        }
        if not all(math.isfinite(n) and n >= 0 for n in numbers.values()):
            raise ValueError("finite non-negative measurements required")
        if numbers["audio_s"] <= 0 or numbers["first_s"] > numbers["total_s"]:
            raise ValueError("positive audio duration and ordered clocks required")
        values.append(numbers)
    return {
        "requests": len(values),
        "first_ms_p50": statistics.median(row["first_s"] for row in values) * 1000,
        "first_ms_max": max(row["first_s"] for row in values) * 1000,
        "rtf": sum(row["total_s"] for row in values)
        / sum(row["audio_s"] for row in values),
        "rss_mib": max(row["rss_kib"] for row in values) / 1024,
    }


def word_error_rate(pairs: list[tuple[str, str]]) -> float:
    """Use normalized input pairs and pool word edits across the corpus."""
    errors = words = 0
    for reference, hypothesis in pairs:
        ref, hyp = reference.split(), hypothesis.split()
        previous = list(range(len(hyp) + 1))
        for i, word in enumerate(ref, 1):
            current = [i]
            for j, other in enumerate(hyp, 1):
                current.append(
                    min(
                        current[-1] + 1,
                        previous[j] + 1,
                        previous[j - 1] + (word != other),
                    )
                )
            previous = current
        errors += previous[-1]
        words += len(ref)
    if not words:
        raise ValueError("reference words required")
    return errors / words


def naturalness(scores: list[float]) -> dict[str, float]:
    if not scores or not all(math.isfinite(score) for score in scores):
        raise ValueError("finite naturalness scores required")
    ordered = sorted(scores)
    position = (len(ordered) - 1) * 0.1
    lower, upper = math.floor(position), math.ceil(position)
    p10 = ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)
    return {
        "mean": statistics.mean(scores),
        "median": statistics.median(scores),
        "p10": p10,
    }


def offline_metrics(
    timings: Path,
    transcripts: Path | None = None,
    scores: Path | None = None,
) -> dict[str, Any]:
    with timings.open() as handle:
        rows = list(csv.DictReader(handle))
    keys = sorted({(row["engine"], row["threads"]) for row in rows})
    result = {}
    for engine, threads in keys:
        subset = [
            row for row in rows if (row["engine"], row["threads"]) == (engine, threads)
        ]
        metrics: dict[str, Any] = summarize(subset)
        groups = {}
        for group in ("short", "medium", "long"):
            selected = [
                row
                for row in subset
                if (
                    "short"
                    if int(row["chars"]) <= 60
                    else "long"
                    if int(row["chars"]) >= 150
                    else "medium"
                )
                == group
            ]
            if selected:
                groups[group] = summarize(selected)
        metrics["groups"] = groups
        if transcripts:
            with transcripts.open() as handle:
                pairs = [
                    (row["reference_norm"], row["hypothesis_norm"])
                    for row in csv.DictReader(handle)
                    if row["engine"] == engine
                    and row["threads"] == threads
                    and row["repeat"] != "warmup"
                ]
            metrics["wer"] = word_error_rate(pairs)
        if scores and threads == "4":
            with scores.open() as handle:
                selected_scores = [
                    float(row["mos"])
                    for row in csv.DictReader(handle)
                    if row["engine"] == engine
                ]
            metrics["naturalness"] = naturalness(selected_scores)
        result[f"{engine}-t{threads}"] = metrics
    if not result:
        raise ValueError("measurement rows required")
    return result


def live_piper(
    directory: Path, records: list[dict[str, Any]], texts: list[str]
) -> None:
    """Use local pinned files only. Do not download models or install packages."""
    paths = {}
    for asset_id in ("piper-lessac-medium", "piper-lessac-config"):
        record = next(item for item in records if item["id"] == asset_id)
        path = directory / "models" / record["url"].rsplit("/", 1)[-1]
        if not path.is_file():
            raise ValueError(f"missing pinned model: {path.name}")
        if hashlib.sha256(path.read_bytes()).hexdigest() != record["sha256"]:
            raise ValueError(f"model digest mismatch: {path.name}")
        paths[asset_id] = path
    if importlib.metadata.version("piper-tts") != "1.8.0":
        raise ValueError("existing piper-tts==1.8.0 installation required")
    for variable in (
        "OMP_NUM_THREADS",
        "MKL_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
    ):
        os.environ[variable] = "4"
    ort = importlib.import_module("onnxruntime")
    piper = importlib.import_module("piper")
    config_module = importlib.import_module("piper.config")
    config = config_module.PiperConfig.from_dict(
        json.loads(paths["piper-lessac-config"].read_text())
    )
    options = ort.SessionOptions()
    options.intra_op_num_threads, options.inter_op_num_threads = 4, 1
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    session = ort.InferenceSession(
        str(paths["piper-lessac-medium"]),
        sess_options=options,
        providers=["CPUExecutionProvider"],
    )
    voice = piper.PiperVoice(config=config, session=session)
    if not texts or not all(text.strip() for text in texts):
        raise ValueError("synthetic text required")
    list(voice.synthesize(texts[0]))
    output = directory / "audio"
    output.mkdir(parents=True, exist_ok=True)
    rows = []
    for repeat in range(5):
        for index, text in enumerate(texts, 1):
            start = time.perf_counter()
            chunks, first, samples = [], None, 0
            for chunk in voice.synthesize(text):
                if len(chunk.audio_float_array):
                    if first is None:
                        first = time.perf_counter() - start
                    samples += len(chunk.audio_float_array)
                    chunks.append(chunk)
            elapsed = time.perf_counter() - start
            if first is None:
                raise ValueError("synthesis returned no audio")
            row = dict(
                engine="piper",
                threads=4,
                repeat=repeat,
                text_id=index,
                chars=len(text),
                first_s=first,
                total_s=elapsed,
                audio_s=samples / config.sample_rate,
                rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            )
            rows.append(row)
            with wave.open(str(output / f"{index:02d}-r{repeat}.wav"), "wb") as handle:
                handle.setnchannels(1)
                handle.setsampwidth(2)
                handle.setframerate(config.sample_rate)
                for chunk in chunks:
                    handle.writeframes(chunk.audio_int16_bytes)
    with (directory / "timings.csv").open("w") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    (directory / "live-summary.json").write_text(
        json.dumps(summarize(rows), indent=2) + "\n"
    )


def render_report(selection: dict[str, Any], records: list[dict[str, Any]]) -> str:
    from tools.asset_rights import render_sources

    table = "\n".join(
        f"| {r['engine']} {r['version']} | {r['voice']} | {r['python']} | "
        f"{r['first_ms_p50']:.2f} / {r['first_ms_max']:.2f} | "
        f"{r['rtf_short']:.4f} / {r['rtf_long']:.4f} | {r['rss_mib']:.1f} | "
        f"{r['wer_percent']:.2f} | {r['naturalness_mean']:.3f} |"
        for r in selection["candidates"]
    )
    sources = "\n\n".join(
        f"### `{r['id']}`\n\nLicense: `{r['license']}`.\n\n"
        f"Source: {r['url']}\n\nSHA-256: `{r['sha256']}`.\n\n"
        f"Attribution:\n\n```text\n{r['attribution']}\n```\n\n" + render_sources(r)
        for r in records
        if r["id"].startswith("piper-")
    )
    header = (
        "| Engine | Voice | Python | First audio p50 / max ms | RTF short / long | "
        "RSS MiB | WER % | UTMOSv2 mean |"
    )
    return f"""# Local TTS selection

## Decision

Select Piper 1.8.0 with `en_US-lessac-medium` and four intra-op `threads`.
Run it in-process on Python 3.14. The project is experimental and has no release.
The selection uses technical measurements, not license terms.
Piper starts audio 35.38 ms before Pocket at the median and uses 669.4 MiB less memory.

Its predicted naturalness is 3.722 versus 3.306.
Pocket has lower WER, 0.13% versus 0.60%.
Kokoro has almost the same naturalness prediction.
It must have 366.19 ms and a Python 3.12 worker.

## Measurements

Measurement date: `{selection["measured_at"]}`.
Hardware class: `{selection["hardware"]}`.
All engines used the CPU. Other processes shared the host.
The test used no CPU affinity or frequency lock.

Each four-thread configuration used one discarded warm-up.
It then ran 30 synthetic English replies five times.
The table pools 150 requests for each configuration.
No new measurement ran for this record.

{header}
|---|---|---|---:|---:|---:|---:|---:|
{table}

First audio means the first non-empty sample array, not playback or the first sound.
The timer starts before generation.
It does not include WAV writes, transport, and server queues.
Piper and Kokoro give sentence arrays. Pocket gives audio frames.
Supertonic gives full-text output.

Short inputs contain a maximum of 60 characters.
Long inputs contain a minimum of 150 characters.
The corpus has 26 short replies, one `medium` reply, and three long replies.
For RTF, divide total synthesis time by total `audio duration`.
It is not a mean of request ratios.
RSS is the Linux process maximum, with native libraries and temporary buffers.

WER uses normalized ASR transcripts from faster-whisper 1.2.1 and small.en.
ASR uses CPU int8 and beam size 5.
The two sides use the same number, time, unit, and punctuation normalization.
The offline probe consumes `reference_norm` and `hypothesis_norm` columns.
It does not run ASR.

UTMOSv2 predicts naturalness, not human preference. No human listening panel ran.
The predictor used revision `cc2700db57bb83ee13dc31ebe1b868c254e15d09`.
It used fusion_stage3, fold 0, and seed 42.
It ran on Python 3.12.13 with Torch 2.14.1+cpu.

It used four intra-op `threads` and one inter-op `thread`.
It calculated scores for repetition 0, with 30 files for each engine.
Each text had the same weight.

The offline probe calculates the mean, median, and 10th percentile.
It uses linear interpolation without clipping.

## Packaging limits

Piper uses ONNX Runtime 1.31.0.
Its session uses four intra-op `threads` and one inter-op `thread`.
It uses sequential execution.
Piper 1.8.0 declares Python `>=3.9`.
It supplies Linux `cp39-abi3` wheels for x86-64 and aarch64.

The test measured x86-64 only.
An aarch64 install and target-host measurement stay necessary.

Kokoro 0.9.4 declares Python `>=3.10,<3.13`.
Its measured worker used 2249.2 MiB without the server.
Worker IPC latency and buffer copies were not measured.
Kokoro ONNX 0.6.1 ran on Python 3.14.3.
Its metadata does not include Python 3.14.
Execution does not prove declared compatibility.

Supertonic used package 1.3.1, model 3, M1, and eight steps.
No TTS runtime is a project dependency at this time.
The live flag must have a Piper installation.
No WAV, model, or raw measurement file is in this repository.
Image inventory: `NOT CHECKED`. There is no image.

## Licenses and sources

Sources accessed: 2026-10-25.
Piper code uses GPL-3.0-or-later, not Apache-2.0.

The Lessac `model card` gives a URL for the custom Blizzard 2013 research license.
It states no model-weight grant.
The weights and configuration have `NOASSERTION` records.
They do not have invented MIT or CC licenses.
The dataset restrictions use `LicenseRef-Blizzard-2013-Research`.

This is not a model-weight license. The dataset is not test audio.
This probe does not include or download dataset recordings.
The technical selection does not change those statements or give publication approval.

The G2P chain uses Piper's bundled eSpeak NG bridge and data.
It does not use a different system library.
Redistributed binaries must keep the upstream licenses and notices.
They must also supply the necessary `corresponding source`.
`THIRD_PARTY_NOTICES.md` records attribution. The project code stays Apache-2.0.

{sources}

## Reproduction

Set `ASSIST_LIVE_PROBE_DIRECTORY` to scratch storage external to the repository.
The default mode reads records only. It imports no synthesis engine and uses no network.
The optional offline CSV inputs can contain measurements from all five engines.

```sh
uv run python tools/probe_tts.py --record-decision \\
  --report docs/research/q7t-local-tts.md
uv run python tools/probe_tts.py --timings "$PROBE/timings.csv" \\
  --transcripts "$PROBE/transcripts.csv" --scores "$PROBE/mos-scores.csv"
uv run pytest tests/test_probe_tts.py -q
```

For an optional live Piper run, supply the pinned ONNX and configuration
in `$ASSIST_LIVE_PROBE_DIRECTORY/models/`.
Use synthetic text only. Run this command with a Piper 1.8.0 environment:

```sh
python tools/probe_tts.py --live --text 'The synthetic timer is set for five minutes.'
```

The probe checks the two file hashes before engine import.
It does not install or download anything. Audio and raw timings stay in scratch storage.
Live output does not replace the recorded results.
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record-decision", action="store_true")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--text", action="append", default=[])
    parser.add_argument("--timings", type=Path)
    parser.add_argument("--transcripts", type=Path)
    parser.add_argument("--scores", type=Path)
    parser.add_argument("--check", type=Path, default=ROOT / "assets/manifest.json")
    parser.add_argument(
        "--report", type=Path, default=ROOT / "docs/research/q7t-local-tts.md"
    )
    args = parser.parse_args(argv)
    from tools.check_assets import check_assets, flatten

    try:
        errors = check_assets(args.check, release=True)
        if errors:
            raise ValueError("; ".join(errors))
        document = json.loads(args.check.read_text())
        selection = document["tts_selection"]
        records = flatten(document["assets"], [])
        selected = next(r for r in records if r["id"] == "piper-runtime")
        if selected["status"] != "included" or selection["engine"] != "piper":
            raise ValueError("selected Piper record required")
        directory = Path(
            os.environ.get(
                "ASSIST_LIVE_PROBE_DIRECTORY",
                str(Path(tempfile.gettempdir()) / "assist-live-tts"),
            )
        )
        if directory.resolve().is_relative_to(ROOT):
            raise ValueError("probe directory must be outside the repository")
        directory.mkdir(parents=True, exist_ok=True)
        if args.live:
            live_piper(directory, records, args.text)
            print("PASS live Piper measurement")
        if args.timings:
            result = offline_metrics(args.timings, args.transcripts, args.scores)
            (directory / "offline-summary.json").write_text(
                json.dumps(result, indent=2) + "\n"
            )
            print("PASS offline TTS metrics")
        if args.record_decision:
            (directory / "decision.json").write_text(
                json.dumps(selection, indent=2) + "\n"
            )
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(render_report(selection, records))
            print("PASS TTS selection recorded")
        print("PASS TTS records")
        return 0
    except (OSError, ValueError, KeyError, StopIteration, ImportError) as error:
        print(f"FAIL TTS probe: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

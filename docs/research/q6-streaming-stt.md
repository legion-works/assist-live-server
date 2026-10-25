# Local STT measurements

Date: 2026-10-25. The project is experimental and has no release.

## Decision

Selected provider: `parakeet`.
No streaming default. Parakeet stays the local compatible default.
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

- `parakeet`: `repeated full-buffer decode at 400 ms intervals (not streaming)`. Revision: `0bbb45a3365852604aef28b538a8f066f4ccaa85`.
- `sherpa-zipformer`: `cache-aware streaming transducer, chunk16 left128`. Revision: `672fbf1b30579d6585301139bb363f42a0ad4a24`.
- `fastconformer`: `cache-aware NVIDIA FastConformer streaming transducer, 80 ms look-ahead`. Revision: `3fafd319033af1c552e7bc8394f7258afdce91b0`.

## Environment

```json
{
  "cpu": "AMD RYZEN AI MAX+ 395 w/ Radeon 8060S",
  "load_average_start": [
    11.51708984375,
    12.71044921875,
    14.71533203125
  ],
  "logical_threads": 32,
  "python": "3.14.3",
  "runtimes": {
    "onnx-asr": "0.12.0",
    "onnxruntime": "1.31.0",
    "sherpa-onnx": "2.0.0"
  },
  "threads": 4,
  "uptime": "09:47:32 up 2 days, 11:47,  1 user,  load average: 11.52, 12.71, 14.72"
}
```

The two benchmark runtimes are development dependencies with CPython 3.14 wheels.
No scratch interpreter or server runtime dependency is necessary.
The clock uses UTC.
`OMP_NUM_THREADS`, `OPENBLAS_NUM_THREADS`, and `MKL_NUM_THREADS` each have value `1`.

## Results

| Split | Candidate | Recordings | Word errors/reference words | Exact transcripts | First word | First partial median ms (coverage) | Final median ms |
|---|---|---:|---|---|---|---|---:|
| tuning | parakeet | 50 | 30/394 (7.6142%) | 28/50 | 48/50 | 431.40 (50/50) | 78.18 |
| tuning | sherpa-zipformer | 50 | 129/394 (32.7411%) | 4/50 | 15/50 | 1770.84 (50/50) | 26.49 |
| tuning | fastconformer | 50 | 79/394 (20.0508%) | 10/50 | 26/50 | 1772.48 (50/50) | 60.53 |
| held_out | parakeet | 200 | 81/1551 (5.2224%) | 142/200 | 191/200 | 430.88 (200/200) | 77.50 |
| held_out | sherpa-zipformer | 200 | 490/1551 (31.5925%) | 28/200 | 66/200 | 1454.43 (200/200) | 28.18 |
| held_out | fastconformer | 200 | 254/1551 (16.3765%) | 76/200 | 124/200 | 1614.04 (200/200) | 67.34 |

## Held-out paired WER differences

The paired bootstrap uses 5000 recording-level resamples and seed `20261025`.
Each resample uses the same recording indices for the two models.
Its denominator is the sum of reference-word counts.
The interval uses the 2.5th and 97.5th percentiles.
It supplies information only and cannot change selection.

- `sherpa-zipformer` minus Parakeet: 26.370 percentage points, 95% interval [23.363, 29.501].
- `fastconformer` minus Parakeet: 11.154 percentage points, 95% interval [8.665, 13.586].

## First words by onset time

These counts supply information only. They cannot change selection.
The onset is `alignment_words[0].start_sample / 16` in ms.
The onset comes from automatic corpus `alignment_words`, not human onset records.
No left padding or other audio change occurs for this test.

| Split | Candidate | Leading silence | First word/recordings |
|---|---|---|---|
| tuning | parakeet | <150 ms | 0/0 |
| tuning | parakeet | 150 to 300 ms | 0/0 |
| tuning | parakeet | >300 ms | 48/50 |
| tuning | sherpa-zipformer | <150 ms | 0/0 |
| tuning | sherpa-zipformer | 150 to 300 ms | 0/0 |
| tuning | sherpa-zipformer | >300 ms | 15/50 |
| tuning | fastconformer | <150 ms | 0/0 |
| tuning | fastconformer | 150 to 300 ms | 0/0 |
| tuning | fastconformer | >300 ms | 26/50 |
| held_out | parakeet | <150 ms | 2/2 |
| held_out | parakeet | 150 to 300 ms | 4/4 |
| held_out | parakeet | >300 ms | 185/194 |
| held_out | sherpa-zipformer | <150 ms | 0/2 |
| held_out | sherpa-zipformer | 150 to 300 ms | 0/4 |
| held_out | sherpa-zipformer | >300 ms | 66/194 |
| held_out | fastconformer | <150 ms | 1/2 |
| held_out | fastconformer | 150 to 300 ms | 3/4 |
| held_out | fastconformer | >300 ms | 120/194 |

## Frozen selection

```json
{
  "candidate": "parakeet",
  "label_sha256": "9612197a4e95598935844d8236d1732da0001388db78e2d14d40bea0c2283f6c",
  "providers": {
    "fastconformer": {
      "provider_config_sha256": "826ba22a6b7cf1850c1d75f25f651c7beda4f60cc4417d0838bd45a0f926a900",
      "stt_model_id": "csukuangfj/sherpa-onnx-nemo-streaming-fast-conformer-transducer-en-80ms-int8",
      "stt_revision": "3fafd319033af1c552e7bc8394f7258afdce91b0"
    },
    "parakeet": {
      "provider_config_sha256": "9d95c5a17431fc82734f749b6aadec051aa93ade416e5873b0d1e36eaf50f2d9",
      "stt_model_id": "nvidia/parakeet-tdt-0.6b-v2",
      "stt_revision": "0bbb45a3365852604aef28b538a8f066f4ccaa85"
    },
    "sherpa-zipformer": {
      "provider_config_sha256": "7671d93a0c63cb3d1733ca5976b095c164894ed3e0298f200d18320f5ed6b212",
      "stt_model_id": "csukuangfj/sherpa-onnx-streaming-zipformer-en-2023-06-26",
      "stt_revision": "672fbf1b30579d6585301139bb363f42a0ad4a24"
    }
  },
  "selection_rule": {
    "final_latency": "no worse median",
    "first_partial": "lower median",
    "first_word": "no worse",
    "wer_margin_pp": 1.0
  }
}
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
| `provider_config_sha256` | The SHA-256 of sorted JSON configuration |
| `partials` | A nonempty array of objects with `audio_ms` and `text` only |

`audio_ms` is finite, nonnegative, monotonic, and at most the source-audio length.
`text` is a string. Empty strings record observations before the first word.
The consumer must align each corpus record with the frozen identity before replay.
All-candidate measurements stay in the external benchmark cache.

## Initial test and corrected tail

The results above are from the second complete test.
The initial test used a 500 ms tail from the runtime class example.
The upstream file-decoding example uses 660 ms.
The initial test selected Parakeet before its held-out test.
An audit then found missing Zipformer word endings at 500 ms.

The second test uses 660 ms and a new external freeze record.
The input audio, model files, selection rule, and 1.0 percentage-point WER margin stay the same.
Only the streaming tail and its configuration digest change.
The second test again selects Parakeet on tuning rows before its held-out test.
No result after that test changes the selection.

Initial word-error counts:

| Split | Parakeet | Zipformer | FastConformer |
|---|---|---|---|
| tuning | 30/394 | 136/394 | 79/394 |
| held_out | 81/1551 | 507/1551 | 256/1551 |

The 660 ms audit changes 7/50 Zipformer tuning transcripts against the 500 ms test.
A 1000 ms diagnostic changes 8/50.
One more recording changes `MIN` to `MINUTES` at 1000 ms, but not at 660 ms.
The configuration keeps the upstream-example 660 ms value.
This remaining tail sensitivity does not change the configuration after the held-out test.

FastConformer has no transcript changes in the 150 tuning audit tests.
Whole-buffer input and 80 ms input agree for each model at the same 500 ms tail.
The audit does not supply a new selection rule.

## Harness checks

1. The upstream WAVs below use each model's supplied transcript file.
   Their hashes agree across the two model repositories.
   The same benchmark decoder gives these counts:

| Model | Sample | Word errors/words | First word |
|---|---|---|---|
| Zipformer | `0.wav` | 0/18 | 1/1 |
| Zipformer | `1.wav` | 1/48 | 1/1 |
| FastConformer | `0.wav` | 0/18 | 1/1 |
| FastConformer | `1.wav` | 1/48 | 1/1 |

The single error is `DISHONOURED` versus `DISHONORED` for each model.
No model-specific spelling correction occurs.
The three candidates also keep the first word of the Parakeet tutorial sample, `2086-149220-0033.wav`.
These audio files stay external to the repository.
LibriSpeech supplies them with the CC-BY-4.0 license. Credit: the LibriSpeech and OpenSLR projects.

2. Five tuning transcripts below show the raw output for each candidate.
   The same normalization function processes all candidates and all corpus transcripts.
   It uses lowercase words, removes punctuation, and expands integer digits.
   It does not remove a spoken `and` or correct a model-specific word.

| Corpus transcript | Parakeet | Zipformer | FastConformer |
|---|---|---|---|
| `set a timer for 1 minute and 23 seconds` | `Set the timer for one minute and twenty-three seconds.` | `SET THE TIMEER FOR ONE MINUTE AND TWENTY THREE SECONDS` | `set the timer for one minute and twenty three seconds` |
| `set the timer for 949 seconds` | `Set the timer for 949 seconds.` | `SAID THE TIMER FOR NINE HUNDRED FORTY NINE SECONDS` | `set the timer for nine hundred forty nine seconds` |
| `set timer for 3 minutes` | `Set timer for three minutes.` | `SAID DIAMOND FOR THREE MINUTES` | `said timer for three minutes` |
| `set the timer for 976 seconds` | `Set the timer for nine hundred and seventy six seconds.` | `SAID THE TIMER FOR NINE HUNDRED AND SEVENTY SIX SECONDS` | `set the timer for nine hundred and seventy six seconds` |
| `start timer for 1 minute 66 seconds` | `Start timer for 1 minute 66 seconds.` | `STARTIMER FOR ONE MINUTE SIXTY SIX SECONDS` | `sturt timer for one minute sixty six seconds` |

The table uses the corrected-tail outputs. The initial test ends the third Zipformer transcript with `MIN`.
For example, `949` and `nine hundred forty nine` become the same four words.
The error `SAID` versus `set` stays a word error for each candidate.

3. The input checker accepts mono 16 kHz int16 PCM WAV only.
   Samples use float32 values divided by 32768.
   There is no added left padding, as in the upstream file example.
   The tail is 10560 samples at 16 kHz, or 660 ms.
   Each stream receives `input_finished()` and decodes while `is_ready()` gives `True`.
   Endpoint detection stays off. No `reset` or endpoint cuts the source audio.

4. The Zipformer uses the larger 2023-06-26 model, not the rejected 20M model.
   Its encoder and joiner use int8. Its decoder uses fp32.
   All three files use `epoch-99-avg-1-chunk-16-left-128`.
   The encoder metadata states `model_type: zipformer2` and `decode_chunk_len: 32`.
   FastConformer uses the int8 encoder, decoder, and joiner from the same 80 ms repository.
   Its metadata states `EncDecHybridRNNTCTCBPEModel` and the NVIDIA 80 ms model URL.

Each file has an immutable URL and checked SHA-256 in `assets/manifest.json`.
The difference applies to these configurations and this timer corpus.
It is not a claim about all read English or other model variants.
First-word errors also occur with more than 300 ms of source lead-in.
The small earlier-onset groups do not show an onset effect.

## License scope

Parakeet uses CC-BY-4.0. The Zipformer README states Apache-2.0.
The NVIDIA FastConformer version `1.20.0` states NVIDIA NGC Terms of Use.
Its ONNX repository has no README or other license statement.

The manifest uses `NOASSERTION` for those bytes and records the upstream terms in full scope.
This is not a claim that the weights have no license.
The two streaming candidates have status `not_selected` for their technical results only.

## Source records

Access date: 2026-10-25. File pins and license quotations are in `assets/manifest.json`.

- Parakeet: https://huggingface.co/istupakov/parakeet-tdt-0.6b-v2-onnx
- Zipformer: https://huggingface.co/csukuangfj/sherpa-onnx-streaming-zipformer-en-2023-06-26
- FastConformer: https://huggingface.co/csukuangfj/sherpa-onnx-nemo-streaming-fast-conformer-transducer-en-80ms-int8
- Runtime examples: https://github.com/k2-fsa/sherpa-onnx/blob/master/python-api-examples/online-decode-files.py
- Runtime API: https://istupakov.github.io/onnx-asr/usage/
- Pinned file-decoding example: https://github.com/k2-fsa/sherpa-onnx/blob/532f1b5a8a207bf53b116b747207c1506696c843/python-api-examples/online-decode-files.py
- Zipformer sample transcripts: https://huggingface.co/csukuangfj/sherpa-onnx-streaming-zipformer-en-2023-06-26/blob/672fbf1b30579d6585301139bb363f42a0ad4a24/test_wavs/trans.txt
- FastConformer sample transcripts: https://huggingface.co/csukuangfj/sherpa-onnx-nemo-streaming-fast-conformer-transducer-en-80ms-int8/blob/3fafd319033af1c552e7bc8394f7258afdce91b0/test_wavs/trans.txt
- NVIDIA terms statement: https://api.ngc.nvidia.com/v2/models/nvidia/nemo/stt_en_fastconformer_hybrid_large_streaming_80ms/versions/1.20.0
- Sample audio license: https://www.openslr.org/12/ (statement: `License: CC BY 4.0`).
- Parakeet tutorial sample: https://dldata-public.s3.us-east-2.amazonaws.com/2086-149220-0033.wav

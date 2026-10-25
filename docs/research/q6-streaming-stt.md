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

The full-buffer decoder usually gives filler text before voice onset.
For 247/250 recordings, its first nonempty transcript does not start
with the corpus first word. Examples include `Okay.` and `Mm.`.
This is not the time to the first correct command word.

The second latency column uses the first transcript with the corpus first word.
It uses normalized word tokens and source-audio time, without decode CPU cost.
Its denominator counts recordings with that word.
This information and the filler count do not change the frozen selection rule.

- `parakeet`: `repeated full-buffer decode at 400 ms intervals (not streaming)`. Revision: `0bbb45a3365852604aef28b538a8f066f4ccaa85`.
- `sherpa-zipformer`: `cache-aware streaming transducer, chunk16 left128`. Revision: `672fbf1b30579d6585301139bb363f42a0ad4a24`.
- `fastconformer`: `cache-aware NVIDIA FastConformer streaming transducer, 80 ms look-ahead`. Revision: `3fafd319033af1c552e7bc8394f7258afdce91b0`.

## Environment

```json
{
  "cpu": "AMD RYZEN AI MAX+ 395 w/ Radeon 8060S",
  "effective_provider_configurations": {
    "fastconformer": {
      "core": "sherpa-onnx-core==2.0.0",
      "decoding_method": "greedy_search",
      "endpoint_detection": false,
      "feature_dim": 80,
      "model_files": [
        {
          "file": "decoder.int8.onnx",
          "sha256": "76eec598da07c204747a859a723b99077ef0bbdc19ef4b3f51eb43275662475d",
          "url": "https://huggingface.co/csukuangfj/sherpa-onnx-nemo-streaming-fast-conformer-transducer-en-80ms-int8/resolve/3fafd319033af1c552e7bc8394f7258afdce91b0/decoder.int8.onnx"
        },
        {
          "file": "encoder.int8.onnx",
          "sha256": "8b982c67b45e3b735d3fee49a4fea525b3149b450ba437f4c8a933f4aa6744c0",
          "url": "https://huggingface.co/csukuangfj/sherpa-onnx-nemo-streaming-fast-conformer-transducer-en-80ms-int8/resolve/3fafd319033af1c552e7bc8394f7258afdce91b0/encoder.int8.onnx"
        },
        {
          "file": "joiner.int8.onnx",
          "sha256": "67f4291dc170fd06b3695a8511f5199fd5965ee3c77cfbd59afc10e145f173f2",
          "url": "https://huggingface.co/csukuangfj/sherpa-onnx-nemo-streaming-fast-conformer-transducer-en-80ms-int8/resolve/3fafd319033af1c552e7bc8394f7258afdce91b0/joiner.int8.onnx"
        },
        {
          "file": "tokens.txt",
          "sha256": "618dc110fc2213886b52e063ff42329bbdf37a266ca7705184090fa5f39f3131",
          "url": "https://huggingface.co/csukuangfj/sherpa-onnx-nemo-streaming-fast-conformer-transducer-en-80ms-int8/resolve/3fafd319033af1c552e7bc8394f7258afdce91b0/tokens.txt"
        }
      ],
      "pcm_scale": 32768,
      "provider": "cpu",
      "runtime": "sherpa-onnx==2.0.0",
      "sample_dtype": "float32",
      "sample_rate": 16000,
      "step_samples": 1280,
      "tail_samples": 10560,
      "threads": 4
    },
    "parakeet": {
      "inter_op_threads": 1,
      "model": "nemo-parakeet-tdt-0.6b-v2",
      "model_files": [
        {
          "file": "config.json",
          "sha256": "666903c76b9798caf2c210afd4f6cd60b08a8dbf9800ec8d7a3bc0d2148ac466",
          "url": "https://huggingface.co/istupakov/parakeet-tdt-0.6b-v2-onnx/resolve/0bbb45a3365852604aef28b538a8f066f4ccaa85/config.json"
        },
        {
          "file": "decoder_joint-model.int8.onnx",
          "sha256": "a449f49acd68979d418651dd2dcb737cc0f1bf0225e009e29ee326354edbf7d3",
          "url": "https://huggingface.co/istupakov/parakeet-tdt-0.6b-v2-onnx/resolve/0bbb45a3365852604aef28b538a8f066f4ccaa85/decoder_joint-model.int8.onnx"
        },
        {
          "file": "encoder-model.int8.onnx",
          "sha256": "3e0581fda6ab843888b51e56d7ee78b6d5bc3237ec113af1f732d1d5286aa155",
          "url": "https://huggingface.co/istupakov/parakeet-tdt-0.6b-v2-onnx/resolve/0bbb45a3365852604aef28b538a8f066f4ccaa85/encoder-model.int8.onnx"
        },
        {
          "file": "vocab.txt",
          "sha256": "ec182b70dd42113aff6c5372c75cac58c952443eb22322f57bbd7f53977d497d",
          "url": "https://huggingface.co/istupakov/parakeet-tdt-0.6b-v2-onnx/resolve/0bbb45a3365852604aef28b538a8f066f4ccaa85/vocab.txt"
        }
      ],
      "onnxruntime": "1.31.0",
      "pcm_scale": 32768,
      "preprocessor": {
        "max_concurrent_workers": 1,
        "use_numpy_preprocessors": true
      },
      "provider": "CPUExecutionProvider",
      "quantization": "int8",
      "runtime": "onnx-asr==0.12.0",
      "sample_dtype": "float32",
      "sample_rate": 16000,
      "step_samples": 6400,
      "tail_samples": 0,
      "threads": 4
    },
    "sherpa-zipformer": {
      "core": "sherpa-onnx-core==2.0.0",
      "decoding_method": "greedy_search",
      "endpoint_detection": false,
      "feature_dim": 80,
      "model_files": [
        {
          "file": "decoder-epoch-99-avg-1-chunk-16-left-128.onnx",
          "sha256": "7bf787f90b194b307e5a4ad6a34fadb4e748304c35f78a8d66358a05b13ee6ef",
          "url": "https://huggingface.co/csukuangfj/sherpa-onnx-streaming-zipformer-en-2023-06-26/resolve/672fbf1b30579d6585301139bb363f42a0ad4a24/decoder-epoch-99-avg-1-chunk-16-left-128.onnx"
        },
        {
          "file": "encoder-epoch-99-avg-1-chunk-16-left-128.int8.onnx",
          "sha256": "563fde436d16cf7607cf408cd6b30909819d03162652ef389c2450ced3f45ac1",
          "url": "https://huggingface.co/csukuangfj/sherpa-onnx-streaming-zipformer-en-2023-06-26/resolve/672fbf1b30579d6585301139bb363f42a0ad4a24/encoder-epoch-99-avg-1-chunk-16-left-128.int8.onnx"
        },
        {
          "file": "joiner-epoch-99-avg-1-chunk-16-left-128.int8.onnx",
          "sha256": "d944208d660d67c8d72cd2acaeac971fa5ceb8c80e76c1968148846fedd6e297",
          "url": "https://huggingface.co/csukuangfj/sherpa-onnx-streaming-zipformer-en-2023-06-26/resolve/672fbf1b30579d6585301139bb363f42a0ad4a24/joiner-epoch-99-avg-1-chunk-16-left-128.int8.onnx"
        },
        {
          "file": "tokens.txt",
          "sha256": "49e3c2646595fd907228b3c6787069658f67b17377c60aeb8619c4551b2316fb",
          "url": "https://huggingface.co/csukuangfj/sherpa-onnx-streaming-zipformer-en-2023-06-26/resolve/672fbf1b30579d6585301139bb363f42a0ad4a24/tokens.txt"
        }
      ],
      "pcm_scale": 32768,
      "provider": "cpu",
      "runtime": "sherpa-onnx==2.0.0",
      "sample_dtype": "float32",
      "sample_rate": 16000,
      "step_samples": 1280,
      "tail_samples": 10560,
      "threads": 4
    }
  },
  "load_average_start": [
    11.23193359375,
    12.4951171875,
    13.14794921875
  ],
  "logical_threads": 32,
  "python": "3.14.3",
  "runtimes": {
    "onnx-asr": "0.12.0",
    "onnxruntime": "1.31.0",
    "sherpa-onnx": "2.0.0"
  },
  "threads": 4,
  "uptime": "10:39:34 up 2 days, 12:39,  1 user,  load average: 11.23, 12.50, 13.15"
}
```

The two benchmark runtimes are development dependencies with CPython 3.14 wheels.
No scratch interpreter or server runtime dependency is necessary.
The clock uses UTC.
`OMP_NUM_THREADS`, `OPENBLAS_NUM_THREADS`, and `MKL_NUM_THREADS` each have value `1`.

## Results

| Split | Candidate | Recordings | Word errors/reference words | Exact transcripts | First word | First partial median ms (coverage) | First reference-word audio ms (coverage) | Final median ms |
|---|---|---:|---|---|---|---|---|---:|
| tuning | parakeet | 50 | 30/394 (7.6142%) | 28/50 | 48/50 | 430.98 (50/50) | 1600.00 (49/50) | 81.86 |
| tuning | sherpa-zipformer | 50 | 129/394 (32.7411%) | 4/50 | 15/50 | 1771.76 (50/50) | 1760.00 (16/50) | 25.88 |
| tuning | fastconformer | 50 | 79/394 (20.0508%) | 10/50 | 26/50 | 1771.98 (50/50) | 1760.00 (26/50) | 60.34 |
| held_out | parakeet | 200 | 81/1551 (5.2224%) | 142/200 | 191/200 | 431.43 (200/200) | 1200.00 (196/200) | 82.99 |
| held_out | sherpa-zipformer | 200 | 490/1551 (31.5925%) | 28/200 | 66/200 | 1453.56 (200/200) | 1760.00 (73/200) | 27.10 |
| held_out | fastconformer | 200 | 254/1551 (16.3765%) | 76/200 | 124/200 | 1614.37 (200/200) | 1600.00 (135/200) | 68.27 |

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
      "provider_config_sha256": "df5d5c68999edade600a93972f9584a5af67fb939efb8616205afc2efadaca63",
      "stt_model_id": "csukuangfj/sherpa-onnx-nemo-streaming-fast-conformer-transducer-en-80ms-int8",
      "stt_revision": "3fafd319033af1c552e7bc8394f7258afdce91b0"
    },
    "parakeet": {
      "provider_config_sha256": "b4ede9e1a2e15e6dafd8e40680691d40760ad11904a9496edc2afe653a331eab",
      "stt_model_id": "nvidia/parakeet-tdt-0.6b-v2",
      "stt_revision": "0bbb45a3365852604aef28b538a8f066f4ccaa85"
    },
    "sherpa-zipformer": {
      "provider_config_sha256": "a127f676549c3784f2f131cd7100f70607022711b722dca35fb19f5c65c47f55",
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
| tuning | 30/394 | 136/394 | 79/394 |
| held_out | 81/1551 | 507/1551 | 256/1551 |

The 660 ms audit changes 7/50
Zipformer tuning transcripts against the 500 ms test.
A 1000 ms diagnostic changes
8/50.
One more recording changes `MIN` to `MINUTES` at 1000 ms, but not at 660 ms.
The configuration keeps the approved upstream-example 660 ms value.
This remaining tail sensitivity does not change the configuration
after the held-out test.

FastConformer has 0 transcript changes
in 150 audit tests.
Whole-buffer input and 80 ms input have 0 transcript
differences at the same 500 ms tail.
These are diagnostic tests, not a new selection rule.

The source-cache hashes below identify the stored historical summaries and audit data.
The tool reads these records from `assets/manifest.json`.
It does not read a prior Markdown report to collect disclosures.

```json
{
  "flush-audit.json": "fea3ce1d8e04c7ad213044eb4dd8e17a5651c6b9422669664aaf659ed722f9ea",
  "models/measurements-both.json": "7da4a301db5d0bf3df030e59b45527972dfcbd0acb41caf1e79acd0ea857a368",
  "reference-results.json": "4d03828d4af394af72284618254490f93f2c28014c61b917ce30137578fa4c5b",
  "upstream-run2.json": "67752d893c333daf0621b8dd6a9c2cc2309ec832cc1276c509f01ef04f01321d"
}
```

## Harness checks

The two models' supplied WAVs have the same hashes across their repositories.
Each uses its supplied transcript file. The corrected harness gives these counts:

| Model | Sample | Word errors/words |
|---|---|---|
| sherpa-zipformer | `0.wav` | 0/18 |
| sherpa-zipformer | `1.wav` | 1/48 |
| fastconformer | `0.wav` | 0/18 |
| fastconformer | `1.wav` | 1/48 |

The single error is `DISHONOURED` versus `DISHONORED` for each model.
No model-specific spelling correction occurs.
The Parakeet tutorial sample has first-word preservation for each model.
The stored first-word counts are:

```json
{
  "fastconformer": 3,
  "parakeet": 3,
  "sherpa-zipformer": 3
}
```

These files stay external to the repository.
LibriSpeech supplies them with the CC-BY-4.0 license.
Credit: the LibriSpeech and OpenSLR projects.

Five tuning rows show raw model output, without model-specific text normalization:

| Corpus transcript | Parakeet | Zipformer | FastConformer |
|---|---|---|---|
| `set a timer for 1 minute and 23 seconds` | `Set the timer for one minute and twenty-three seconds.` | `SET THE TIMEER FOR ONE MINUTE AND TWENTY THREE SECONDS` | `set the timer for one minute and twenty three seconds` |
| `set the timer for 949 seconds` | `Set the timer for 949 seconds.` | `SAID THE TIMER FOR NINE HUNDRED FORTY NINE SECONDS` | `set the timer for nine hundred forty nine seconds` |
| `set timer for 3 minutes` | `Set timer for three minutes.` | `SAID DIAMOND FOR THREE MINUTES` | `said timer for three minutes` |
| `set the timer for 976 seconds` | `Set the timer for nine hundred and seventy six seconds.` | `SAID THE TIMER FOR NINE HUNDRED AND SEVENTY SIX SECONDS` | `set the timer for nine hundred and seventy six seconds` |
| `start timer for 1 minute 66 seconds` | `Start timer for 1 minute 66 seconds.` | `STARTIMER FOR ONE MINUTE SIXTY SIX SECONDS` | `sturt timer for one minute sixty six seconds` |

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
{
  "fastconformer": {
    "chunk_shift": "16",
    "model_type": "EncDecHybridRNNTCTCBPEModel",
    "onnx.infer": "onnxruntime.quant",
    "subsampling_factor": "8",
    "url": "https://catalog.ngc.nvidia.com/orgs/nvidia/teams/nemo/models/stt_en_fastconformer_hybrid_large_streaming_80ms"
  },
  "sherpa-zipformer": {
    "decode_chunk_len": "32",
    "left_context_len": "128,64,32,16,32,64",
    "model_type": "zipformer2",
    "onnx.infer": "onnxruntime.quant"
  }
}
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

### `stt-parakeet`

License: `CC-BY-4.0`.



- License source: https://huggingface.co/istupakov/parakeet-tdt-0.6b-v2-onnx/blob/0bbb45a3365852604aef28b538a8f066f4ccaa85/README.md
- Source revision or version: `0bbb45a3365852604aef28b538a8f066f4ccaa85`.
- File: `encoder-model.int8.onnx`. SHA-256: `3e0581fda6ab843888b51e56d7ee78b6d5bc3237ec113af1f732d1d5286aa155`.

License statement:

```text
license: cc-by-4.0
```

### `stt-sherpa-zipformer`

License: `Apache-2.0`.



- License source: https://huggingface.co/csukuangfj/sherpa-onnx-streaming-zipformer-en-2023-06-26/blob/672fbf1b30579d6585301139bb363f42a0ad4a24/README.md
- Source revision or version: `672fbf1b30579d6585301139bb363f42a0ad4a24`.
- File: `encoder-epoch-99-avg-1-chunk-16-left-128.int8.onnx`. SHA-256: `563fde436d16cf7607cf408cd6b30909819d03162652ef389c2450ced3f45ac1`.

License statement:

```text
license: apache-2.0
```

### `stt-fastconformer`

License: `NOASSERTION`.

```text
The upstream NVIDIA model uses NGC Terms of Use. The converted ONNX repository supplies no model card. NOASSERTION means no SPDX assertion for the converted bytes, not a claim of no license.
```

- License source: https://api.ngc.nvidia.com/v2/models/nvidia/nemo/stt_en_fastconformer_hybrid_large_streaming_80ms/versions/1.20.0
- Source revision or version: `1.20.0`.
- Scope record:

```text
Upstream NVIDIA FastConformer Hybrid Large Streaming 80ms, version 1.20.0. The ONNX conversion repository has no separate model card or license text.
```

License statement:

```text
License to use this model is covered by the NGC [TERMS OF USE](https://ngc.nvidia.com/legal/terms) unless another License/Terms Of Use/EULA is clearly specified. By downloading the public and release version of the model, you accept the terms and conditions of the NGC [TERMS OF USE](https://ngc.nvidia.com/legal/terms).
```

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
- Sample audio license: https://www.openslr.org/12/ (statement: `License: CC BY 4.0`).
- Parakeet tutorial sample: https://dldata-public.s3.us-east-2.amazonaws.com/2086-149220-0033.wav

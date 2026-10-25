# Local TTS selection

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

Measurement date: `2026-10-25`.
Hardware class: `AMD Ryzen AI Max+ 395, x86-64 Linux, 16 cores and 32 logical threads`.
All engines used the CPU. Other processes shared the host.
The test used no CPU affinity or frequency lock.

Each four-thread configuration used one discarded warm-up.
It then ran 30 synthetic English replies five times.
The table pools 150 requests for each configuration.
No new measurement ran for this record.

| Engine | Voice | Python | First audio p50 / max ms | RTF short / long | RSS MiB | WER % | UTMOSv2 mean |
|---|---|---|---:|---:|---:|---:|---:|
| piper 1.8.0 | en_US-lessac-medium | 3.14.3 | 29.74 / 127.02 | 0.0123 / 0.0124 | 368.6 | 0.60 | 3.722 |
| pocket 4.0.1 | Alba | 3.14.3 | 65.12 / 129.37 | 0.2911 / 0.2791 | 1038.0 | 0.13 | 3.306 |
| kokoro 0.9.4 | af_heart | 3.12.13 | 366.19 / 1410.77 | 0.1218 / 0.1165 | 2249.2 | 0.00 | 3.725 |
| kokoro-onnx 0.6.1 | af_heart | 3.14.3 | 351.70 / 1750.87 | 0.1456 / 0.1309 | 893.7 | 0.33 | 3.454 |
| supertonic 1.3.1 | M1 | 3.14.3 | 312.38 / 1013.64 | 0.1018 / 0.0744 | 557.7 | 0.27 | 3.654 |

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

### `piper-runtime`

License: `GPL-3.0-or-later`.

Source: https://files.pythonhosted.org/packages/84/81/0112a7d510911f33018dc24023d1655bb772f84a4e95fe7f0180f66bbd17/piper_tts-1.8.0-cp39-abi3-manylinux_2_17_x86_64.manylinux2014_x86_64.manylinux_2_28_x86_64.whl

SHA-256: `25b4d3f31ff70c8fa7151908e00aaa5650cbdf16bca8fcf21299f3941b89a7d3`.

Attribution:

```text
Piper 1.8.0, OHF-Voice/piper1-gpl. GPL-3.0-or-later. Keep the GPL text and corresponding source with redistributed binaries.
```

- License source: https://pypi.org/pypi/piper-tts/1.8.0/json
- Source revision or version: `1.8.0`.
- File: `piper_tts-1.8.0-cp39-abi3-manylinux_2_17_x86_64.manylinux2014_x86_64.manylinux_2_28_x86_64.whl`. SHA-256: `25b4d3f31ff70c8fa7151908e00aaa5650cbdf16bca8fcf21299f3941b89a7d3`.

License statement:

```text
GPL-3.0-or-later
```

- License source: https://github.com/OHF-Voice/piper1-gpl/blob/639388b6317fc4731e91d53da42aea68fd4166ff/COPYING
- Source revision or version: `639388b6317fc4731e91d53da42aea68fd4166ff`.
- Scope record:

```text
Piper v1.8.0 source. The package declares GPL-3.0-or-later, not GPL-3.0-only.
```

License statement:

```text
GNU GENERAL PUBLIC LICENSE
                       Version 3, 29 June 2007
```

### `piper-lessac-medium`

License: `NOASSERTION`.

Source: https://huggingface.co/rhasspy/piper-voices/resolve/5041a7a49ae5b3a0f30e6837769eb03fdbb231bd/en/en_US/lessac/medium/en_US-lessac-medium.onnx

SHA-256: `5efe09e69902187827af646e1a6e9d269dee769f9877d17b16b1b46eeaaf019f`.

Attribution:

```text
en_US-lessac-medium. Dataset: Voice Factory International Inc. and Lessac Technologies Inc., Blizzard 2013. Custom research-only dataset terms. No separate model-weight grant or attribution clause is stated in the model card.
```

- License source: https://www.cstr.ed.ac.uk/projects/blizzard/2013/lessac_blizzard2013/license.html
- Source revision or version: `08024d4b9fed6b6163fcc5b4c5f0f962078dd21d4ce716bb4e5c0a2be350ff95`.
- Scope record:

```text
Dataset terms only, not a model-weight license grant. Unversioned HTML captured 2026-10-25 with the recorded SHA-256. Research Purposes excludes commercial voice synthesis development, marketing, sale, and licensing. No dataset audio is supplied or selected as test audio.
```

License statement:

```text
3.2 Licensors hereby grant to the User a non-exclusive and non-transferable licence, without the right to sub-licence, to use the Materials exclusively for Research Purposes only throughout the world. No other rights or licences are granted by Licencors, other than as set out in this Clause 3.
```

- License source: https://huggingface.co/rhasspy/piper-voices/blob/5041a7a49ae5b3a0f30e6837769eb03fdbb231bd/en/en_US/lessac/medium/MODEL_CARD
- Source revision or version: `5041a7a49ae5b3a0f30e6837769eb03fdbb231bd`.
- File: `en/en_US/lessac/medium/en_US-lessac-medium.onnx`. SHA-256: `5efe09e69902187827af646e1a6e9d269dee769f9877d17b16b1b46eeaaf019f`.

License statement:

```text
## Dataset

* URL: https://www.cstr.ed.ac.uk/projects/blizzard/2013/lessac_blizzard2013/
* License: https://www.cstr.ed.ac.uk/projects/blizzard/2013/lessac_blizzard2013/license.html

## Training

Trained from scratch.
```

### `piper-lessac-config`

License: `NOASSERTION`.

Source: https://huggingface.co/rhasspy/piper-voices/resolve/5041a7a49ae5b3a0f30e6837769eb03fdbb231bd/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json

SHA-256: `efe19c417bed055f2d69908248c6ba650fa135bc868b0e6abb3da181dab690a0`.

Attribution:

```text
en_US-lessac-medium configuration and phoneme-ID map. The model card links dataset terms but states no separate configuration license.
```

- License source: https://huggingface.co/rhasspy/piper-voices/blob/5041a7a49ae5b3a0f30e6837769eb03fdbb231bd/en/en_US/lessac/medium/MODEL_CARD
- Source revision or version: `5041a7a49ae5b3a0f30e6837769eb03fdbb231bd`.
- File: `en/en_US/lessac/medium/en_US-lessac-medium.onnx.json`. SHA-256: `efe19c417bed055f2d69908248c6ba650fa135bc868b0e6abb3da181dab690a0`.

License statement:

```text
* License: https://www.cstr.ed.ac.uk/projects/blizzard/2013/lessac_blizzard2013/license.html
```

### `piper-espeak-bridge`

License: `GPL-3.0-or-later`.

Source: https://files.pythonhosted.org/packages/84/81/0112a7d510911f33018dc24023d1655bb772f84a4e95fe7f0180f66bbd17/piper_tts-1.8.0-cp39-abi3-manylinux_2_17_x86_64.manylinux2014_x86_64.manylinux_2_28_x86_64.whl

SHA-256: `25b4d3f31ff70c8fa7151908e00aaa5650cbdf16bca8fcf21299f3941b89a7d3`.

Attribution:

```text
Piper eSpeak NG bridge and ucd-tools. GPL-3.0-or-later. Preserve the upstream notices, license, and corresponding source.
```

- License source: https://pypi.org/pypi/piper-tts/1.8.0/json
- Source revision or version: `1.8.0`.
- File: `piper_tts-1.8.0-cp39-abi3-manylinux_2_17_x86_64.manylinux2014_x86_64.manylinux_2_28_x86_64.whl`. SHA-256: `25b4d3f31ff70c8fa7151908e00aaa5650cbdf16bca8fcf21299f3941b89a7d3`.

License statement:

```text
GPL-3.0-or-later
```

- License source: https://github.com/espeak-ng/espeak-ng/blob/724808c5a83f9ef95fdd0db886ba7ba537ff224a/README.md
- Source revision or version: `724808c5a83f9ef95fdd0db886ba7ba537ff224a`.
- Scope record:

```text
Statically linked eSpeak NG. Piper CMakeLists.txt pins short commit 724808c, resolved to this commit.
```

License statement:

```text
eSpeak NG Text-to-Speech is released under the [GPL version 3](COPYING) or
later license.
```

- License source: https://github.com/espeak-ng/espeak-ng/blob/724808c5a83f9ef95fdd0db886ba7ba537ff224a/src/ucd-tools/COPYING
- Source revision or version: `724808c5a83f9ef95fdd0db886ba7ba537ff224a`.
- Scope record:

```text
Statically linked ucd-tools license text.
```

License statement:

```text
GNU GENERAL PUBLIC LICENSE
```

### `piper-espeak-data`

License: `GPL-3.0-or-later AND Unicode-DFS-2016`.

Source: https://files.pythonhosted.org/packages/84/81/0112a7d510911f33018dc24023d1655bb772f84a4e95fe7f0180f66bbd17/piper_tts-1.8.0-cp39-abi3-manylinux_2_17_x86_64.manylinux2014_x86_64.manylinux_2_28_x86_64.whl

SHA-256: `25b4d3f31ff70c8fa7151908e00aaa5650cbdf16bca8fcf21299f3941b89a7d3`.

Attribution:

```text
eSpeak NG data and Unicode data. GPL-3.0-or-later AND Unicode-DFS-2016. Keep the copyright and permission notices with the data.
```

- License source: https://pypi.org/pypi/piper-tts/1.8.0/json
- Source revision or version: `1.8.0`.
- File: `piper_tts-1.8.0-cp39-abi3-manylinux_2_17_x86_64.manylinux2014_x86_64.manylinux_2_28_x86_64.whl`. SHA-256: `25b4d3f31ff70c8fa7151908e00aaa5650cbdf16bca8fcf21299f3941b89a7d3`.

License statement:

```text
GPL-3.0-or-later
```

- License source: https://github.com/espeak-ng/espeak-ng/blob/724808c5a83f9ef95fdd0db886ba7ba537ff224a/COPYING.UCD
- Source revision or version: `724808c5a83f9ef95fdd0db886ba7ba537ff224a`.
- Scope record:

```text
Unicode data notices in the bundled eSpeak NG source and data.
```

License statement:

```text
Copyright © 1991-2018 Unicode, Inc. All rights reserved.
Distributed under the Terms of Use in http://www.unicode.org/copyright.html.

Permission is hereby granted, free of charge, to any person obtaining
a copy of the Unicode data files and any associated documentation
```

### `piper-onnxruntime`

License: `MIT`.

Source: https://files.pythonhosted.org/packages/30/2e/5c6ec7e26a097e97ee70f2dee68b8ca4d9d26701f2f33c3f8ab585cb89fe/onnxruntime-1.31.0-cp314-cp314-manylinux_2_28_x86_64.whl

SHA-256: `e4efa4a1a0bb0b5173c6a3292c181d518b8323f9d56e978635d0c09d38c94d1a`.

Attribution:

```text
ONNX Runtime 1.31.0. Copyright (c) Microsoft Corporation. MIT. Preserve the package license and third-party notices.
```

- License source: https://pypi.org/pypi/onnxruntime/1.31.0/json
- Source revision or version: `1.31.0`.
- File: `onnxruntime-1.31.0-cp314-cp314-manylinux_2_28_x86_64.whl`. SHA-256: `e4efa4a1a0bb0b5173c6a3292c181d518b8323f9d56e978635d0c09d38c94d1a`.

License statement:

```text
MIT License
```

### `piper-numpy`

License: `BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0`.

Source: https://files.pythonhosted.org/packages/16/d5/928078d2b28f26829b138b4a6c3980045022fb409f570657a224ae60ef4e/numpy-2.5.4-cp314-cp314-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl

SHA-256: `d549420b8858885cea8838a727842249218b9c1da24dd517e25c9c7a948310a3`.

Attribution:

```text
NumPy 2.5.4. Copyright (c) 2005-2025, NumPy Developers. BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0. Preserve the full package and bundled-library notices.
```

- License source: https://pypi.org/pypi/numpy/2.5.4/json
- Source revision or version: `2.5.4`.
- File: `numpy-2.5.4-cp314-cp314-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl`. SHA-256: `d549420b8858885cea8838a727842249218b9c1da24dd517e25c9c7a948310a3`.

License statement:

```text
BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0
```

### `piper-pathvalidate`

License: `MIT`.

Source: https://files.pythonhosted.org/packages/9a/70/875f4a23bfc4731703a5835487d0d2fb999031bd415e7d17c0ae615c18b7/pathvalidate-3.3.1-py3-none-any.whl

SHA-256: `5263baab691f8e1af96092fa5137ee17df5bdfbd6cff1fcac4d6ef4bc2e1735f`.

Attribution:

```text
pathvalidate 3.3.1. MIT. Preserve its copyright and permission notice from the pinned package.
```

- License source: https://pypi.org/pypi/pathvalidate/3.3.1/json
- Source revision or version: `3.3.1`.
- File: `pathvalidate-3.3.1-py3-none-any.whl`. SHA-256: `5263baab691f8e1af96092fa5137ee17df5bdfbd6cff1fcac4d6ef4bc2e1735f`.

License statement:

```text
MIT License
```

## Reproduction

Set `ASSIST_LIVE_PROBE_DIRECTORY` to scratch storage external to the repository.
The default mode reads records only. It imports no synthesis engine and uses no network.
The optional offline CSV inputs can contain measurements from all five engines.

```sh
uv run python tools/probe_tts.py --record-decision \
  --report docs/research/q7t-local-tts.md
uv run python tools/probe_tts.py --timings "$PROBE/timings.csv" \
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

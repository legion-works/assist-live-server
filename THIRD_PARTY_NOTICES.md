# Third-party notices

## Bootstrap

This repository does not include a third-party model, voice, or copied runtime source at this time.
This repository is the home for server model and voice components.
The project uses the full Apache-2.0 license in `LICENSE`.

The development environment uses pytest and its dependencies from PyPI.
Package builds use hatchling and its build dependencies from PyPI.
See `uv.lock` for package versions.

## Release gate

Record the license and necessary attribution of each library, model, and voice before release use.
A missing license or a non-commercial license stops a release.
No model or voice license approval follows from this bootstrap.

## Smart Turn model

The model `smart-turn-v3.2-cpu.onnx` has a BSD-2-Clause rights record.
The repository does not contain the model. There is no server image.
`assets/manifest.json` records a planned startup download with an immutable revision and SHA-256.
There is no startup downloader at this time.
See `docs/research/q5-smart-turn.md` for the file-level source records and license scope.

The upstream project supplies this license text:
https://github.com/pipecat-ai/smart-turn/blob/4786657e242dfe77dd138699ac564ee074a2a543/LICENSE

```text
BSD 2-Clause License

Copyright (c) 2024–2025, Daily

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
```

## Cost probe dependencies

The development environment uses NumPy 2.5.4, ONNX Runtime 1.31.0, and Transformers 5.20.2.
The client package has none of these dependencies. They are not server-image components at this time.
The package metadata declares these licenses:

- NumPy: `BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0`.
- ONNX Runtime: MIT.
- Transformers: Apache-2.0.

The pinned versions and wheel hashes are in `uv.lock`.
The dependency sources and Python floors are in `docs/verification.md`.
The `feature extractor` uses constructor defaults. It downloads no configuration file or tokenizer.

## Kokoro runtime and candidate voice

Kokoro local synthesis has status `confirmed_blocked_absent`.
The project is experimental and has no release. There is no server image.
The repository does not supply Kokoro weights, voices, dictionaries, or runtime packages.
The default voice allowlist is empty. No Kokoro component is in the startup download plan.

The candidate for inspection is `kokoro==0.9.4`, with `misaki==0.9.4`, for American English.
The model weights have an Apache-2.0 statement and a pinned SHA-256.
That statement does not supply approval for the complete runtime chain.
The candidate `af_heart` voice and `config.json` have no file-scoped license approval.
The voice and configuration are not release selections.

The installed code packages have Apache-2.0 text.
Their `Requires-Python` fields do not include Python `3.13` and above.
The server Python floor is `>=3.14.2`. This is a runtime stop cause.

The actual English path uses these parts:

- `misaki` supplies `us_gold.json` and `us_silver.json` with its attached Apache-2.0 package license.
- `phonemizer-fork==3.3.2` uses GPL-3.0-or-later.
- `espeakng-loader==0.2.4` has MIT loader source, but bundles eSpeak NG `1.52.0` and its data.
- The bundled eSpeak library and data use GPL-3.0-or-later, with Unicode data notices.
- `num2words==0.5.14` uses LGPL-2.1-or-later.
- `en_core_web_sm==3.8.0` has MIT text and a source-data notice file.

The loader wheel contains no license file. Its MIT source license does not replace the GPL and data notices.
The Apache image policy stops inclusion of GPL and LGPL runtime components.
This is a project supply rule, not a statement about all possible GPL combinations.

`assets/manifest.json` records the pinned sources, versions, digests, source statements, and blocked paths.
`docs/research/q7-kokoro.md` records the actual first-use downloads and other package declarations.
The inspection copied no third-party code or data into this repository.

# Third-party notices

## Parakeet local compatible STT

The local compatible default is `nvidia/parakeet-tdt-0.6b-v2`.
It uses the ONNX conversion at revision `0bbb45a3365852604aef28b538a8f066f4ccaa85`.
The weights use CC-BY-4.0, not the project Apache-2.0 license.

```text
NVIDIA Parakeet TDT 0.6B v2. CC-BY-4.0. ONNX export and int8 quantization by onnx-asr contributors. Source and license links are in the asset manifest.
```

The changes from the source weights are ONNX export and int8 quantization.
The project does not change those bytes.
The repository has no model bytes. The download plan records each selected file.

- Initial model: https://huggingface.co/nvidia/parakeet-tdt-0.6b-v2
- ONNX model: https://huggingface.co/istupakov/parakeet-tdt-0.6b-v2-onnx/tree/0bbb45a3365852604aef28b538a8f066f4ccaa85
- License statement: https://huggingface.co/istupakov/parakeet-tdt-0.6b-v2-onnx/blob/0bbb45a3365852604aef28b538a8f066f4ccaa85/README.md
- License text: https://creativecommons.org/licenses/by/4.0/legalcode.en

The pinned README states:

```text
license: cc-by-4.0
```

Keep this attribution, the source URLs, and the license URL when you supply these files.
No streaming model has a selection for release use.

## onnx-asr runtime package

The server runtime uses the `onnx-asr==0.12.0` package from PyPI to run the Parakeet model.
The package uses the MIT license, not the project Apache-2.0 license.

```text
MIT License

Copyright (c) 2025 Ilya Stupakov
```

- Source: https://github.com/istupakov/onnx-asr
- Package metadata: `License-Expression: MIT`, with the license text in the `LICENSE` file of the wheel.

Keep the copyright line and the MIT license text when you supply this package.
The `sherpa-onnx==2.0.0` package is a benchmark development dependency only.
It is not a server runtime or client dependency.

## Bootstrap

This repository does not include a third-party model, voice, or copied runtime source at this time.
This repository is the home for server model and voice components.
The project uses the full Apache-2.0 license in `LICENSE`.

The development environment uses pytest and its dependencies from PyPI.
Package builds use hatchling and its build dependencies from PyPI.
See `uv.lock` for package versions.

## Release gate

Record the license and necessary attribution of each library, model, and voice before release use.
Missing source, hash, quotation, or attribution records stop a release.
License terms do not control technical selection. Third-party licenses do not change to Apache-2.0.

## Smart Turn model

The model `smart-turn-v3.2-cpu.onnx` has a BSD-2-Clause rights record.

```text
Smart Turn v3.2. Copyright (c) 2024–2025, Daily. BSD-2-Clause. Full notice, conditions, and disclaimer in THIRD_PARTY_NOTICES.md.
```

The repository does not contain the model. The service image downloads it at startup.
`assets/manifest.json` records a planned startup download with an immutable revision and SHA-256.
The startup command checks each downloaded file against its recorded SHA-256.
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

The server runtime uses NumPy 2.5.4 and ONNX Runtime 1.31.0.
The development environment also uses Transformers 5.20.2.
The client package has none of these dependencies. The service image includes the runtime libraries.
The package metadata declares these licenses:

- NumPy: `BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0`.
- ONNX Runtime: MIT.
- Transformers: Apache-2.0.

The pinned versions and wheel hashes are in `uv.lock`.
The dependency sources and Python floors are in `docs/verification.md`.
The `feature extractor` uses constructor defaults. It downloads no configuration file or tokenizer.

## Kokoro runtime and candidate voice

Kokoro local synthesis has status `not_selected`, with `reason: technical`.
The project is experimental and has no release. The service image does not include Kokoro.
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

GPL and LGPL terms are source records, not technical selection stop causes.
The measured first-audio median is 366.19 ms, versus 29.74 ms for Piper.

`assets/manifest.json` records pinned sources, versions, digests, source statements, and not-selected paths.
`docs/research/q7-kokoro.md` records the actual first-use downloads and other package declarations.
The inspection copied no third-party code or data into this repository.

## Silero model rights

Silero VAD has status `included` and license MIT.
The selected candidate is `v6.2.3`, commit `5cd7945676eb32225748052e2e6a0580e4686a08`.
The file is `src/silero_vad/data/silero_vad.onnx`.
Its SHA-256 is `1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3`.
The PyPI `silero-vad==6.2.3` wheel supplies the same model bytes.

The repository and package have MIT text and an MIT classifier.
The README body also states MIT. The badge alt text states `CC BY-NC 4.0`.
The wheel metadata has this conflict. The badge image says MIT.

The repository owner answered the file-scoped question with `MIT` on 2026-10-25.
Source: https://github.com/snakers4/silero-vad/issues/816#issuecomment-6393620007
The comment resolves the remaining badge alt text for the pinned file.

```text
Silero VAD v6.2.3. Copyright (c) 2020-present Silero Team. MIT. Keep the copyright and permission notice.
```

The startup download plan includes the model. No model bytes are in the repository.
The release checker examines each image layer and the startup plan.

`assets/manifest.json` has the pinned file, package, and source statements.
`docs/research/q8-silero.md` records the owner answer and initial conflicting statements.

The Silero record uses `license_sources` for all rights statements.
Each entry has `kind`, `url`, `quote`, and `revision_or_version`.
Each entry also has `file_binding` with the selected `path` and `sha256`.
The checker reads license identifiers from `quote`, not a different source list.

The model uses this upstream license:
https://github.com/snakers4/silero-vad/blob/5cd7945676eb32225748052e2e6a0580e4686a08/LICENSE

```text
MIT License

Copyright (c) 2020-present Silero Team

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## Selected local TTS components

The server runtime uses Piper 1.8.0 and pathvalidate 3.3.1.
NumPy 2.5.4 and ONNX Runtime 1.31.0 are also runtime dependencies, not development-only dependencies.
The service image keeps the installed packages and their license files.
The model and package bytes stay external to the repository.
Sources accessed: 2026-10-25. See `docs/research/q7t-local-tts.md` for the pinned sources and quotations.

### Piper runtime

```text
Piper 1.8.0, OHF-Voice/piper1-gpl. GPL-3.0-or-later. Keep the GPL text and corresponding source with redistributed binaries.
```

The package declares `GPL-3.0-or-later`:
https://pypi.org/pypi/piper-tts/1.8.0/json

The pinned wheel `METADATA` names `The Home Assistant Authors` as the author.
Its `licenses/COPYING` file has this license-text copyright line:

```text
Copyright (C) 2007 Free Software Foundation, Inc. <http://fsf.org/>
```

This line applies to the GPL text, not the Piper program.
The wheel supplies no Piper program copyright line. The author field is not a program copyright statement.

License text and `corresponding source`:
https://github.com/OHF-Voice/piper1-gpl/tree/639388b6317fc4731e91d53da42aea68fd4166ff

The project code stays Apache-2.0. Piper code does not become Apache-2.0.
Binary packaging must keep the GPL text, upstream notices, and the necessary `corresponding source`.

### Lessac voice and configuration

```text
en_US-lessac-medium. Dataset: Voice Factory International Inc. and Lessac Technologies Inc., Blizzard 2013. Custom research-only dataset terms. No separate model-weight grant or attribution clause is stated in the model card.
```

```text
en_US-lessac-medium configuration and phoneme-ID map. The model card links dataset terms but states no separate configuration license.
```

The manifest uses `NOASSERTION` for these two files. It does not invent a model-weight license from the dataset license.
The dataset terms are `LicenseRef-Blizzard-2013-Research`:
https://www.cstr.ed.ac.uk/projects/blizzard/2013/lessac_blizzard2013/license.html

The pinned `model card` gives the URL for those terms:
https://huggingface.co/rhasspy/piper-voices/blob/5041a7a49ae5b3a0f30e6837769eb03fdbb231bd/en/en_US/lessac/medium/MODEL_CARD

No dataset recording is test audio. No dataset audio is in this repository.

### G2P library and data

```text
Piper eSpeak NG bridge and ucd-tools. GPL-3.0-or-later. Preserve the upstream notices, license, and corresponding source.
```

```text
eSpeak NG data and Unicode data. GPL-3.0-or-later AND Unicode-DFS-2016. Keep the copyright and permission notices with the data.
```

Piper uses `static linking` for the bridge, eSpeak NG, and ucd-tools at this source revision:
https://github.com/espeak-ng/espeak-ng/tree/724808c5a83f9ef95fdd0db886ba7ba537ff224a

The source includes `COPYING`, `COPYING.UCD`, and more file-level notices.
Unicode notice source:
https://github.com/espeak-ng/espeak-ng/blob/724808c5a83f9ef95fdd0db886ba7ba537ff224a/COPYING.UCD

```text
Copyright © 1991-2018 Unicode, Inc. All rights reserved.
Distributed under the Terms of Use in http://www.unicode.org/copyright.html.

Permission is hereby granted, free of charge, to any person obtaining
a copy of the Unicode data files and any associated documentation
(the "Data Files") or Unicode software and any associated documentation
(the "Software") to deal in the Data Files or Software
without restriction, including without limitation the rights to use,
copy, modify, merge, publish, distribute, and/or sell copies of
the Data Files or Software, and to permit persons to whom the Data Files
or Software are furnished to do so, provided that either
(a) this copyright and permission notice appear with all copies
of the Data Files or Software, or
(b) this copyright and permission notice appear in associated
Documentation.
```

### Python runtime libraries

```text
ONNX Runtime 1.31.0. Copyright (c) Microsoft Corporation. MIT. Preserve the package license and third-party notices.
```

```text
NumPy 2.5.4. Copyright (c) 2005-2025, NumPy Developers. BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0. Preserve the full package and bundled-library notices.
```

```text
pathvalidate 3.3.1. MIT. Preserve its copyright and permission notice from the pinned package.
Copyright (c) 2016-2025 Tsuyoshi Hombashi
```

Package declarations:

- https://pypi.org/pypi/onnxruntime/1.31.0/json
- https://pypi.org/pypi/numpy/2.5.4/json
- https://pypi.org/pypi/pathvalidate/3.3.1/json

The pinned wheels contain the complete upstream license files.
The examined files are Piper `licenses/COPYING`, pathvalidate `licenses/LICENSE`, NumPy `licenses/LICENSE.txt`, and ONNX Runtime `onnxruntime/LICENSE`.
The package `METADATA` files declare the licenses above. pathvalidate and ONNX Runtime declare `MIT License`.
The NumPy declaration includes its bundled-library licenses, not only BSD-3-Clause.

Runtime sources:

- Piper: https://github.com/OHF-Voice/piper1-gpl/tree/639388b6317fc4731e91d53da42aea68fd4166ff
- pathvalidate: https://github.com/thombashi/pathvalidate
- ONNX Runtime: https://github.com/microsoft/onnxruntime
- NumPy: https://github.com/numpy/numpy

Each binary image must keep those files and all bundled-library notices.
The release checker makes a different inventory from the image layers.

## URL parser

The cloud adapters declare `yarl` as a dependency.
The examined installed wheel is `yarl==1.25.1`.
Its `METADATA` declares `Apache-2.0`. Its `licenses/LICENSE` contains the Apache License, Version 2.0.
The wheel's `licenses/NOTICE` contains this text:

```text
   Copyright 2016-2021, Andrew Svetlov and aio-libs team

   Licensed under the Apache License, Version 2.0 (the "License");
   you may not use this file except in compliance with the License.
   You may obtain a copy of the License at

       http://www.apache.org/licenses/LICENSE-2.0

   Unless required by applicable law or agreed to in writing, software
   distributed under the License is distributed on an "AS IS" BASIS,
   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
   See the License for the specific language governing permissions and
   limitations under the License.
```

Keep the wheel's license and notice in each binary package.
Source: https://pypi.org/project/yarl/1.25.1/.

## Bundled package data

The image keeps the installed runtime wheels without changes. This includes package data that the selected providers do not use.
The records in `assets/manifest.json` give each model-like file, size, SHA-256, wheel source, and license quotation.
These files do not select a different provider or voice.
Only `onnx_asr/preprocessors/data/fbanks.npz` supplies data for the selected NumPy preprocessor.
The selected providers do not use the other bundled ONNX files and NumPy arrays.

```text
numpy 2.5.4. BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0. Preserve the wheel license and notices.
onnx-asr 0.12.0. MIT. Preserve the wheel license and notices.
onnxruntime 1.31.0. MIT. Preserve the wheel license and notices.
piper-tts 1.8.0. GPL-3.0-or-later. Preserve the wheel license and notices.
Nakdimon. Copyright 2022, Elazar Gershuni. MIT. Preserve piper/hebrew/LICENSE and SOURCE.
```

The Nakdimon file has its own MIT notice in `piper/hebrew/LICENSE`.
The Piper wheel also has a different Apache license for parts of the `g2pW` bridge.
That notice does not change the license of Piper or the Lessac voice.
The complete upstream notices stay in the installed packages.

The image and server command set `ORT_DISABLE_TELEMETRY=1` before ONNX Runtime loads, as specified in `onnxruntime/Privacy.md`, to disable default 1DS telemetry.

## Network and runtime libraries

The image also contains these Python dependencies.
Their wheel metadata gives these license statements:

| Package | Version | License statement |
|---|---|---|
| aiohappyeyeballs | 2.7.1 | PSF-2.0 |
| aiohttp | 3.14.5 | Apache-2.0 AND MIT |
| aiosignal | 1.4.0 | Apache 2.0 |
| attrs | 26.1.0 | MIT |
| flatbuffers | 25.12.19 | Apache 2.0 |
| frozenlist | 1.8.0 | Apache-2.0 |
| idna | 3.20 | BSD-3-Clause |
| multidict | 7.2.1 | Apache-2.0 |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause |
| propcache | 0.5.4 | Apache-2.0 |
| protobuf | 7.36.2 | 3-Clause BSD License |

The image keeps the license and notice files from each wheel.

`uv.lock` gives the versioned PyPI wheel URLs and SHA-256 values.

`/app/wheel-provenance.json` identifies the installed wheel for each external runtime package.

The image checker compares this record with the manifest and installed metadata.

## Base image and operating-system packages

The base is the official `python:3.14.3-slim-bookworm` multi-platform image:

```text
sha256:f21c0d5a44c56805654c15abccc1b2fd576c8d93aca0a3f74b4aba2dc92510e2
```

Python uses the PSF license. Its license text is `/usr/local/lib/python3.14/LICENSE.txt`.
Source: https://www.python.org/downloads/release/python-3143/.
The base image also keeps its `pip` installer, its MIT notice files, and its Python headers.
The server does not use these files. The image does not delete base-image package files.

The image build uses `uv==0.13.1` only in its `build stage`. `uv` uses MIT OR Apache-2.0.
Source: https://github.com/astral-sh/uv/tree/0.13.1.

The Debian base keeps package notices in `/usr/share/doc/<package>/copyright`.
Some package notices use `symbolic links` to the source package notice.
Full license texts are in `/usr/share/common-licenses`.
Debian source packages and copyright records are available from https://sources.debian.org/.
The base digest sets the OS package bytes. The build installs no more OS packages.

The base package inventory is:

```text
adduser apt base-files base-passwd bash bsdutils ca-certificates coreutils dash
debconf debian-archive-keyring debianutils diffutils dpkg e2fsprogs findutils
gcc-12-base gpgv grep gzip hostname init-system-helpers libacl1 libapt-pkg6.0
libattr1 libaudit-common libaudit1 libblkid1 libbz2-1.0 libc-bin libc6 libcap-ng0
libcap2 libcom-err2 libcrypt1 libdb5.3 libdebconfclient0 libext2fs2 libffi8
libgcc-s1 libgcrypt20 libgdbm6 libgmp10 libgnutls30 libgpg-error0 libhogweed6
libidn2-0 liblz4-1 liblzma5 libmd0 libmount1 libncursesw6 libnettle8 libp11-kit0
libpam-modules libpam-modules-bin libpam-runtime libpam0g libpcre2-8-0
libreadline8 libseccomp2 libselinux1 libsemanage-common libsemanage2 libsepol2
libsmartcols1 libsqlite3-0 libss2 libssl3 libstdc++6 libsystemd0 libtasn1-6
libtinfo6 libudev1 libunistring2 libuuid1 libxxhash0 libzstd1 login logsave mawk
mount ncurses-base ncurses-bin netbase openssl passwd perl-base readline-common
sed sysvinit-utils tar tzdata usr-is-merged util-linux util-linux-extra zlib1g
```

`gcc-12-base` supplies runtime records, not a compiler.
The build does not copy a compiler, `uv`, or a wheel-build environment to the last image.

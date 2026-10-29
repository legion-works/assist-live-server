# Verification record

## Package metadata

Home Assistant 2026.10.3 has Python floor `>=3.14.2`.
Source: https://pypi.org/pypi/homeassistant/2026.10.3/json, field `info.requires_python`.

The standalone package test ran before package metadata was available:

```sh
cd $WORKSPACE/assist-live-server && uv run --no-project --with pytest pytest tests/test_package.py -q
```

Result: `ModuleNotFoundError: No module named 'assist_live_client'`. Date: 2026-10-24.

## Package checks

Command:

```sh
cd $WORKSPACE/assist-live-server && uv sync --locked && uv run pytest tests/test_package.py -q && uv run python tools/check_bootstrap.py
```

Result: `1 passed` and `PASS server bootstrap`. Date: 2026-10-24.

Command:

```sh
cd $WORKSPACE/assist-live-server && uv run python -c "import assist_live_server, assist_live_client; print('import ok')"
```

Result: `import ok`. Date: 2026-10-24.

Command: `cd $WORKSPACE/assist-live-server && uv tree`.

Result: the workspace root lists the server and client. The client has no dependencies. Date: 2026-10-24.

## Formatting and lint

Command: `cd $WORKSPACE/assist-live-server && uvx ruff format --check . && uvx ruff check .`.

Result: `14 files already formatted` and `All checks passed!`. Date: 2026-10-24.

## Documentation checks

The ASD-STE100 checker reported no findings. Date: 2026-10-24.

## Repository checks

The Git root check printed `PASS own Git root`. Date: 2026-10-24.

The public-file checker read 22 files. It found 0 private-denylist terms, 0 absolute home paths, and 0 RFC1918 addresses. Date: 2026-10-24.

The package test also ran from the client directory:

```sh
cd $WORKSPACE/assist-live-server/client && uv run pytest ../tests/test_package.py -q
```

Result: `1 passed`. Date: 2026-10-24.

Dependency checks rejected `tensorflow`, `huggingface-hub`, and `faster-whisper` with `FAIL client model-runtime dependencies`. They accepted `pydantic-model` and printed `PASS server bootstrap`. The missing app descriptor check printed `FAIL app descriptor`. Date: 2026-10-24.

The checker import test printed `PASS importable checker` and did not start package checks. A failure-collection probe printed 10 named `FAIL` lines to stdout. It printed no traceback. Date: 2026-10-24.

## Deferred checks

There are no protocol contract tests at this time. CI builds the client and server packages.
It installs the client wheel alone and checks that there is no server module.
Type checks and unit tests stop on warnings. HA runtime tests and HACS validation belong to the integration repository.

## Review of all three repositories

On 2026-10-24, the review checked `assist-live`, `assist-live-server`, and `assist-live-esphome` for cross-repository contracts and convention drift.
It also checked comment and prose hygiene and privacy across history.
Result: the server documents did not follow the shared conventions.
This change makes the repository documents follow the shared conventions.
It also gives the bootstrap checker a main entry point, complete failure reporting, stdout results, and a Python-floor check.

A static analysis snapshot reported 0 errors, 0 warnings, 0 info, and 0 hints across 5 source files. Date: 2026-10-24.

## Asset license policy

The project is experimental and has no release. There is no server image at this time.
The asset gate checks included files and startup downloads.
The ESPHome source stays external to the Apache server image and this policy.

The manifest accepts an asset list or an object with an `assets` list.
The object sets `release_root` relative to the manifest directory.
`image_inventory` and `download_plan` are asset-ID lists or file paths.
These paths are relative to the release root. Each file contains a JSON asset-ID list.

A null image inventory means that there is no image inventory. The gate prints `NOT CHECKED image inventory`.
It does not claim an image check in that state.

Each model, voice, runtime, phonemizer, tokenizer, and voice-data part must have its own record or a nested `components` record.
Each included record must have these fields:

- `id`, `kind`, and `status: confirmed_included`.
- An HTTPS `url` with the immutable `revision`, and a 64-digit `sha256`.
- An approved SPDX `license` and pinned `license_sources` for the file.
- `redistribution: true`, `download: included` or `startup`, and non-empty `attribution`.
- Relative artifact `paths` and an explicit `components` list, empty if there are no parts.

Approved licenses are `Apache-2.0`, `MIT`, `BSD-2-Clause`, `BSD-3-Clause`, `CC0-1.0`, and `CC-BY-4.0`.
Other licenses stop the release. This includes unknown, non-commercial, and no-derivatives terms and GPL, AGPL, or LGPL components.

Each source URL must contain its own pinned revision or package version.
A badge URL is not a license grant.
The gate checks the recorded facts. A rights investigation must check the source text before the manifest includes the record.

The gate checks SHA-256 against each available local artifact. A startup downloader must check that hash before it uses a downloaded file.
An image or download-plan ID without an included record stops the release.
The release-tree check stops on model, audio, or runtime files with no manifest record.
It does not include development environments, caches, or build output. The image inventory must list all image assets.

A provider configuration starts when `server/assist_live_server/providers/` contains a Python module other than `__init__.py`.
The check includes subdirectories.
After that signal, a release must have one or more included assets and a non-null image inventory.
An empty included-asset list is satisfactory only before that signal.
Provider changes must list all transitive parts and update the inventory and download plan.

`confirmed_blocked_absent` records must have paths to check and `download: none`.
The gate checks those paths in the release tree.
It examines runtime source and package metadata for the asset ID, URL, and listed identifiers.
It also checks the image inventory and the download plan. Tests and rights documents are not runtime inputs.

Unconfirmed artifact hashes and revisions can stay empty for blocked assets.
Their `license_sources` must contain source text and a clear scope, with or without a file-level grant.
Those records are not permission to supply the assets.

Piper has a `confirmed_blocked_absent` record.
Smart Turn v3.2 has a `confirmed_included` record and a planned startup download.
Its ONNX file has a pinned revision, SHA-256, and file-level BSD-2-Clause sources.
There is no startup downloader or server image at this time.

Asset tests ran before the checker was available:

```sh
cd $WORKSPACE/assist-live-server && uv run pytest tests/test_asset_gate.py -q
```

Result: `ModuleNotFoundError: No module named 'tools'`, `4 failed`. Date: 2026-10-24.

## CI and release boundaries

`task check` runs formatting, lint, type checks, unit tests, package checks, asset checks, and the default Gitleaks rules.
The project sets Ruff rule groups `E`, `F`, `W`, `I`, `B`, and `UP`, with target `py314` from the recorded Python floor.
Mypy checks the server, client, and tools. Pytest stops on warnings.

## Client dependency range

The client accepts `aiohttp` versions from 3.14.4 to less than 4. The server keeps its pinned dependency.

The dependency guard reported a failure before the metadata change because the client requirement was `aiohttp==3.14.5`. The assertion was `assert aiohttp.specifier.contains("3.14.4")`. Date: 2026-10-25.

The guard reported success after the metadata change. The wheel installed with `aiohttp==3.14.4` on Python 3.14.2. A client import, `TurnOpen` construction, and a `parse_json` and `encode_control` cycle completed. The client-only tests do not include this import and protocol smoke test.

Dry-run resolution with the wheel and Home Assistant 2026.10.1 on Python 3.14.2 selected `aiohttp==3.14.4`. Dry-run resolution with Home Assistant 2026.10.3 selected `aiohttp==3.14.5`. Date: 2026-10-25.
The Pyright client import path stays. Pyright and Mypy also have server and repository import paths.

CI runs the same named tasks. `ci-complete` is the single mandatory check.
It runs after each other `job`, also on failure.
A result other than `success`, such as `skipped` or `cancelled`, stops the check.
Tests parse the workflow and run its last check with each result value.

The contract check builds the two wheels and source packages in temporary directories.
It installs each wheel in an isolated environment and imports it from an external directory.
The client check also refuses a server module in the client wheel or environment.
These are package-boundary checks, not protocol compatibility claims.

The two release workflows accept manual dispatch only.
Their first step checks `vars.ASSIST_LIVE_RELEASES_APPROVED == 'true'`.
The repository `variable` is not set. The operator sets it only after explicit publication approval.

### Release metadata

Release-please uses manifest mode for the server and client, with different component tags.
Only its `job` has write permissions, for release metadata and pull requests.
The artifact workflow checks the server and client in isolated `jobs` in runner temporary storage.
It uploads and publishes nothing. It has no PyPI or GHCR step.

## GitHub Actions pins

Each GitHub Actions step uses a full commit SHA. GitHub API tag records gave these pins on 2026-10-24:

| Action | Version | Commit SHA | Source command |
|---|---|---|---|
| `actions/checkout` | `v4.3.1` | `34e114876b0b11c390a56381ad16ebd13914f8d5` | `gh api repos/actions/checkout/git/ref/tags/v4.3.1` |
| `astral-sh/setup-uv` | `v7.1.2` | `85856786d1ce8acfbcc2f13a5f3fbd6b938f9f41` | `gh api repos/astral-sh/setup-uv/git/ref/tags/v7.1.2` |
| `go-task/setup-task` | `v1.0.0` | `0ab1b2a65bc55236a3bc64cde78f80e20e8885c2` | `gh api repos/go-task/setup-task/git/ref/tags/v1.0.0` |
| `actions/setup-go` | `v5.5.0` | `d35c59abb061a4a6fb18e82ac0862c26744d6ab5` | `gh api repos/actions/setup-go/git/ref/tags/v5.5.0` |
| `googleapis/release-please-action` | `v4.4.0` | `16a9c90856f42705d54a6fda1823352bdc62cf38` | `gh api repos/googleapis/release-please-action/git/ref/tags/v4.4.0` |

All five tag records point directly to commits. No annotated-tag step was necessary.
CI pins `uv` to `0.13.1`, Task to `3.45.5`, Go to `1.26.1`, Actionlint to `1.7.12`, and Gitleaks to `8.30.0`.

## Asset and CI checks

Command:

```sh
cd $WORKSPACE/assist-live-server && uv run pytest tests/test_asset_gate.py tests/test_ci.py -q && task check && uv run python tools/check_assets.py --manifest assets/manifest.json --release && gitleaks dir . --redact --exit-code 1
```

Result: `40 passed` for the asset and CI tests, and `41 passed` for all tests.
The output includes `PASS CI boundaries`, `PASS server bootstrap`, `PASS isolated client and server packages`, and `PASS asset licenses`.
Ruff reports `All checks passed!`. Mypy reports `Success: no issues found in 5 source files`.

Actionlint has no output. Gitleaks reports `no leaks found`. Date: 2026-10-24.

The image check reports `NOT CHECKED image inventory: no image inventory exists`.
There is no claim about a built image.

Command: `python3 -` with a temporary copy of the public release tree and five manifest mutations.

Result: the complete permissive test asset is satisfactory.
Each mutation stops the release: a missing hash, a non-commercial license, a badge-only source, a GPL startup runtime, and a blocked file in the tree.
Date: 2026-10-24.

The ASD-STE100 check reports 0 findings in this record and the pull request template.
The public-file self-scan reports 0 private terms, 0 absolute home paths, and 0 private network addresses.
The public-tree check reports `PASS history secrets commits=2` and `PASS public data matches=0`.
A static analysis snapshot reports 0 errors and 0 warnings across 10 source files. Date: 2026-10-24.

## Smart Turn input contract

The first input-window test ran before the probe module was available:

```sh
cd $WORKSPACE/assist-live-server && uv run pytest tests/test_probe_smart_turn.py -q
```

Result: `ModuleNotFoundError: No module named 'tools.probe_smart_turn'`, `1 failed`.
Date: 2026-10-24.

The input contract is eight seconds of mono audio at 16 kHz.
The probe keeps the last 128000 samples and adds `zero padding` on the left for shorter inputs.
`WhisperFeatureExtractor(chunk_length=8)` uses the prototype arguments.
The ONNX input is `input_features`, with shape `(1, 80, 800)` and dtype `float32`.
The extractor uses constructor defaults. It downloads no configuration or tokenizer.
No external tensor file is necessary for the model session.

## Smart Turn dependency metadata

The probe uses development dependencies only.
The server and client package dependencies do not change.
The client has no model-runtime dependencies.
`uv.lock` pins the probe dependencies, their transitive dependencies, and wheel SHA-256 values.

All new package requirements accept Python `3.14.2`. The `flatbuffers` field is empty.
The CI interpreter is Python `3.14.3`.

Source for each row: `https://pypi.org/pypi/<package>/<version>/json`, field `info.requires_python`.
Access date: 2026-10-24.
The check compared each requirement with Python `3.14.2` through `packaging.specifiers.SpecifierSet`.
Result: `PASS dependency floors packages=27`.

| Package | Version | `requires_python` |
|---|---|---|
| `annotated-doc` | `0.0.5` | `>=3.9` |
| `anyio` | `4.15.1` | `>=3.10` |
| `click` | `8.5.0` | `>=3.10` |
| `filelock` | `4.2.4` | `>=3.11` |
| `flatbuffers` | `25.12.19` | Empty |
| `fsspec` | `2026.9.0` | `>=3.10` |
| `h11` | `0.16.0` | `>=3.8` |
| `hf-xet` | `1.7.0` | `>=3.8` |
| `httpcore2` | `2.16.0` | `>=3.11` |
| `httpx2` | `2.16.0` | `>=3.11` |
| `httpx2-jsfetch` | `1.0` | `>=3.12` |
| `huggingface-hub` | `2.3.0` | `>=3.10.0` |
| `idna` | `3.20` | `>=3.9` |
| `markdown-it-py` | `4.2.0` | `>=3.10` |
| `mdurl` | `0.1.2` | `>=3.7` |
| `numpy` | `2.5.4` | `>=3.12` |
| `onnxruntime` | `1.31.0` | `>=3.11` |
| `protobuf` | `7.36.2` | `>=3.10` |
| `regex` | `2026.9.29` | `>=3.10` |
| `rich` | `15.0.0` | `>=3.9.0` |
| `safetensors` | `0.8.0` | `>=3.10` |
| `shellingham` | `1.5.4` | `>=3.7` |
| `tokenizers` | `0.23.3` | `>=3.10` |
| `tqdm` | `4.70.1` | `>=3.8` |
| `transformers` | `5.20.2` | `>=3.10.0` |
| `truststore` | `0.10.5` | `>=3.10` |
| `typer` | `0.27.3` | `>=3.10` |

Probe dependency license declarations:

- NumPy: `info.license_expression` is `BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0`.
  Source: https://pypi.org/pypi/numpy/2.5.4/json.
- ONNX Runtime: `info.license` is `MIT License`.
  Source: https://pypi.org/pypi/onnxruntime/1.31.0/json.
- Transformers: `info.license` is `Apache 2.0 License`.
  Source: https://pypi.org/pypi/transformers/5.20.2/json.

Mypy uses explicit package bases for the `tools` namespace.
This prevents duplicate module names when one tool imports a different tool.
The NumPy-only extractor uses no PyTorch model.
Its optional-framework advisory is not applicable to this use.
The upstream logging interface supplies `TRANSFORMERS_NO_ADVISORY_WARNINGS` for that condition.
Source: https://huggingface.co/docs/transformers/main_classes/logging.

## Smart Turn rights and cost

The file-level rights sources and cost table are in `docs/research/q5-smart-turn.md`.
The full BSD-2-Clause notice is in `THIRD_PARTY_NOTICES.md`.
The rights decision uses a pinned `model card` and the model project's full license text.
The project names the v3.2 model and explicitly includes model weights.
It supplies a URL for the pinned file's repository.
The downloaded bytes agree with the recorded SHA-256.

The measurement uses 100 local synthetic candidates after five warm-up candidates.
It reports process CPU time and wall time independently, for `feature extraction`, inference, and the total.
It records p50 and p90 for each phase and clock.
ONNX intra-op and inter-op `thread counts` are 1. Numerical-library `thread limits` are also 1.

This development host is not a validated target host.
There is no claim about correct model decisions or release performance.

Command:

```sh
cd $WORKSPACE/assist-live-server && uv run pytest tests/test_probe_smart_turn.py -q && uv run python tools/probe_smart_turn.py --record-decision --allow-blocked-absent --candidates 100 --report docs/research/q5-smart-turn.md && uv run python tools/check_assets.py --manifest assets/manifest.json --release
```

Result: `13 passed`, `PASS decision recorded`, `PASS included rights`, and `PASS asset licenses`.
The image check prints `NOT CHECKED image inventory: no image inventory exists`.
Date: 2026-10-24.

Command: `cd $WORKSPACE/assist-live-server && task check`.

Result: `22 files already formatted`, `All checks passed!`, `PASS CI boundaries`, and `54 passed`.
Mypy prints `Success: no issues found in 6 source files`.
The package checks print `PASS server bootstrap` and `PASS isolated client and server packages`.
The asset check prints `PASS asset licenses`. The `secret scan` reports `no leaks found`.
Date: 2026-10-24.

Command: `uv run --locked --python 3.14.3 pytest tests/ -q` with an isolated temporary environment.

Result: `54 passed`. The same command with the Python floor, `3.14.2`, also reports `54 passed`.
Date: 2026-10-24.

The ASD-STE100 checker reports 0 findings in this record, the research report, and the notices.
The public-file self-check reads 43 files and reports 0 private terms, 0 home paths, and 0 private addresses.
The public-tree check with `--history` reports `PASS own Git root`, `PASS history secrets commits=3`, and `PASS public data matches=0`.
A current Python analysis reports 0 errors and 0 warnings for the two changed Python files.
Date: 2026-10-24.

## Local synthesis rights test

The first rights test ran before the Kokoro checker was available:

```sh
cd $WORKSPACE/assist-live-server && uv run pytest tests/test_kokoro_licenses.py -q
```

Result: `ModuleNotFoundError: No module named 'tools.probe_kokoro_licenses'`, `1 failed`.
Date: 2026-10-24.

## Local synthesis package boundaries

The actual candidate packages cannot run in the server environment as packaged.
Version-specific PyPI metadata gives these requirements:

| Package | Version | `info.requires_python` | Source |
|---|---|---|---|
| `kokoro` | `0.9.4` | `<3.13,>=3.10` | https://pypi.org/pypi/kokoro/0.9.4/json |
| `misaki` | `0.9.4` | `<3.13,>=3.8` | https://pypi.org/pypi/misaki/0.9.4/json |

Access date: 2026-10-24.
The server floor is `>=3.14.2`. No package or Python-floor change follows from this inspection.
The repository checker uses the standard library only.
The root development group, server dependencies, client dependencies, and lock file stay unchanged.

The candidate inspection used an isolated Python `3.12` environment in scratch storage.
It installed `kokoro==0.9.4`, `misaki==0.9.4`, `torch==2.6.0+cpu`, and `transformers==4.51.3`.
The install resolver supplied the remaining declared dependencies.
The inspection installed no system package or runtime external to that scratch environment.

Commands, with `$PROBE` set to the isolated scratch directory:

```sh
uv venv --python 3.12 $PROBE/venv
uv pip install --python $PROBE/venv/bin/python --index-url https://download.pytorch.org/whl/cpu torch==2.6.0+cpu
uv pip install --python $PROBE/venv/bin/python kokoro==0.9.4 misaki==0.9.4 transformers==4.51.3
```

Result: the candidate and its declared English phonemizer dependencies installed in scratch storage.
The model, voice, and first-use G2P model loaded there. Date: 2026-10-24.

## Local synthesis first-use inspection

The inspection used `KPipeline(lang_code="a", trf=False, device="cpu")` and candidate voice `af_heart`.
The model download calls used Hugging Face revision `f3ff3571791e39611d31c381e3a41a3af07b4987`.
The model and voice loaded without synthesis. The inspection made no audio file.
All download caches stayed in scratch storage.

Downloads:

- `config.json`: `5abb01e2403b072bf03d04fde160443e209d7a0dad49a423be15196b9b43c17f`.
- `kokoro-v1_0.pth`: `496dba118d1a58f5f3db2efc88dbdc216e0483fc89fe6e47ee1f2c53f18ad1e4`.
- `voices/af_heart.pt`: `0ab5709b8ffab19bfd849cd11d98f75b60af7733253ad0d67b12382a102cb4ff`.
- `en_core_web_sm-3.8.0-py3-none-any.whl`: `1932429db727d4bff3deed6b34cfc05df17794f4a52eeb26cf8928f7c1a0fb85`.

The voice tensor shape was `(510, 1, 256)`.
The inspection tested the English out-of-dictionary fallback with synthetic text.
The active bundled eSpeak library reported version `1.52.0`.
Its SHA-256 was `b15ce0803b26254e8a0d088c6b9281a2637b2b02613147cc320cfdefaf2c3ccf`.

The installed loader source bytes agree with the pinned upstream source.
The wheel has no license file, although the loader source has an MIT license.
The loaded library and its data have GPL rights and Unicode data notices.
The data inventory includes 364 files, with individual SHA-256 values in scratch storage.
The manifest records the necessary English data digests and the complete tree digest.

The inspection also checked packaged Misaki dictionaries, `phonemizer-fork`, `num2words`, and spaCy model notices.
GPL and LGPL components stop inclusion in the image policy.
The unconfirmed voice and configuration scopes also stop inclusion.
The full source records are in `docs/research/q7-kokoro.md` and `assets/manifest.json`.

## Local synthesis release checks

The decision is `confirmed_blocked_absent`, with an explicit empty `kokoro_default_voices` allowlist.
No voice name, digest, or attribution can go into that allowlist without file-scoped rights for the complete necessary chain.
The release check also examines blocked paths, runtime source, image-inventory records, and the startup download plan.
No Kokoro component is in that plan. The Smart Turn record stays unchanged.

Command:

```sh
cd $WORKSPACE/assist-live-server && uv run pytest tests/test_kokoro_licenses.py -q && uv run python tools/probe_kokoro_licenses.py --record-decision --allow-blocked-absent --check assets/manifest.json --report docs/research/q7-kokoro.md && uv run python tools/check_assets.py --manifest assets/manifest.json --release
```

Result: `20 passed`, `PASS decision recorded`, `PASS blocked asset absent`, and `PASS asset licenses`.
The image check prints `NOT CHECKED image inventory: no image inventory exists`.
Date: 2026-10-24.

Command: `cd $WORKSPACE/assist-live-server && task check`.

Result: `25 files already formatted`, `All checks passed!`, `PASS CI boundaries`, and `74 passed`.
Mypy prints `Success: no issues found in 7 source files`.
The package checks print `PASS server bootstrap` and `PASS isolated client and server packages`.
The asset check prints `PASS asset licenses`. The `secret scan` reports `no leaks found`.
Date: 2026-10-24.

Command: `uv run --locked --python 3.14.3 pytest tests/ -q` with an isolated temporary environment.

Result: `74 passed`. The same command with Python `3.14.2` also reports `74 passed`.
Date: 2026-10-24.

The ASD-STE100 checker reports 0 findings in the research report, notices, and this record.
The public-file self-check reads 46 files and reports 0 private terms, 0 home paths, and 0 private addresses.
The public-tree check with `--history` reports `PASS own Git root`, `PASS history secrets commits=4`, and `PASS public data matches=0`.
Date: 2026-10-24.

## Voice detector rights test

Command: `cd $WORKSPACE/assist-live-server && uv run pytest tests/test_silero_license.py -q`.

Before the checker was available, the result was `ModuleNotFoundError: No module named 'tools.probe_silero_license'`, `1 failed`.
Date: 2026-10-24.

## Voice detector source and input contract

Command: `gh api repos/snakers4/silero-vad/tags --jq '[.[]|{name,sha:.commit.sha}]'`.

Result: tag `v6.2.3` names commit `5cd7945676eb32225748052e2e6a0580e4686a08`.
Date: 2026-10-24.

The selected file is `src/silero_vad/data/silero_vad.onnx` at that commit.
Its SHA-256 is `1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3`.
The file size is `2327524` bytes.
The `silero_vad-6.2.3-py3-none-any.whl` SHA-256 is `7b7f5436cfcb02fae583a05b512ea96467fd449fe54cb49a5e4f06c51a1e43b8`.
Its `silero_vad/data/silero_vad.onnx` member has the same model bytes.

PyPI metadata source: https://pypi.org/pypi/silero-vad/6.2.3/json .
Its `Requires-Python` is `>=3.8`, which includes the server floor `3.14.2`.
The checker uses the standard library only. No repository package or dependency changed.
The model and package stayed in scratch storage for inspection.
Access date: 2026-10-24.

The prototype names `silero_vad.onnx`, but its binary and source pin are not available.
Its byte identity stays `unverified`. This investigation selects a named release file for the rights check.
The installed ONNX Runtime `1.31.0` loaded the selected file and printed `PASS prototype ONNX input contract`.

Its actual inputs are `input: tensor(float)`, `state: tensor(float)`, and `sr: tensor(int64)`.
The state shape is `[2, null, 128]`. The sampling-rate input is scalar.
The embedded model metadata has no license statement.

## Voice detector license sources

The repository `LICENSE`, README body, package classifier, and attached package license state MIT.
README line `1` and wheel `METADATA` line `51` have badge alt text `License: CC BY-NC 4.0`.
The image URL says MIT and has the repository license as its `link target`.
The MIT and non-commercial claims stay unresolved for the selected model file.
The badge is not a file-license grant.

The repository and package license-text SHA-256 is `2e63e9a38b6e8fc0c7bc37ce174caca1862870856c6daf5697cfb785e925520b`.
The wheel metadata SHA-256 is `ff91344bc524cfe9970eabf27b7ec68e721aa32968079b0012b797803725b529`.
The investigation examined all textual package members for license statements.
Six license-related lines occur in `METADATA` and its attached `LICENSE`.
The package and selected model directory have no `model card`.
The manifest and research report keep each statement, source location, URL, and file digest.

The Silero rights records use only `license_sources`.
Each source has `kind`, `url`, `quote`, `revision_or_version`, and `file_binding`.
The `file_binding` has the selected model `path` and `sha256` in the same entry.
The checker reads the claims from `quote`. The earlier asset records stay unchanged.

## Voice detector clarification search

Commands:

```sh
gh search issues --repo snakers4/silero-vad license --json number,title,url --limit 10
gh search issues --repo snakers4/silero-vad 'CC BY-NC' --json number,title,url --limit 30
gh search issues --repo snakers4/silero-vad badge --json number,title,url --limit 30
gh search issues --repo snakers4/silero-vad commercial --json number,title,url --limit 30
gh api --paginate --slurp 'repos/snakers4/silero-vad/discussions?per_page=100'
gh api --paginate --slurp 'repos/snakers4/silero-vad/commits?path=README.md&per_page=100'
```

Results: `license=5`, `CC BY-NC=1`, `badge=0`, and `commercial=5`.
The discussion query gave `263` titles and bodies. The history query gave `108` root README commits.
The positive `license` results checked that the query could find known issues.
The investigation read nine issues and their comments, plus replies to the two related discussions.

The owner replies are about custom training, tuning, and conversion services.
No examined `OWNER` or `MEMBER` statement grants a license for this model file and revision.
Source URLs, dates, associations, and source statements are in `docs/research/q8-silero.md`.

The README rename led to the initial `silero-vad/README.md` path.
Its history has one commit: `56860ee673881e211ff2bda6f560cf9bc496575e`, dated `2020-11-23T10:28:37Z`.
The diff adds the non-commercial alt text with an AGPL image.
The `link target` is a different model project.
Commit `dbf3adf06046fee7caa4fef096974c1891b3440d`, dated `2020-11-23T10:42:52Z`, changes the image to MIT but keeps the alt text.

This history suggests inherited text. It supplies no license clarification for the selected model.

The historical AGPL image is not a license finding for the selected release.

The investigation made no upstream contact.
The research report has a proposed question for operator approval.
Access date: 2026-10-24.

## Voice detector release checks

Command:

```sh
cd $WORKSPACE/assist-live-server && uv run pytest tests/test_silero_license.py -q && uv run python tools/probe_silero_license.py --record-decision --allow-blocked-absent --check assets/manifest.json --report docs/research/q8-silero.md && uv run python tools/check_assets.py --manifest assets/manifest.json --release
```

Result: `31 passed`, `PASS decision recorded`, `PASS blocked asset absent`, and `PASS asset licenses`.
The image check prints `NOT CHECKED image inventory: no image inventory exists`.
Date: 2026-10-24.

Command: `cd $WORKSPACE/assist-live-server && task check`.

Result: `28 files already formatted`, `All checks passed!`, `PASS CI boundaries`, and `105 passed`.
Mypy prints `Success: no issues found in 8 source files`.
The package checks print `PASS server bootstrap` and `PASS isolated client and server packages`.
The asset check prints `PASS asset licenses`. The `secret scan` reports `no leaks found`.
Date: 2026-10-24.

Command: `uv run --locked --python 3.14.3 pytest tests/ -q` with an isolated temporary environment.

Result: `105 passed`. The same command with Python `3.14.2` also reports `105 passed`.
Date: 2026-10-24.

The ASD-STE100 checker reports 0 findings in this record, the research report, and the notices.
The public-file self-check reads 49 files and reports 0 private terms, 0 home paths, and 0 private addresses.
The public-tree check with `--history` reports `PASS own Git root`, `PASS history secrets commits=5`, and `PASS public data matches=0`.
The three earlier asset records are byte-identical. Their probes and the package metadata stay unchanged.
Date: 2026-10-24.

## Rights source record contract

All 16 asset and component records use one `license_sources` shape.
The six support-package records use the same shape.
Each entry has `kind`, `url`, `quote`, and `revision_or_version`.
It also has `file_binding` with `path` and `sha256`, or a non-empty `scope`.
The gate must find a non-empty `quote` from the cited source for included and blocked records.

An included record must have a license-text or model-card entry with the selected file SHA-256.
The download plan uses the same asset record and digest.
The source can have its own upstream-project revision, different from the model revision.
Its statement and file identity must be in the same entry.
`upstream_project` contains navigation only.
The gate refuses `evidence` and `license_claims` fields.

The source audit downloaded the pinned source files and package archives to temporary storage.
Each of the 45 source statements is one text segment from its cited source.
Package-archive fragments identify the source member.
The audit also checked dictionary bytes and the attached-license digests.

Command: `python $PROBE/audit_sources.py` against a saved initial manifest and the current manifest.

```text
PASS decision-bearing fields unchanged records=16
PASS canonical sources=45 quotes are contiguous verbatim source excerpts
PASS each fetched source digest and embedded dictionary digest verified
```

The compared fields include ID, status, license, revision, version, file SHA-256, URL, and download behavior.
Redistribution flags, the download plan, the default voice list, and the image inventory also stay unchanged.
Piper, Kokoro, and Silero stay blocked. Smart Turn stays included for the planned startup download.
Date: 2026-10-24.

## Shared source failure conditions

The new source-contract tests ran before the gate changes.

Command: `uv run pytest tests/test_asset_gate.py tests/test_probe_smart_turn.py -q`.

Result: `29 failed, 45 passed`.
The tests found missing source fields, legacy lists, and a missing file-digest bridge.
They also found report inputs that were not compatible.
They also found that corrupt cache recovery and saved-measurement use were not available.
The missing-quote test gave `AssertionError: assert False` before the check was available.
Date: 2026-10-24.

The gate reports the record name and missing field on stdout.
A mutation test removed `quote` from each top-level record in turn.

Command: `uv run python $PROBE/mutations.py`.

```text
FAIL asset licenses: piper-runtime: license source quote required
FAIL asset licenses: smart-turn-v3.2-cpu: license source quote required
FAIL asset licenses: kokoro-82m: license source quote required
FAIL asset licenses: silero-vad: license source quote required
PASS quote-removal mutations rejected=4 of 4
```

Each mutated gate command gave exit 1. The mutation script gave exit 0 after all four checks.
The gate also reports the missing source-field set for each mutation.
Date: 2026-10-24.

## Rights report generation

The three probes use the shared source reader.
Each probe can render all 16 asset and component records.
The renderers use manifest-owned source URLs, revisions, file hashes, and inspection values.
No rights decision reads a parallel source list.

The source data keeps the upstream line breaks, Markdown, and source-header comment prefixes.
The voice table's `af\_heart` identifier keeps its backslash in the source record.
The two non-contiguous Silero metadata lines have individual source entries.
These changes keep the license-statement words and the upstream format.

The saved Smart Turn measurement has its initial 100 rows and digest.
No model download, session construction, or cost measurement ran for report generation.
The cached JSON digest is `62270a7120211374463cda06c2fef1677247c411821fec8189b77fe18f10d1e7`.

Commands, with `ASSIST_LIVE_PROBE_DIRECTORY` set to temporary storage and `UV_LINK_MODE=copy`:

```sh
uv run pytest tests/test_probe_smart_turn.py -q
uv run python tools/probe_smart_turn.py --record-decision --reuse-measurements $PROBE/measurements.json --allow-blocked-absent --candidates 100 --report docs/research/q5-smart-turn.md
uv run python tools/check_assets.py --manifest assets/manifest.json --release
uv run pytest tests/test_kokoro_licenses.py -q
uv run python tools/probe_kokoro_licenses.py --record-decision --allow-blocked-absent --check assets/manifest.json --report docs/research/q7-kokoro.md
uv run python tools/check_assets.py --manifest assets/manifest.json --release
uv run pytest tests/test_silero_license.py -q
uv run python tools/probe_silero_license.py --record-decision --allow-blocked-absent --check assets/manifest.json --report docs/research/q8-silero.md
uv run python tools/check_assets.py --manifest assets/manifest.json --release
```

Results: Smart Turn `15 passed`, `PASS decision recorded`, and `PASS included rights`.
Kokoro reports `22 passed`, `PASS decision recorded`, and `PASS blocked asset absent`.
Silero reports `32 passed`, `PASS decision recorded`, and `PASS blocked asset absent`.
Each asset command reports `PASS asset licenses`.

Image inventory stays `NOT CHECKED`. There is no image.
Date: 2026-10-24.

The Smart Turn runtime and measurement text is byte-identical to the previous report.
The Kokoro inspection values and earlier source-statement words stay unchanged.
All earlier report SHA-256 values stay available in the reports.
The report diff contains source data and its display only, with the corrected phrase `six license statements`.

## Cached model validation

The model cache checks the file digest before use.
A corrupt cached file starts a new download, but replacement must have the correct SHA-256.
A bad download keeps the cached file unchanged and stops the command.
The tests use an in-memory downloader with no network.

The installed Transformers `5.20.2` source checks package availability in `is_torch_available()`.
It does not read the `USE_TORCH` or `USE_TF` settings.
Its `USE_TORCH_XLA` setting is a different setting.
The NumPy-only feature-extractor test gives `PASS` without those two assignments.
Date: 2026-10-24.

## Rights release checks

Command: `task check`.

```text
29 files already formatted
All checks passed!
PASS CI boundaries
Success: no issues found in 9 source files
148 passed
PASS server bootstrap
PASS isolated client and server packages
NOT CHECKED image inventory: no image inventory exists
PASS asset licenses
no leaks found
```

Command: `uv run --locked --python 3.14.2 pytest tests/ -q` in an isolated temporary environment.
Result: `148 passed`. The same command with the CI version, Python `3.14.3`, reports `148 passed`.

Command: `unshare -rn sh -c 'UV_LINK_MODE=copy uv run --offline --no-sync pytest tests/ -q'`.
Result: `148 passed` with the network namespace disabled.
Date: 2026-10-24.

The ASD-STE100 checker reports 0 findings in each of the four changed Markdown files.
The public-file self-check reads 50 files, with 0 private terms, 0 home paths, and 0 private addresses.
The public-tree check with `--history` reports `PASS own Git root`, `PASS history secrets commits=6`, and `PASS public data matches=0`.
Client and server package metadata, the lockfile, and CI stay unchanged.
Date: 2026-10-24.

Workspace analysis reports 31 unresolved imports and 0 warnings.
Its workspace root is not the repository root.
Configured Mypy and pytest resolve those modules. No import or type exemption is in use.

## Component records and checks (2026-10-25)

Date: 2026-10-25.
The sections above keep the 2026-10-24 commands and results without changes.
Their historical commands and policies do not give the current repository state.
Use the commands and rules in this section for the current repository.

### Current source-record contract

Decision statuses use `included` and `not_selected`.
Each `not_selected` record must have a `reason` code.
The consent option is `--allow-not-selected`. There are no aliases for previous status or flag names.

The release gate checks records, not permission for commercial use.
GPL and non-commercial components can have an `included` decision.
Missing quotations, file bindings, or attribution notices stay release errors.
The measured TTS candidates use `reason: technical`.

The `license` field accepts known SPDX identifiers, `NOASSERTION`, or a specified custom term.
An expression can connect known identifiers with `AND` or `OR`.
The field does not accept arbitrary custom identifiers.
`NOASSERTION` records no file-level grant without an invented license name.
`LicenseRef-Blizzard-2013-Research` is a descriptive `dataset_license` field.
It is not the model's `license` value.

A custom identifier must have an entry in `CUSTOM_LICENSE_TERMS`.
Its source `kind` must equal that identifier.
The SHA-256 of its `quote` must equal the stored term digest.
The only current custom entry is `LicenseRef-OpenRAIL-M` for Supertonic weights.
Its pinned license source has the actual grant and points to the use restrictions.
An invented MIT name or a quotation with no grant does not satisfy that rule.

Each source must have `kind`, `url`, `quote`, and `revision_or_version`.
Each source must also have a `file_binding` or a non-empty `scope`.
An included record must have a `license_text`, `model_card`, or `owner_statement` source with the asset's SHA-256 in `file_binding`.
The gate does not accept a badge URL as the file-level source.

An `owner_statement` uses a specified issue-comment URL and names the selected model release.
A dataset-license snapshot uses the captured HTML SHA-256 and a dataset-only scope.
Its URL is not immutable, and its terms are not a model-weight grant.

Included attribution must occur in `THIRD_PARTY_NOTICES.md`.
Not-selected assets must not be in the release tree, runtime inputs, image inventory, or download plan.
Image inventory stays `NOT CHECKED` when there is no image inventory.

### Planned model files

The download plan has 10 explicit entries. Each entry has `id`, `url`, `sha256`, and `path`.
The URL and digest must agree with the selected asset record.
The target must be a declared relative artifact path. Duplicate entry IDs are errors.
The plan can be inline or in a JSON file below the release root.

Each included model, voice, and tokenizer file not in the image must have a pinned plan entry.
An ID alone is not sufficient for those files. There is no implicit model download list.
Image-inventory IDs show image contents for this gate.
The asset record and its license sources must also have the same file digest.

The plan has these model-file targets:

- Smart Turn: `models/smart-turn-v3.2-cpu.onnx`.
- Silero: `models/silero_vad.onnx`.
- Piper voice: `models/piper/en_US-lessac-medium.onnx`.
- Piper configuration: `models/piper/en_US-lessac-medium.onnx.json`.

Silero uses its primary GitHub source at commit `5cd7945676eb32225748052e2e6a0580e4686a08`.
Its SHA-256 is `1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3`.
The Piper source URLs use the pinned voice repository commit `5041a7a49ae5b3a0f30e6837769eb03fdbb231bd`.
The plan records each voice-file digest, not a package-level digest.
Runtime wheel entries also have explicit URLs, digests, and targets.
The bundled Piper bridge and data use the same wheel URL, digest, and target as the runtime.

### Selected components

The repository owner answered the Silero file-scoped question with this text:

```text
MIT
```

Command: `gh api repos/snakers4/silero-vad/issues/comments/6393620007`.
Result: `author_association: OWNER`, `created_at: 2026-10-25T04:51:19Z`, and `body: MIT`.
The issue closed as completed at `2026-10-25T04:51:32Z`.
Source: https://github.com/snakers4/silero-vad/issues/816#issuecomment-6393620007
The source entry names `v6.2.3` and gives the selected model SHA-256.
The earlier MIT and badge statements stay in `license_sources`.

Piper 1.8.0 is the selected local TTS, with `en_US-lessac-medium` and four intra-op `threads`.
The runtime package declares GPL-3.0-or-later and Python `>=3.9`.
Source: https://pypi.org/pypi/piper-tts/1.8.0/json
The Lessac `model card` supplies a dataset-license URL, not a model-weight grant.
The weights and configuration records use `NOASSERTION`.
The custom dataset terms and their captured HTML digest stay explicit.
This record does not change the test-audio license rule or give publication approval.

### Record failure tests

The tests ran before the record-rule changes.

Command: `UV_LINK_MODE=copy uv run --locked pytest tests/test_asset_gate.py tests/test_probe_tts.py -q`.
Result: `23 failed, 72 passed`.

The GPL and non-commercial acceptance tests reported this error:

```text
AssertionError: assert ['test-voice: license does not permit this Apache image policy'] == []
```

The missing-notice test reported `AssertionError: assert False`.
The owner-comment test reported `license source immutable revision or version required`.
The renderer test found Python dictionary syntax in a public report.
The new TTS tests reported `ModuleNotFoundError: No module named 'tools.probe_tts'`.
The `quote` and `file_binding` checks gave `PASS` before these changes.
The new tests keep those checks for GPL and non-commercial records.

Command: `uv run --locked pytest tests/test_asset_gate.py -q -k 'unrecognized_license_reference or noassertion_records'`.
Result before the identifier check changed: `3 failed, 1 passed, 82 deselected`.
The three unrecognized-reference tests reported `AssertionError: assert 0 == 1` and printed `PASS asset licenses`.
The `NOASSERTION` test gave `PASS`.
The repaired gate rejects the unrecognized identifiers with a named error.

The pinned-download tests ran before their implementation.
Command: `uv run --locked pytest tests/test_asset_gate.py -q -k 'model_outside_image or image_model or download_entry or pinned_model_download or unrecognized_license_reference'`.
Result: `16 failed, 4 passed, 83 deselected`.
These tests found missing explicit file entries, incorrect URL/hash/target bindings, duplicate IDs, and an unrecognized non-SPDX identifier.

The custom-term test also ran before its implementation.
Command: `uv run --locked pytest tests/test_asset_gate.py -q -k registered_custom_license`.
Result: `1 failed, 3 passed, 103 deselected`.
The correct term must have the named source `kind` and its grant quotation.
Negative source mutations stay errors.

Command: `uv run --locked python $PROBE/mutations.py`.
Result: `PASS mutation evidence: 12 compiled mutants killed`.
Each mutant has correct Python syntax. Its behavioral test gave exit 1.
The mutation names are `license-reference`, `license-policy`, `quote`, `binding`, `notice`, `reason`, and `owner-binding`.
The other names are `custom-term-kind`, `custom-term-quote`, `model-download-required`, `model-download-binding`, and `model-download-duplicates`.

### Offline TTS metrics

Set `ASSIST_LIVE_PROBE_DIRECTORY` to temporary storage external to the repository.
Set `UV_LINK_MODE=copy`. `$PROBE` below means that temporary storage.
The metric inputs stay external to the repository. No synthesis ran for these checks.

```sh
uv run --locked python tools/probe_tts.py --timings $PROBE/timings.csv --transcripts $PROBE/transcripts.csv --scores $PROBE/mos-scores.csv --record-decision --report docs/research/q7t-local-tts.md
```

```text
PASS offline TTS metrics
PASS TTS selection recorded
PASS TTS records
```

The metrics use pooled word edits and audio time, plus linear interpolation for the naturalness 10th percentile.
The probe does not include warm-up transcripts. It keeps engine, `thread`, and input-length groups apart.
The live path must have `--live`, an installed Piper 1.8.0 runtime, and local files with the recorded hashes.
It downloads no model and installs no package.

### Current probe and release checks

The Smart Turn command uses the saved 100-candidate measurement and its initial model digest.
No Smart Turn model download or new cost measurement ran.

The three rights probes can render all 28 asset and component records.
The Smart Turn measurement values stay unchanged.
The earlier report diff also removed the project-page title sentence.
The current report has that sentence and readable file bindings.

```sh
uv run --locked pytest tests/test_probe_smart_turn.py -q
uv run --locked python tools/probe_smart_turn.py --record-decision --reuse-measurements $PROBE/smart-measurements.json --candidates 100 --report docs/research/q5-smart-turn.md
uv run --locked pytest tests/test_kokoro_licenses.py -q
uv run --locked python tools/probe_kokoro_licenses.py --record-decision --allow-not-selected --check assets/manifest.json --report docs/research/q7-kokoro.md
uv run --locked pytest tests/test_silero_license.py -q
uv run --locked python tools/probe_silero_license.py --record-decision --check assets/manifest.json --report docs/research/q8-silero.md
uv run --locked python tools/probe_tts.py --record-decision --report docs/research/q7t-local-tts.md
uv run --locked python tools/check_assets.py --manifest assets/manifest.json --release
```

Results: the three rights probes report `PASS decision recorded`.
Smart Turn tests report `15 passed`. Kokoro tests report `21 passed`. Silero tests report `31 passed`.

Smart Turn and Silero report `PASS included rights`.
Kokoro reports `PASS not-selected asset absent`.
The TTS probe reports `PASS TTS selection recorded` and `PASS TTS records`.
The asset gate reports `NOT CHECKED image inventory: no image inventory exists` and `PASS asset licenses`.

Command: `task check`.

```text
32 files already formatted
All checks passed!
PASS CI boundaries
Success: no issues found in 10 source files
205 passed
PASS server bootstrap
PASS isolated client and server packages
NOT CHECKED image inventory: no image inventory exists
PASS asset licenses
no leaks found
```

Command: `UV_PROJECT_ENVIRONMENT=$PROBE/venv3142 uv run --locked --python 3.14.2 pytest tests/ -q`.
Result: `205 passed`. CI-version Python 3.14.3 also reports `205 passed` through `task check`.

Command: `unshare -rn sh -c 'UV_LINK_MODE=copy uv run --offline --no-sync pytest tests/ -q'`.
Result: `205 passed` without network access.

The last analysis snapshot reports 0 errors and 0 warnings across 11 Python files.
The ASD-STE100 checker reports 0 findings for each of the six changed Markdown files.
The public-file self-check reads 53 files, with 0 private terms, 0 home paths, and 0 private addresses.
The public-tree check with `--history` reports `PASS own Git root`, `PASS history secrets commits=8`, and `PASS public data matches=0`.
Package metadata, `uv.lock`, and CI stay unchanged. This change adds no TTS runtime dependency.

The filesystem check examines all 53 publishable files.
It finds 0 current retired-decision words, with 18 occurrences in the historical record.
The full historical prefix agrees byte-for-byte with `04372a7`.
There is one new dated section, with the current commands, counts, and rules.

## Protocol and authenticated gateways

Date: 2026-10-25 (UTC).

Initial command: `uv run --locked pytest tests/contract -q`.
The initial result included `ModuleNotFoundError: No module named 'assist_live_server.auth'`.
More RED tests reported a missing replay-store argument and a missing replay-window constant.
The arrival-time test reported `AttributeError: 'DeviceEvent' object has no attribute 'arrival_ns'` before implementation.

Command: `uv run --locked pytest tests/contract -q`.
Result: `25 passed`.
The two timing adapters use the same tag, mapping, replay, restart, and size tests.
The service tests use Unix sockets and synthetic PCM bytes.

Command: `task check`.

```text
42 files already formatted
All checks passed!
PASS CI boundaries
Success: no issues found in 16 source files
174 passed
PASS server bootstrap
PASS isolated client and server packages
NOT CHECKED image inventory: no image inventory exists
PASS asset licenses
no leaks found
```

Command: `uv run --locked --python 3.14.2 pytest tests/ -q` in an isolated temporary environment.
Result: `174 passed`.
The complete gate uses the CI version, Python `3.14.3`.

Command: `unshare -rn .venv/bin/python -m pytest tests/ -q`.
Result: `174 passed` with the network namespace disabled.

The numbered mutation tests change one guard at a time in external source copies.
Each copy has a satisfactory Python parse check before pytest runs.
Result: `22/22 behavioral mutants killed`.
The guards include signed fields, byte order, duplicates, reordered events, previous boots, message limits, credentials, satellite access, queues, and heartbeats.
They also include transcript policy, reboot limits, long-running traffic, stale classes, bitmap size, replay persistence failures, and window boundaries.
The unmodified long-run test accepts 200,000 sequential events with a bounded bitmap.

The ASD-STE100 checker reports 0 findings in `docs/protocol.md`, `STRUCTURE.md`, and this record.
The public-tree check with `--history` reports:

```text
PASS own Git root
PASS history secrets commits=7
PASS public data matches=0
```

The file self-check reports 0 private terms, 0 home paths, and 0 private addresses.
Workspace diagnostics are unavailable because the analyzer does not recognize the isolated worktree root.
Configured Mypy and pytest supply the type and execution checks.

Replay persistence and device-clock conversion have interfaces but no production store in this module.
The timing store must supply durable replay hooks before the service accepts device traffic.
The server's client-version pin must equal the client package version in release configuration.
The device trial did not run because no test device is available.
Transport selection stays open.

## Clock conversion and SQLite records

Date: 2026-10-25 (UTC).

Initial command: `uv run --locked pytest tests/test_clock.py tests/test_store.py tests/test_pending_events.py -q`.
The initial result included `ModuleNotFoundError: No module named 'assist_live_server.clock'` and missing store modules.
The complete-turn test also reported `KeyError: 'events'` before the query supplied stored events.

Command: `uv run --locked pytest tests/test_clock.py tests/test_store.py tests/test_pending_events.py tests/contract -q`.
Result: `55 passed`.
The output includes `PASS event-to-store-to-client`, `PASS identity isolation`, and `PASS transcript opt-in`.
The tests check clock units, window limits, boot changes, pending events, expiry, retention, restart, and cursor order.
The service tests use the configured UDP listener and the WebSocket path.
Byte checks examine SQLite files, WAL files, exports, diagnostics, and logs with synthetic PCM markers.

Command: `task check`.

```text
51 files already formatted
All checks passed!
PASS CI boundaries
Success: no issues found in 21 source files
204 passed
PASS server bootstrap
PASS isolated client and server packages
NOT CHECKED image inventory: no image inventory exists
PASS asset licenses
no leaks found
```

Command: `UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT="$FLOOR_ENV" uv run --locked --python 3.14.2 pytest tests/ -q`.
Result: `204 passed`.

Command: `UV_OFFLINE=1 unshare -rn sh -c 'ip link set lo up && .venv/bin/python -m pytest tests/ -q'`.
Result: `204 passed`.
The temporary network namespace has loopback only. The package test uses cached build dependencies without external network access.
An earlier run without `UV_OFFLINE=1` could not refresh the cached `hatchling` index.

A synthetic trial sends 10,000 signed events in groups of 64, with a concurrent 100 ms heartbeat.
The server accepts all events. The queue has no drops in this trial.
The SQLite writer uses WAL with `synchronous=NORMAL` and owns all checkpoints.

| Measurement | Samples | p50 ms | p99 ms | Maximum ms |
|---|---:|---:|---:|---:|
| Event-loop lag, 1 ms probe | 11,445 | 0.418762 | 1.061935 | 2.012874 |
| Heartbeat lag, 100 ms interval | 115 | 0.463090 | 1.056299 | 1.060818 |
| Arrival to transaction commit | 10,000 | 20.291497 | 163.966180 | 223.147937 |

The event-loop p99 is below the 10 ms test target. These values are from a synthetic local trial, not a device performance test.
The replay update and device event share one transaction. A unique device, boot, and sequence key supplies a second duplicate check.
An earlier synchronous FULL-write trial had a p99 of 69.886903 ms and a maximum of 456.996348 ms on the event loop.
The dedicated SQLite writer keeps such storage waits off the audio event loop.

Mutation result: `15/15 behavioral mutants killed` after Python parse checks.
The changes test minimum offset, units, satellite access, expiry, transcript defaults, replay restart, arrival time, writer count, and duplicate keys.
They also test the two clock-window limits, boot changes, and playback quality.
The last two changes test backend validation before storage and refusal of queued events after credential revocation.
The writer-count test checks concurrent acceptance. The different unique-key test checks duplicate commits.

The ASD-STE100 checker reports 0 findings in `docs/protocol.md`, `STRUCTURE.md`, and this record.
The public-tree check with `--history` reports:

```text
PASS own Git root
PASS history secrets commits=9
PASS public data matches=0
```

The file self-check reports 0 private terms, 0 home paths, and 0 private addresses.
Workspace diagnostics cannot identify the isolated worktree root. Configured Mypy and pytest supply the type and execution checks.
Device timing and transport selection stay open because no test device is available.

## Device gateway limits and credential checks

Date: 2026-10-25 (UTC).

Initial command: `uv run --locked pytest tests/contract -q`.
Result before the corrections: `12 failed, 49 passed`.
The failures include missing HMAC work, identity-only device mapping, credential-write order, connection expiry, text deadlines, JSON depth, and HTTP errors.

Command: `uv run --locked pytest tests/test_clock.py tests/test_store.py tests/test_pending_events.py tests/contract -q`.
Result: `89 passed`.
The output includes `PASS event-to-store-to-client`, `PASS identity isolation`, and `PASS transcript opt-in`.
New tests also check a mismatched correct UUIDv7, correctly signed oversized events on the two adapters, and credentials on all integration routes.

Command: `task check`.

```text
51 files already formatted
All checks passed!
PASS CI boundaries
Success: no issues found in 21 source files
238 passed
PASS server bootstrap
PASS isolated client and server packages
PASS asset licenses
no leaks found
```

The image inventory stays unchecked because there is no image inventory.

Command: `UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT="$FLOOR_ENV" uv run --locked --python 3.14.2 pytest tests/ -q`.
Result: `238 passed`.

Command: `UV_OFFLINE=1 unshare -rn sh -c 'ip link set lo up && .venv/bin/python -m pytest tests/ -q'`.
Result: `238 passed`.

The device route has a limit of 64 concurrent connections and a two-second first-event deadline.
Ping frames cannot extend that deadline. A peer has 100 ms to reply to a close frame before the transport closes.
The 150-connection test accepts 64, refuses 86, and checks expiry and slot recovery.
Its traced-memory peak increases by less than 32 MiB.

A client/server RSS probe uses two groups of 150 connections in one process.
Each group accepts 64, refuses 86, and has no open device connections after the deadline.
The maximum RSS increase is 4,792 KiB. This is a synthetic local trial, not a hardware capacity recommendation.

The unknown-key and wrong-tag paths each run HMAC and `hmac.compare_digest`.
A 10,000-pair trial gives median times of 11,841 ns for a known key and 11,821 ns for an unknown key.
The ratio is 1.001692. These measurements check for the previous HMAC omission, not a constant-time guarantee for the complete HTTP stack.

The new 10,000-event writer trial has no queue drops or replay refusals. SQLite uses WAL with `synchronous=NORMAL`.

| Measurement | Samples | p50 ms | p99 ms | Maximum ms |
|---|---:|---:|---:|---:|
| Event-loop lag, 1 ms probe | 9,067 | 0.375385 | 1.059113 | 1.976853 |
| Heartbeat lag, 100 ms interval | 91 | 0.354328 | 1.068698 | 1.068698 |
| Arrival to transaction commit | 10,000 | 18.230015 | 100.250800 | 255.152572 |

The event-loop p99 is below the 10 ms target.
The durable server-restart test refuses a duplicate and a previous boot before the next correct event.
Device routes without replay hooks refuse the WebSocket upgrade and a configured UDP listener.

Mutation results: `35/35 behavioral mutants killed` for the gateway checks, and `15/15 behavioral mutants killed` for the timing store.
All source copies parse before the test runs. The tests import the changed copy, not the installed source tree.
The ASD-STE100, secret, and public-tree checks examine the changed files. No hardware or cloud test ran.

## Local STT replay measurements

Date: 2026-10-25.

Command, with no benchmark module: `uv run --locked pytest tests/test_benchmark_stt.py -q`.

```text
E   ModuleNotFoundError: No module named 'tools.benchmark_stt'
1 error during collection
```

The development group adds `onnx-asr==0.12.0` and `sherpa-onnx==2.0.0`.
The lock includes `sherpa-onnx-core==2.0.0`.
The server and client runtime dependencies stay unchanged.

PyPI metadata declares `Requires-Python: >=3.10` for onnx-asr and `>=3.7` for sherpa-onnx.
The first package has a universal Python wheel. The second has CPython 3.14 Linux wheels.
No different interpreter is necessary for these models.

- Metadata: https://pypi.org/pypi/onnx-asr/0.12.0/json
- Metadata: https://pypi.org/pypi/sherpa-onnx/2.0.0/json
- Access date: 2026-10-25.

The model files and corpus audio stay in external directories.
The test uses the 250 human replay records, with 50 tuning and 200 held-out records.
The initial 500 ms streaming tail removes some Zipformer word endings.
The corrected test uses the upstream-example 660 ms tail.
The selection rule and its 1.0 percentage-point WER margin stay unchanged.
The report records the two tests and the remaining tail sensitivity.

Command, from the server root:

```sh
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 \
STT_MODEL_ROOT="$PROBE/run2-models" uv run python tools/benchmark_stt.py \
  --labels "$WORKSPACE/assist-live/tests/data/labels.jsonl" \
  --audio-root "$CORPUS_AUDIO" --selection-split tuning --gate-split held_out \
  --partials-output docs/reports/q6-partials.jsonl \
  --candidates parakeet,sherpa-zipformer,fastconformer \
  --report docs/research/q6-streaming-stt.md --check
```

```text
PASS asset licenses
PASS Q6 comparison: parakeet; no streaming default
```

The corrected tuning word-error counts are Parakeet 30/394, Zipformer 129/394, and FastConformer 79/394.
The held-out counts are 81/1551, 490/1551, and 254/1551.
The selection freeze occurs before all held-out scores.
The paired bootstrap uses 5000 resamples and seed `20261025` for information only.
The report gives first-word counts by source onset time.

Intent results must come from the HA harness. They do not come from transcript scores.

The two streaming models decode their upstream WAVs with 0/18 and 1/48 word errors each.
Their first words have 2/2 preservation. The one error is British versus American spelling.
Five tuning rows show the same normalization rules for all candidates.
The model-file hashes, input sample rate, padding, flush, and endpoint settings have explicit checks.

Command: `uv run --locked pytest tests/test_benchmark_stt.py -q`.
Result: `17 passed`.

The numbered semantic mutations each cause the applicable test to give a failure:

1. Accept a held-out selection row.
2. Accept an audio SHA-256 mismatch.
3. Export decreasing `audio_ms` timestamps.
4. Remove the first word from the WER corpus transcript.
5. Replace the frozen 660 ms tail with 500 ms.

The transcript consumer checker accepts 250 rows, with 50 tuning and 200 held-out records.
It checks the frozen identity, field contract, monotonic timestamps, and source-audio length limits.

Command: `uv run --locked python tools/check_assets.py --manifest assets/manifest.json --release`.

```text
NOT CHECKED image inventory: no image inventory exists
PASS asset licenses
```

Command: `UV_LINK_MODE=copy task check`.

```text
35 files already formatted
All checks passed!
PASS CI boundaries
Success: no issues found in 11 source files
222 passed
PASS server bootstrap
PASS isolated client and server packages
NOT CHECKED image inventory: no image inventory exists
PASS asset licenses
no leaks found
```

Command: `UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=$PROBE/venv3142 uv run --locked --python 3.14.2 pytest tests/ -q`.
Result: `222 passed`. The CI interpreter, Python 3.14.3, also reports `222 passed` through `task check`.

The current record does not claim device acceptance, pause-set acceptance, or an image inventory result.

The ASD-STE100 checker reports 0 findings in each of the three changed Markdown files.
The publishable-file privacy self-check and the public-tree checker examine the worktree.
The public-tree check with history gives:

```text
PASS own Git root
PASS history secrets commits=11
PASS public data matches=0
```

## STT report generation and runtime parameters

Date: 2026-10-25.

The tool is the only source of `docs/research/q6-streaming-stt.md`.
The manifest stores historical measurement summaries, audit counts, and source-cache hashes.
The renderer includes the initial 500 ms test and the corrected 660 ms test.
The complete command keeps the disclosure headings, initial counts, and audit results.
It does not read a prior Markdown report to make those sections.

The new CLI tests first give these results:

```text
7 failed, 18 passed
AssertionError: '## Initial test and corrected tail' missing from generated report
AssertionError: split command changed complete export bytes
IndexError: list index out of range
ImportError: cannot import name 'recognition_metrics'
AssertionError: model-file change did not change provider_config_sha256
AssertionError: inert --check option was accepted
```

The hash uses the runtime parameters that the decoder uses:
the audio step, tail, sample rate, sample scale, `threads`, and model-file pins.
The hash does not include `decode_mode` description text.
No input audio, model file, selection rule, or WER margin changes.
The replay keeps Parakeet as the selected provider.

The second latency column counts the first transcript with the corpus first word.
It uses normalized word tokens and source-audio time.
It does not change the frozen selection rule.
The report states that full-buffer output before voice onset usually contains filler text.
The provider must gate full-buffer decoding on `VAD speech onset` in a future change.

Commands with `--split` write external measurement caches only.
They do not write the public report or the complete 250-row export.
The CLI does not accept `--check`. That option had no behavior.
The historical command records above keep that spelling.
The current command below does not use it.

Use an external directory for this STT experiment.
`PROBE` names that directory. `CORPUS_AUDIO` names its fetched-WAV directory.
The model-root directory must have no prior `selection.json`.
Set these paths before the command:

```sh
PROBE="$HOME/.cache/assist-live/stt-benchmark"
CORPUS_AUDIO="$PROBE/audio"
STT_MODEL_ROOT="$PROBE/repair-models"
```

Command, from the server root:

```sh
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 \
STT_MODEL_ROOT="$STT_MODEL_ROOT" uv run python tools/benchmark_stt.py \
  --labels "$WORKSPACE/assist-live/tests/data/labels.jsonl" \
  --audio-root "$CORPUS_AUDIO" --selection-split tuning --gate-split held_out \
  --partials-output docs/reports/q6-partials.jsonl \
  --candidates parakeet,sherpa-zipformer,fastconformer \
  --report docs/research/q6-streaming-stt.md
```

```text
PASS asset licenses
PASS Q6 comparison: parakeet; no streaming default
PASS all 750 final counts and partial sequences reproduce corrected run
PASS full report equals renderer output; disclosures and historical counts retained
```

The WER and first-word counts agree with the earlier corrected test.
The new provider configuration digest is
`b4ede9e1a2e15e6dafd8e40680691d40760ad11904a9496edc2afe653a331eab`.
All 250 selected-provider rows have that digest.

Command, with the transcript consumer checkout as a read-only input:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$TURN_CHECKOUT/server" \
uv run --locked python "$TURN_CHECKOUT/tools/tune_profiles.py" \
  --validate-partials --labels "$WORKSPACE/assist-live/tests/data/labels.jsonl" \
  --partials docs/reports/q6-partials.jsonl \
  --stt-model-id nvidia/parakeet-tdt-0.6b-v2 \
  --stt-revision 0bbb45a3365852604aef28b538a8f066f4ccaa85 \
  --provider-config-sha256 b4ede9e1a2e15e6dafd8e40680691d40760ad11904a9496edc2afe653a331eab
```

```text
PASS partial trajectories rows=250 tuning=50 held_out=200
```

The isolated semantic mutations each give a test failure:

1. Change the manifest tail from 660 ms to 500 ms.
2. Replace the configured decode step with hardcoded intervals.
3. Replace the runtime tail with 500 ms.

Command: `uv run --locked pytest tests/test_benchmark_stt.py -q`.
Result: `27 passed`.

The copy-of-tree CLI test runs the documented complete command with synthetic model fixtures.
It checks all disclosure headings, the initial counts, the added latency column, and the provider follow-up.
The split-mode tests check that the public export bytes do not change.
The empty-reference test checks a named `FAIL Q6 comparison` line without a traceback.
The manifest-tail and runtime-step mutations each cause their named tests to give a failure.

The runtime consumes keys from its preprocessor dictionary.
A dictionary copy keeps the canonical configuration unchanged.
A regression test first gives a failure with `{}` for those parameters.
The initialized decoder's configuration hash agrees with the recorded digest after this repair.

Command: `UV_LINK_MODE=copy task check`.

```text
35 files already formatted
All checks passed!
PASS CI boundaries
Success: no issues found in 11 source files
232 passed
PASS server bootstrap
PASS isolated client and server packages
NOT CHECKED image inventory: no image inventory exists
PASS asset licenses
no leaks found
```

Command: `UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=$PROBE/venv3142 uv run --locked --python 3.14.2 pytest tests/ -q`.
Result: `232 passed`.

The ASD-STE100 checker reports 0 findings in each changed Markdown file.
The public-tree checker with history gives these results:

```text
PASS own Git root
PASS history secrets commits=17
PASS public data matches=0
```

## Store resilience and record integrity

Date: 2026-10-25.

Acceptance errors refuse the current event, resolve its result, count the error, and log no event data.
The acceptance and maintenance tasks continue with intervals that have a five-second maximum.
Device result waits have a two-second limit.
Shutdown counts queued and in-flight discards and resolves their results.

The audio and subscription routes share 64 connection slots, with a limit of 16 for each integration token.
The device route keeps its 64-slot limit and a four-slot limit for each authenticated device.
A device connection cannot change its authenticated identity.

Credential changes must use the store and its bound registry.
The protocol gives the recovery steps for a process crash between the credential-file write and the SQLite commit.
`Registry.rotate` cannot clear the replay window.

### Behavioral checks

Command before the implementation:

```sh
uv run --locked pytest tests/contract/test_event_store.py \
  tests/contract/test_connection_limits.py -q \
  -k 'worker_store_error or maintenance_recovers or shutdown_counts or result_wait or share_global or reserves_slots'
```

Result: `6 failed, 18 deselected`.
The failures include `TimeoutError`, unresolved results, and `DID NOT RAISE WSServerHandshakeError`.

The query-plan test first gave `SCAN events` for the retention delete.
The direct-rotation test first gave `DID NOT RAISE ValueError`.
All 11 historical-evidence changes first gave exit code 0. The necessary exit code is 1.
The stored canonical SHA-256 checks the complete `stt_benchmark_evidence`, not selected report strings.

Command: `uv run --locked pytest tests/test_store.py tests/test_probe_tts.py tests/test_benchmark_stt.py tests/contract/ -q`.
The full test suite also runs these checks.
The mutation checks remove task guards, result resolution, indexes, connection limits, credential-change guards, and record protection.
Each changed guard must cause a test failure. The historical-evidence test checks all 11 reported summary changes.

### Writer measurements

The timing probe seeds 10,000 rows, then accepts 10,000 device events through the SQLite writer.
A different run seeds 100,000 rows and accepts 10,000 events.
The two runs include the 100 ms maintenance task and a 5 ms event-loop timer.
Times include the writer queue wait and commit. Other processes share the host.

`PROBE` names the external directory with the timing probe and its output.
Command for each source tree: `uv run --locked python "$PROBE/bench.py" "$PROBE/output"`.

| Seeded rows | Commit p50 before / after ms | Commit p99 before / after ms | Loop-lag p99 before / after ms |
|---:|---:|---:|---:|
| 10,000 | 0.922 / 0.186 | 40.730 / 0.757 | 1.060 / 1.103 |
| 100,000 | 4.039 / 0.181 | 58.402 / 0.550 | 1.072 / 0.851 |

The 10,000-row loop-lag difference is 0.043 ms. The 100,000-row result decreases.
These measurements do not give a target-host performance guarantee.
Retention runs each minute, not in the acceptance transaction. Status updates check each turn's deadlines.
The query-plan checks use range searches and an expression-index search for unmatched-event counts.

### Probe records

Command:

```sh
uv run --locked python tools/probe_tts.py --record-decision \
  --report docs/research/q7t-local-tts.md
```

Results: `PASS TTS selection recorded` and `PASS TTS records`.
The record is byte-identical. A copied-tree test compares the complete file after this command.
The `Dataset` and `Training` sections come from the manifest's model-card text.
The renderer keeps those sections. A mutation that removes the source text causes a copied-tree test failure.

The documented STT command also ran with the pinned model files and corpus audio in external storage.
The command used the same arguments as the complete-comparison command above, in a copied tree.
Result: `PASS Q6 comparison: parakeet; no streaming default`.
The record is different only in host load, uptime, and the first-partial and `final_latency_ms` columns.
The selected trajectory export is byte-identical, with 250 rows.

### Repository checks

Command: `task check`.
Results: `All checks passed!`, `PASS CI boundaries`, `Success: no issues found in 23 source files`, and `347 passed`.
The bootstrap, isolated-package, asset-license, and secret-scan checks give exit code 0.
Image inventory stays `NOT CHECKED`: there is no image inventory.

Command: `UV_LINK_MODE=copy uv run --locked --python 3.14.2 pytest -q`.
Result: `347 passed`.

Command: `unshare -rn sh -c 'ip link set lo up && UV_OFFLINE=1 uv run --locked pytest -q'`.
Result: `347 passed`.

The public-tree checker with `--history` reports:

```text
PASS own Git root
PASS history secrets commits=23
PASS public data matches=0
```

The ASD-STE100 checker reports 0 findings in the protocol and verification records.
The privacy self-scan checks 82 publishable files and gives `private_terms=0 home_paths=0 private_addresses=0`.
All 24 numbered behavioral mutations cause test failures. The 11 historical-evidence changes also give the necessary command failure.

## Guarded turn detection

Date: 2026-10-25 (UTC).

Initial command: `uv run --locked pytest tests/turn -q`.
Initial result: `ModuleNotFoundError: No module named 'assist_live_server.turn'`.

Command: `uv run --locked pytest tests/turn -q` with a prefilled external model cache.
Result: `125 passed`.
The tests use synthetic PCM and `vad_probability` and `turn_probability` inputs for policy checks.
The inference test uses the two pinned models. A missing cache is a named failure, not a skipped test.
Four NumPy `feature` tests agree with the locked Transformers probe to a tolerance of `2e-5`.

The two `Profile` objects have unmeasured defaults: `min_silence_ms=400`, `smart_turn_limit=0.8`, and `max_silence_ms=1500`.
The two settings keep the transcript guard and the 15 s hard endpoint.
Each detector instance processes one turn. Its events use the consumed audio clock.

The model loader checks SHA-256 before inference. It does not download files during inference.
The runtime reads the packaged asset manifest. Only the tuning tool accepts an external manifest override.

The server packages contain one copy of the asset manifest.
Tests compare its bytes with the source manifest in `direct wheels` and wheels from source archives.
The client wheel contains no server module or manifest.
NumPy `2.5.4` and ONNX Runtime `1.31.0` are server runtime dependencies.
The client does not have these dependencies. The server does not install PyTorch or the Silero Python package.

The `TRAJECTORY_SCHEMA` constant in `tools/tune_profiles.py` gives the partial-trajectory format.
Each row has a recording ID, `split`, audio hash, frozen STT identity, provider-configuration digest, and timestamped `partials`.
The corpus `labels` use `id`, `split`, `audio_sha256`, and an `audio_path` relative to the external audio root.
The tool reads the source type from `speaker_type`, or from the documented `speaker_id` prefix when `speaker_type` is missing.
The STT identity comes from the trajectory rows and must agree with the declared frozen identity, not with corpus-label fields.

Interval fields are not necessary for `set=replay` rows.
For rows with `set=pause`, `pause_intervals_ms` is necessary. For rows with `executable_prefix=true`, `executable_prefix_intervals_ms` is also necessary.
Each interval is a two-item array of start and end milliseconds. The time window does not include its end.

The corpus must add these two interval fields for those rows before pause and prefix tuning can run.
The named failures are `pause interval labels missing` and `prefix interval labels missing`.

The tool rejects unknown `split` values, mismatched hashes or revisions, missing recordings, duplicate recordings, and timestamps that decrease.
It checks actual WAV bytes and reads only `partials` with timestamps at or before the current audio time.

Tuning uses tuning rows only. For freeze, a minimum of 300 different held-out human pause recordings is necessary.
The frozen record checks the held-out recording identities and trajectories with SHA-256.
The held-out command does not tune or rewrite `Profile` values.

12 numbered behavioral mutations cause test failures in the review repair:

1. Disable the transcript guard in `fast`: the candidate test accepts `turn on the`.
2. Let `turn on the` cause an endpoint: the push test gets an endpoint at 416 ms.
3. Accept held-out rows for tuning: the partition test does not get the necessary error.
4. Accept freeze below 300 pause recordings: the denominator test does not get the necessary error.
5. Accept a model hash mismatch: the CI fetch test gets exit code 0, not 1.
6. Remove the maximum-silence endpoint: the fallback test gets no event.
7. Remove the endpoint at 15 s: the continuous-speech test gets no event.
8. Drop digits from the last token: the guard does not accept complete number endings.
9. Run Smart Turn each 32 ms frame: the inference count is more than seven.
10. Keep the previous turn's VAD state: the two-turn test disagrees with new runtimes.
11. Change the canonical privacy-checker URL: the CI assertion rejects it.
12. Remove `--history` from the CI privacy command: the shell test sees the missing argument.

The code contains no mutations after the tests. The repository has no frozen `Profile` file.

Command: `uv run --locked python tools/tune_profiles.py --fetch-models` with no manifest override.
Result: `PASS pinned turn models fetched with SHA-256 checks`.
The loader uses the landed download-plan objects: `id`, `url`, `sha256`, and `path`.
The acceptance status check reports `silero-vad=included smart-turn-v3.2-cpu=included`.
Files stay below the external cache path specified by the manifest, with symlink checks.

Command: `task check`, with the optional private denylist supplied and no manifest override.
Result: `455 passed`, `PASS server bootstrap`, `PASS isolated client and server packages`, and `PASS asset licenses`.
The directory and Git-history secret checks report `no leaks found`.
The earlier four asset/probe test failures do not occur. No asset check changed.

Command: `uv run --locked --python 3.14.2 pytest tests/ -q` in an isolated temporary environment.
Result: `455 passed`.
Command: `unshare -rn sh -c 'ip link set lo up && .venv/bin/python -m pytest tests/ -q'` with the cache prefilled.
Result: `455 passed`.

A concurrent floor/offline test showed a Hatchling index request in the isolated network.
The package tests use `uv build --offline` to use the build backend cached by workspace sync.
This prevents network requests, not source builds. Three concurrent floor/offline pairs then give satisfactory results.

Command: `uv run --locked mypy`.
Result: `Success: no issues found in 30 source files`.
Python workspace diagnostics are unavailable for the isolated worktree.

The public-tree check with `--history` reports `PASS history secrets` and `PASS public data matches=0`.
Its count can change with the reachable Git state. It is not a constant verification value.

CI runs unit tests on Python `3.14.3` and the declared floor, `3.14.2`.
Secret and public-tree tasks use a full checkout. CI checks the Gitleaks archive SHA-256 before installation.

The only privacy checker is `assist-live/tools/check_public_tree.py`.
CI downloads that file from the canonical repository's `main` branch into `RUNNER_TEMP`.
The download must succeed before the checker runs with Python 3.14 and `--history --root .`.

There is no second checkout or nested Git root.
The canonical file imports only the standard library. Its external commands are Git and Gitleaks, which CI supplies.

The local task uses `../assist-live/tools/check_public_tree.py` when that file is available.
If it is missing, the task prints `NOT CHECKED public tree`.
An explicit canonical check supplies the privacy result for isolated worktrees.
Without an external private denylist, the checker prints `NOT CHECKED private denylist` and continues the public-pattern checks.

The ASD-STE100 checker reports 0 findings in `STRUCTURE.md` and this record.

The selected STT export has a satisfactory `--validate-partials` result: `rows=250 tuning=50 held_out=200`.
The export path is `assist-live-server/docs/reports/q6-partials.jsonl`, relative to the workspace.
The corpus path is `assist-live/tests/data/labels.jsonl`.
The export file SHA-256 is `750c5aa56d8ec7ace237b8a5816f1f9a7fa3f9f7ef6a937ff4435813dc0ae186`.
The declared STT model is `nvidia/parakeet-tdt-0.6b-v2`, revision `0bbb45a3365852604aef28b538a8f066f4ccaa85`.
The declared provider-configuration SHA-256 is `b4ede9e1a2e15e6dafd8e40680691d40760ad11904a9496edc2afe653a331eab`.

The command checks corpus recording IDs, corpus audio hashes, `split` values, ordered timestamps, and declared frozen STT identity.
It does not check audio bytes in this metadata-only mode. The input export stays unchanged.

Human pause recordings and executable-prefix interval records are not available.
The 250 replay trajectories do not supply the 300-command held-out pause denominator.
The tuning and human safety acceptance steps stay pending. No freeze command ran.
The project is experimental and has no release.
Run the tuning and held-out commands only when the pause and prefix inputs are available:

```sh
uv run --locked python tools/tune_profiles.py --fetch-models
uv run --locked python tools/tune_profiles.py \
  --labels "$LABELS" --audio-root "$CORPUS_AUDIO" --partials "$PARTIALS" \
  --stt-model-id "$STT_MODEL_ID" --stt-revision "$STT_REVISION" \
  --provider-config-sha256 "$PROVIDER_CONFIG_SHA256" \
  --split tuning --profile safe --profile fast \
  --output docs/reports/p1-profiles.json --freeze
uv run --locked python tools/tune_profiles.py \
  --labels "$LABELS" --audio-root "$CORPUS_AUDIO" --partials "$PARTIALS" \
  --stt-model-id "$STT_MODEL_ID" --stt-revision "$STT_REVISION" \
  --provider-config-sha256 "$PROVIDER_CONFIG_SHA256" \
  --split held_out --profiles-file docs/reports/p1-profiles.json --check-held-out
```

If the safe prefix gate gives a failure, use the constant safety fallback without a new threshold search:

```sh
uv run --locked python tools/tune_profiles.py --disable-safe-early \
  --profiles-file docs/reports/p1-profiles.json --output "$SAFE_FALLBACK_PROFILES"
```

Then run the held-out command with that output as `--profiles-file`.
The two turn-model statuses are `included` in the landed manifest.

### Review repairs

The guard keeps digits in the last token. Number endings such as "set volume to 50" can end a command.

The mandatory word-class list causes a longer wait for complete particle endings such as "turn the lights on".
This is a known latency cost. Keep the word-class list unchanged before measurements.
The human pause and prefix measurements will quantify that cost. See `profiles.md`.

Smart Turn runs for a changed complete `partial`, or after 160 ms of new audio with an unchanged `partial`.
The low-score test counts seven inferences from 416 through 1376 ms, not one inference for each 32 ms frame.

The maximum-silence and hard endpoints do not wait for this throttle.
A new detector sets shared VAD state and context to their initial values. Runtime sharing is for sequential turns only.

Call `flush(partial)` at a stream stall or end to consume the received sub-block samples.
VAD padding does not change the audio clock. Empty flush calls do not add time without voice.
The replay tool calls flush after the last received samples.
Unknown `Profile` names and duplicate asset records give `ValueError`.
Model fetch refuses other CLI modes and inputs before side effects.

CI uses the same SHA-256-checked actionlint 1.7.12 archive and Task 3.51.1 as the sibling repositories.

## Local voice recognition

Date: 2026-10-25.

The provider uses `onnx-asr==0.12.0` as a server runtime dependency.
The client package has no model-runtime dependency.
The [PyPI metadata](https://pypi.org/pypi/onnx-asr/0.12.0/json) declares `requires_python: >=3.10` (accessed 2026-10-25).
The server floor stays Python `3.14.2`.

The initial provider test reported `ModuleNotFoundError` for the missing `providers.parakeet` and `providers.stt` modules.
The interval-label tests reported `Failed: DID NOT RAISE ValueError` in six conditions before the new checks.
The slow-decoder test reported `TimeoutError` because an audio push waited for inference.
The session accepts audio during inference and merges ticks into one pending update.
The slow-decoder test checks two snapshots: the first buffer and the newest complete buffer.
Thirteen concurrent callers share those two decodes without a queue for each tick.

The finish-priority test first reported decode order `1, 2, 3, 4`, not the necessary `1, 4, 2, 3`.
Finish work runs before queued `partial` work from other sessions, without interruption of an active decode.
The capacity test reported `completed work still consumes an admission slot` before the handoff change.
The executor releases capacity before it gives a result to the caller.

The image inventory is empty because all model, voice and runtime assets download at startup.
No container image is available at this time. The image build must compare the built image with this inventory.
The manifest contains 0 records with `download: included`, with component records in that count.
The asset checker and its tests stay unchanged.

Commands from the repository root:

```sh
uv run --locked pytest tests/providers -q
ASSIST_LIVE_MODEL_CACHE="$TURN_MODEL_CACHE" task check
UV_PROJECT_ENVIRONMENT="$FLOOR_ENV" uv run --locked --python 3.14.2 pytest tests/ -q
unshare -rn sh -c 'ip link set lo up && UV_OFFLINE=1 uv run --locked pytest -q'
uv lock --check
uv run --locked python tools/tune_profiles.py --validate-partials \
  --labels "$WORKSPACE/assist-live/tests/data/labels.jsonl" \
  --partials docs/reports/q6-partials.jsonl \
  --stt-model-id nvidia/parakeet-tdt-0.6b-v2 \
  --stt-revision 0bbb45a3365852604aef28b538a8f066f4ccaa85 \
  --provider-config-sha256 b4ede9e1a2e15e6dafd8e40680691d40760ad11904a9496edc2afe653a331eab
uv run --project "$WORKSPACE/assist-live" python \
  "$WORKSPACE/assist-live/tools/check_public_tree.py" --history --root . \
  --private-denylist "$PRIVATE_DENYLIST"
```

The provider suite reports `15 passed`.
The complete suite, floor suite, and network-blocked suite each report `505 passed`.
The complete check reports `All checks passed!`, `PASS CI boundaries`, and `Success: no issues found in 34 source files`.
It also reports `PASS server bootstrap`, `PASS isolated client and server packages`, and `PASS asset licenses`.
The lock check resolves 60 packages without a change.
The unchanged export reports `PASS partial trajectories rows=250 tuning=50 held_out=200`.

The worktree task reports `NOT CHECKED public tree` because the sibling checker is missing there.
The explicit canonical check reports `PASS own Git root`, `PASS history secrets`, and `PASS public data matches=0`.
The ASD-STE100 checker reports 0 findings in this record, `STRUCTURE.md`, and `docs/providers/stt.md`.

Nine valid-Python mutations each cause a behavioral test failure:

1. Remove the onset gate.
2. Remove the 15 s limit.
3. Put input chunks in opposite order.
4. Decode only the last interval at finish.
5. Remove the 400 ms pause minimum.
6. Remove the 800 ms pause maximum.
7. Remove the sorted, non-overlap interval check.
8. Discard the newest pending tick.
9. Remove finish-first scheduling.

The 250-recording replay used the pinned Silero VAD, 512-sample frames, and threshold 0.5.
The automatic corpus `alignment_words` field supplies the first-word time.
Nonempty results before that time fell from 319 to 12, across 229 to 12 recordings.
All 250 recordings had a VAD onset. The 12 remaining results follow a VAD signal before the aligned first-word time.
No held-out threshold changed.

The external cost test used one, two, and four concurrent sessions with a 100 ms heartbeat.
The 15 s test used 100 sessions for each of the one-session and four-session configurations.
With four 15 s sessions, interval wall-time p50/p99 was 570.39/926.56 ms, with queue time.
Finish wall-time p50/p99 was 901.48/968.36 ms. Heartbeat lag p99 was 1.82 ms.

The same four-session workload then ran again with finish-first scheduling.
Finish wall-time p50/p99 was 496.30/555.33 ms, with heartbeat lag p99 of 1.74 ms.
Interval wall-time p50/p99 rose to 844.61/1636.86 ms.
The second test had 100 finish calls and 498 heartbeat observations. Background host load was not constant between tests.
One-minute host load stayed below 32.

See [local voice recognition](providers/stt.md) for the two cost tables, worker-only times, source pins, and measurement limits.

The gateway connection to `speech_started()` is not in this provider change.
Human pause acceptance and the target-device baseline stay pending.
No container image, device trial, cloud call, model file, or audio file is part of this change.
The project is experimental and has no release.

## Runtime package record for onnx-asr

Date: 2026-10-25.

The local Parakeet provider made `onnx-asr==0.12.0` a server runtime dependency.
`THIRD_PARTY_NOTICES.md` records it as a runtime package.
The record gives the MIT license, the copyright line, and the source.
The earlier notice said that `onnx-asr` was a benchmark development dependency only.
That statement is incorrect after the provider change.

The section "Local STT replay measurements" above says that the server and client runtime dependencies stay unchanged.
That statement was correct at that date.
The section "Local voice recognition" supersedes it.
`sherpa-onnx==2.0.0` stays a benchmark development dependency only.

The package metadata of the installed wheel gives `License-Expression: MIT` and `License-File: LICENSE`.

## Streaming voice recognition gateway

Date: 2026-10-25 (UTC).

The streaming gateway selects one configured STT session before input audio.
Each turn has its own detector. The detector's VAD onset starts interval decode in the STT session.
The heartbeat has its own timer. Detection and STT decode run at the same time.

The server stops input at the detector endpoint, at 15 seconds of audio, or 15 seconds after input starts.
It flushes received samples at a stream stall and calls `finish()` after input stops.
The store receives onset, candidate, decision, `stt_partial`, `stt_final`, and speaker timing events.
The store keeps no text from `partial`. The two-setting policy controls `stt_final` text only.

The audio route had a boundary defect: aiohttp refused a correct 32,021-byte frame at the protocol maximum.
The route accepts that frame. A 32,022-byte frame closes with code `1009`.
The contract test checks the two boundaries.

The client stops its audio sender when it receives `final` or `end`.
A late `ClientConnectionResetError` from that sender does not replace the complete result.
Transport errors before `final` and other source errors propagate.
The Home Assistant integration gets this client change only after its client dependency pin changes.

Commands from the server repository:

```sh
uv run pytest tests/test_audio_gateway.py tests/contract/test_streaming_stt.py -q
task check
UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT="$CHECK_ENVIRONMENT" uv run --locked --python 3.14.2 pytest tests/ -q
unshare -rn sh -c 'ip link set lo up && UV_OFFLINE=1 uv run --locked pytest -q'
uv run python "$REPLAY_PROBE"
uv run python "$MUTATION_PROBE"
uv run --project "$WORKSPACE/assist-live" python "$WORKSPACE/assist-live/tools/check_public_tree.py" --history --root . --private-denylist "$PRIVATE_DENYLIST"
```

Set `ASSIST_LIVE_MODEL_CACHE` to the same external cache that `task unit` uses.
The RED result was `ModuleNotFoundError: No module named 'assist_live_server.audio_gateway'`.

The gateway tests gave `PASS`. The complete suite gave `PASS` on Python 3.14.3 and 3.14.2.
The suite also gave `PASS` without external network access.

The `task check` command gave `PASS` for Ruff, mypy, unit tests, bootstrap, the two packages, asset checks, and secret checks.
Its public-tree step reported `NOT CHECKED public tree` because the worktree has no sibling integration checkout.
The explicit canonical checker gave `PASS own Git root` and `PASS public data matches=0`.
The history secret check also gave `PASS`.

The external replay probe used the Parakeet provider and Silero VAD and Smart Turn models, not test doubles.
It sent 32 ms frames at real-time intervals, then added a maximum of 1.6 seconds without voice.
All five recordings sent `end_decision`, one `final`, and `speaker_result` before stream close.
The probe compared each `final.text` value with the provider's `finish()` result. All values stayed unchanged.

The five source WAV SHA-256 values were:

```text
00f7031f4ecbe360965e9012a7b2c7f713f1dc0315eee73b2eec418a57f20fa6
0136e4bde0e4e9511f0394cefd5b06c21f8534d17eac88dc87b3f9a100aa48dc
02cbc1043f5991259e71669d7bce7f5d5442d042c45909856064d1f9518b75ba
0311dcfa53376ffe166231f91e97d26e75fd52d3057be2988cd54d439c28ed91
042c1802259254dc52523c510fd34bcdafd622432bcd425de26c9f1aab0052a2
```

These recordings are from the external licensed STT replay corpus. No audio file is in this change.
This small probe does not measure recognition quality, human pause acceptance, or target-device capacity.

Eight isolated source mutations each gave an unsatisfactory result in its target test:

1. A heartbeat that waits for an audio frame.
2. Two complete results for one turn.
3. Work that continues after turn cancellation.
4. No forwarding of the VAD onset to STT.
5. No `finish()` call at the audio bound.
6. Acceptance after a non-SQLite store failure.
7. Refusal of the inclusive audio-frame maximum.
8. Continued source input after `final`.

The non-SQLite acceptance task refuses a store failure.
A typed `TurnExistsError`, not error text, selects HTTP `409` for concurrent opens of the same turn.
The `feature.yml` template tells users not to include secrets or household recordings.
The project is experimental and has no release. No device trial, cloud call, publication, or commit is part of these checks.

## Audio stream boundary checks

Date: 2026-10-25 (UTC).

The client and server accept the inclusive 32,021-byte audio-frame maximum.
The client TTS test also checks that a 32,022-byte frame closes with code `1009`.
The STT gateway imports its audio-byte bound from the provider. There is one definition.

The tests use `sqlite3.OperationalError` to stop `stt_partial`, `stt_final`, and `speaker_result` writes.
Before a `final` result, the server tries to write a `stt_failed` record and sends one error.
The error stays available if the server cannot write that record.
After a `final` result, a speaker-write failure adds no error message or terminal timing event.
The metrics endpoint counts that failure. A log entry contains no error text or transcript text.

A barrier test sends a late frame after the input deadline and before the endpoint record.
The receiver drops that frame. The stream completes with one `final` result.

The server refuses a second active stream for the same turn with code `1008` and `reason` `audio turn active`.
That stream writes no timing rows and does not release the first stream's slot.
Tests check the same slot after a complete result, provider error, malformed input, and disconnect.
Sequential streams follow the turn-existence and satellite-access rules. They can add different STT records to the same stored ID.

The device, audio, and subscription routes use one 100 ms close timeout.
Tests with peers that do not reply to close frames check slot release on all three routes.

Commands from the server repository:

```sh
uv run pytest tests/test_audio_gateway.py tests/contract/test_streaming_stt.py -q
task check
UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT="$CHECK_ENVIRONMENT" uv run --locked --python 3.14.2 pytest tests/ -q
unshare -rn sh -c 'ip link set lo up && UV_OFFLINE=1 uv run --locked pytest -q'
uv run python "$REPLAY_PROBE"
uv run python "$MUTATION_PROBE"
uv run --project "$WORKSPACE/assist-live" python "$WORKSPACE/assist-live/tools/check_public_tree.py" --history --root . --private-denylist "$PRIVATE_DENYLIST"
```

The new regression tests initially gave `15 failed, 2 passed, 22 deselected`.
The same tests then gave `17 passed, 22 deselected`.
The two initial satisfactory tests checked the oversized frame and the device close timeout.
Set `ASSIST_LIVE_MODEL_CACHE` to the external turn-model cache before these commands.

The complete checks gave these results:

```text
Gateway tests: 32 passed in 44.93s
Python 3.14.3: 545 passed in 96.29s
Python 3.14.2: 545 passed in 130.42s
No external network, loopback up: 545 passed in 129.12s
Ruff: All checks passed!
Mypy: Success: no issues found in 35 source files
PASS server bootstrap
PASS isolated client and server packages
PASS asset licenses
PASS own Git root
PASS public data matches=0
PASS real Parakeet and real detector: 5 replay clips
PASS finding mutations: 9/9 killed; sources parse; worktree unchanged
```

The history secret check and the two Gitleaks checks also gave `PASS`.
The relative public-tree step reported `NOT CHECKED public tree` because the worktree has no sibling integration checkout.
The explicit canonical checker supplied that check with history and the private denylist.

The replay used the same five source WAV digests in the streaming verification section above.
Each stream sent one `final`, followed by `speaker_result` and `end`. Its `final.text` equaled the provider's `finish()` result.
All five streams kept transcript text out of the timing store.

The nine mutations remove each boundary guard, the post-final error guard, slot release, or slot ownership.
Each changed source parsed before its target test gave an unsatisfactory result. No mutant changed the working source.
The project is experimental and has no release. No device trial, cloud call, publication, or commit is part of these checks.

## Sentence-level local voice synthesis (2026-10-25)

The local provider uses Piper 1.8.0, `en_US-lessac-medium`, native 22050 Hz mono int16 PCM, and four ONNX intra-op `threads`.
The voice and configuration came from the manifest download plan. Each file agreed with its pinned SHA-256 before loading.
No voice bytes or synthetic audio are in the repository.

Initial command:

```sh
uv run --locked pytest tests/providers/test_piper.py tests/contract/test_streaming_tts.py tests/test_ci.py -q
```

Initial result:

```text
ImportError: cannot import name 'TTSAudio' from 'assist_live_client.models'
```

The legacy-format refusal test also gave `Failed: DID NOT RAISE ProtocolError` before the guard was available.
The native-format schema and checker-digest tests also gave unsatisfactory results before their changes.

Commands and satisfactory results:

```sh
uv run --locked pytest tests/providers/test_piper.py -q
uv run --locked pytest tests/providers/test_piper.py tests/contract/test_streaming_tts.py tests/test_ci.py -q
ASSIST_LIVE_MODEL_CACHE="$TURN_MODEL_CACHE" task check
UV_PROJECT_ENVIRONMENT="$FLOOR_ENVIRONMENT" uv run --locked --python 3.14.2 pytest tests/ -q
unshare -rn sh -c 'ip link set lo up && UV_OFFLINE=1 uv run --locked pytest -q'
uv lock --check
uv run --locked python tools/check_assets.py --manifest assets/manifest.json --release
actionlint
uv run --project "$WORKSPACE/assist-live" python "$WORKSPACE/assist-live/tools/check_public_tree.py" --history --root . --private-denylist "$PRIVATE_DENYLIST"
```

Set `ASSIST_LIVE_MODEL_CACHE` to the external turn-model cache for each suite.
`TURN_MODEL_CACHE` names that cache. `FLOOR_ENVIRONMENT` names an external Python 3.14.2 environment.

```text
Provider tests: 15 passed
Provider, client contract, and CI tests: 42 passed
task check: 568 passed in 29.08s
Python 3.14.2: 568 passed in 37.62s
No external network, loopback up: 568 passed in 39.46s
Ruff: All checks passed!
Mypy: Success: no issues found in 37 source files
PASS CI boundaries
PASS server bootstrap
PASS isolated client and server packages
PASS asset licenses
Lock check: 62 packages, exit 0
actionlint: exit 0
PASS own Git root
History secret check: PASS
PASS public data matches=0
```

The relative public-tree step reports `NOT CHECKED public tree` because this worktree has no sibling integration checkout.
The explicit canonical checker supplied that check with history and the private denylist. Gitleaks found no leaks in files or history.

The HTTP probe and the latency probe used external scratch scripts:

```sh
uv run --locked python "$ASSIST_LIVE_TTS_PROBE"
uv run --locked python "$ASSIST_LIVE_TTS_MEASUREMENT"
```

The HTTP probe sent text in three parts, with 150 ms and 500 ms pauses.
It sent `3.` and `5 degrees. ` in different parts. The last fragment had no punctuation.
The client received the format at 2.48 ms, the first audio at 239.12 ms, and sentence `0` end at 239.27 ms.

The first sentence text completed at 153.57 ms. The client received the last text part at 654.24 ms.
Sentence `1` end was at 689.02 ms. All times use one monotonic clock relative to probe start.
Result: `PASS HTTP client → route → native Piper → typed items; audio before text end`.

The [local TTS provider record](providers/tts-local.md) gives the measured p50 and p99 latency for one and four TTS sessions.
It also gives the same configurations with four Parakeet sessions and p99 event-loop lag at a 100 ms heartbeat interval.
The four Parakeet sessions used the pinned models and decoded synthetic Piper audio at 16 kHz for the probe only.
The production path has no sample-rate conversion. The probe checked `intra_op_num_threads=4` on the loaded Piper session.

Twelve numbered mutations each made a target test give an unsatisfactory result:

1. Wait for complete text: first-audio test timed out.
2. Drop the last fragment: the sentence assertion did not agree.
3. Divide `3.5` at the decimal point: the sentence assertion did not agree.
4. Accept an unlisted voice: the necessary `ValueError` did not occur.
5. Remove the sentence-end control: route contract timed out before the sentence end.
6. Supply cancelled audio to the next session: the next session received incorrect bytes.
7. Change `tts()` from bytes to typed items: the legacy byte assertion did not agree.
8. Accept 22050 Hz through the legacy API: the necessary `ProtocolError` did not occur.
9. Bypass voice-file hashes: the necessary SHA-256 error did not occur.
10. Use the STT executor for TTS: a full STT queue refused the TTS request.
11. Accept a fifth inference: the necessary `TTSBusyError` did not occur.
12. Remove the downloaded checker digest: the CI output did not contain the known SHA-256.

Each Python mutation parsed and ran before its behavioral assertion gave an unsatisfactory result.
The four changed Python modules agreed with their pre-mutation snapshots after restoration. The 42 target tests then gave satisfactory results.

The installed wheel metadata and license files supplied the runtime package records in `THIRD_PARTY_NOTICES.md`.
Piper and pathvalidate each declare Python `>=3.9`. The workspace floor stays `>=3.14.2`.

Sources: https://pypi.org/pypi/piper-tts/1.8.0/json and https://pypi.org/pypi/pathvalidate/3.3.1/json, accessed 2026-10-25.
Piper declares `GPL-3.0-or-later`. pathvalidate declares `MIT License`.
The Piper wheel has no program copyright line. Its GPL text has a Free Software Foundation copyright line, which is not a Piper copyright statement.

The client dependency ranges did not change. The typed API accepts 22050 Hz and keeps sentence boundaries.
The bytes-only API refuses that rate before audio because a legacy consumer can use a constant 16 kHz WAV header.
Target-device measurements and a listening check stay open. No cloud call, device trial, publication, or commit is part of these checks.

## Incremental sentence assembly (2026-10-25)

Abbreviation-dense input made the assembler examine the complete prefix for each text part on the event loop.
The assembler keeps character, boundary, and token state across parts. It does not examine previous text again.
The abbreviation spelling has a five-character limit. A constant-state check identifies dotted initials without a token-length limit.

Opening straight or curly quotation marks and brackets do not change abbreviation lookup.
Decimal numbers, ellipsis, the last fragment, and the 16384-character limit keep their contracts.

Initial command:

```sh
uv run --locked pytest tests/providers/test_piper.py -k 'below_200 or scaling_stays or opening_quotes or curly_quoted' -q --tb=short
```

Initial results:

```text
9 failed, 19 deselected in 21.54s
Abbreviation-dense cap test: subprocess.TimeoutExpired after 10 seconds
4000/8000-character scaling test: subprocess.TimeoutExpired after 10 seconds
Seven quotation-mark and bracket cases: sentence assertions did not agree
```

Each performance test runs in a subprocess with a 10-second limit, so a slow assembler cannot stop the complete test suite indefinitely.
The maximum-input test asserts a time below 200 ms. The scaling test uses three samples at each length and asserts a median ratio below 3.
The heartbeat test asserts p99 loop lag below 20 ms, with a 100 ms heartbeat and 1 ms between streams.

Measured results on the development host, with a one-minute load of 24.70:

| Measurement | Result |
|---|---:|
| 16383 characters, one-character parts | 7.411 ms |
| 4000 characters, median | 1.806 ms |
| 8000 characters, median | 3.648 ms |
| Scaling ratio | 2.020 |
| Heartbeat p99 lag | 6.416 ms |
| Maximum stream time during heartbeat test | 9.990 ms |
| Complete streams during heartbeat test | 161 |
| Heartbeat samples in 1.2 seconds | 13 |

The equivalent boundary probe used 30 replies with three deterministic input sequences each.
All 90 sentence lists agreed with the lists captured before the change.
The two quoted-abbreviation examples and one bracket example each give one sentence.
A set of 10000 deterministic one-character input sequences agreed with the previous assembler on punctuation, whitespace, decimals, and initials.

Commands for the external probes:

```sh
uv run --locked python "$ASSEMBLY_BOUNDARY_PROBE" before
uv run --locked python "$ASSEMBLY_BOUNDARY_PROBE" after
```

Results:

```text
PASS captured 90 baseline feeds and 3 quoted cases
PASS 90/90 feed segmentations unchanged; 3/3 quoted cases corrected
PASS 10000 deterministic character-feed differential cases
Provider tests: 28 passed in 4.62s
Mypy: Success: no issues found in 37 source files
Ruff: All checks passed!
```

Gate commands:

```sh
uv run --locked pytest tests/providers/test_piper.py -q
ASSIST_LIVE_MODEL_CACHE="$TURN_MODEL_CACHE" task check
UV_PROJECT_ENVIRONMENT="$FLOOR_ENVIRONMENT" uv run --locked --python 3.14.2 pytest tests/ -q
unshare -rn sh -c 'ip link set lo up && UV_OFFLINE=1 uv run --locked pytest -q'
uv run --project "$WORKSPACE/assist-live" python "$WORKSPACE/assist-live/tools/check_public_tree.py" --history --root . --private-denylist "$PRIVATE_DENYLIST"
```

Set `ASSIST_LIVE_MODEL_CACHE` to the external turn-model cache for each suite.
Results:

```text
task check: 581 passed in 70.31s
Python 3.14.2: 581 passed in 46.45s
No external network, loopback up: 581 passed in 43.77s
PASS CI boundaries
PASS server bootstrap
PASS isolated client and server packages
PASS asset licenses
PASS own Git root
History secret check: PASS
PASS public data matches=0
```

The relative public-tree task reports `NOT CHECKED public tree` because the worktree has no sibling integration checkout.
The explicit canonical checker supplied the history and private-data check. Gitleaks found no leaks in files or history.
No runtime dependencies or public signatures changed. The project is experimental and has no release.

Two mutation checks tested the new guards:

1. Remove opening-delimiter normalization: all seven quotation-mark and bracket tests gave unsatisfactory sentence lists.
2. Examine the complete prefix again: the 16383-character test took 2324.13 ms, and the scaling ratio increased to 3.654.

The second mutation made the maximum-input and scaling assertions give unsatisfactory results, not only subprocess timeouts.
The parser agreed with its saved snapshot after restoration.

## Explicit cloud voice adapters

Date: 2026-10-25 (UTC).
The project is experimental and has no release.
The provider data-use and protocol sources are in [Cloud voice services](providers/cloud.md).

The initial command was:

```sh
uv run pytest tests/providers/test_deepgram.py tests/providers/test_elevenlabs.py -q
```

Initial result:

```text
ModuleNotFoundError: No module named 'assist_live_server.providers.deepgram'
ModuleNotFoundError: No module named 'assist_live_server.providers.elevenlabs'
2 errors in 0.17s
```

The adapters supply constructors without cloud factory entries.
Each connection sends credentials only in the documented authentication header.
Tests use fake WebSocket servers with the documented request and response fields.
The fake-server fixture refuses other TCP destinations and DNS queries.
Its teardown also finds refused connections when an adapter catches their errors.

The adapter tests examine reconnect opt-out, first audio before input end, text order, flush, cancellation, and credential protection.
Other tests examine redirect refusal, character times, PCM chunk limits, and cleanup after the last response.
These tests are protocol checks, not live latency measurements.

Live checks: `NOT RUN (needs operator credentials and spend approval)`.

### Cloud adapter repository checks

Date: 2026-10-26 (UTC).
The model cache uses `${XDG_CACHE_HOME:-$HOME/.cache}/assist-live/turn` through `ASSIST_LIVE_MODEL_CACHE`.
The environment also sets `UV_LINK_MODE=copy`.

Command:

```sh
uv run pytest tests/providers/test_deepgram.py tests/providers/test_elevenlabs.py -q && task check && uv run python tools/check_assets.py --manifest assets/manifest.json --release
```

Result:

```text
53 passed in 0.46s
93 files already formatted
All checks passed!
PASS CI boundaries
Success: no issues found in 39 source files
PASS pinned turn models fetched with SHA-256 checks
634 passed in 31.13s
PASS server bootstrap
PASS isolated client and server packages
PASS isolated client and server packages
PASS asset licenses
NOT CHECKED public tree
PASS asset licenses
```

Gitleaks found no leaks in files or the 37-commit history.
The relative public-tree task has no sibling integration checkout. The explicit canonical check below supplies this gate.

Command:

```sh
UV_PROJECT_ENVIRONMENT=$SCRATCH/venv3142 uv run --locked --python 3.14.2 pytest -q
```

Result: `634 passed in 36.01s`.

Command:

```sh
unshare -rn sh -c 'ip link set lo up && UV_OFFLINE=1 uv run --locked pytest -q'
```

Result: `634 passed in 31.33s`.
The suite completed with no external network and with loopback available for fake servers.

The ASD-STE100 checker found 0 findings in `docs/providers/cloud.md`, `STRUCTURE.md`, and this record.

Command:

```sh
uv run --project $WORKSPACE/assist-live python $WORKSPACE/assist-live/tools/check_public_tree.py --history --root . --private-denylist $WORKSPACE/.opencode/private/denylist.txt
```

Result:

```text
PASS own Git root
History secret check: PASS
PASS public data matches=0
```

Six mutation checks made isolated copies of the server source.
The Python parser accepted each copy. Each test loaded its copy from the isolated path.
Each mutation caused one unsatisfactory behavioral test result:

1. Remove opt-out on reconnect: unsatisfactory request-option assertion.
2. Put the credential in a log: unsatisfactory credential-protection assertion.
3. Use the default chunk schedule: unsatisfactory initial-message assertion.
4. Add a second 120-character text buffer: first-audio timeout before input end.
5. Remove flush: unsatisfactory flush-message assertion.
6. Call a cloud constructor from the default factory: unsatisfactory default-provider assertion.

No mutation changed the working source. No commit or live cloud check occurred.

### Cloud response limits

Date: 2026-10-26 (UTC).
Fake-server tests sent too many committed transcript segments, interim text, `alignment` text, and PCM audio.
The initial limit tests gave 9 unsatisfactory results. The cumulative `alignment` test also gave an unsatisfactory result after sentence punctuation changed.
The fragment limit did not test a total budget across complete sentences.
The count-free history-result test also found the commit count in this record.

The Deepgram receiver limits committed message count, segment bytes, and total transcript bytes before it keeps new text.
The ElevenLabs receiver limits each `alignment` message, total `alignment` bytes, total audio bytes, and stream time.
The tests examine socket closure, static errors, memory use, audio limits, and accepted transcript boundaries.
The [cloud provider contract](providers/cloud.md) gives the limits and their units.

Command:

```sh
uv run --locked pytest tests/providers/test_deepgram.py tests/providers/test_elevenlabs.py tests/turn/test_profiles.py::test_profile_documentation_records_particle_cost_without_fixed_ref_counts -q
```

The initial corrected limit checks gave `63 passed in 0.66s` before the boundary-control tests.

The complete provider suite then gave `80 passed in 0.71s`.
This includes text-derived time and PCM budgets, quiet `partial` results, optional `is_final` flags, request logging, and the URL-parser dependency.
Long reply controls use 160 seconds of output at 16000, 22050, and 24000 Hz.
Short-input controls reject audio above the allowance for the text sent.
The deadline test changes the monotonic clock at a message boundary. It does not wait for real-time audio playback.

Cancellation tests call only the cancellation operation before they examine socket, HTTP session, and task state.
The STT test waits for the cleanup task that `cancel` itself schedules. It does not use `aclose` to close the session first.
The TTS test cancels its pending stream read and examines cleanup without one more stream close.

Eleven mutation checks removed transcript limits, `alignment` limits, output budgets, cancellation cleanup, the input ceiling, and the URL-parser dependency.
Each changed copy gave an unsatisfactory test result. The working source did not change during the checks.
The logging default stays the provider default, `enable_logging=True`. An explicit adapter option can request `False` for an eligible account.
The provider contract records the public logging, account restriction, and PCM format sources.

## Provider destinations and speaker metadata

Date: 2026-10-26 (UTC).

Initial command:

```sh
uv run pytest tests/test_destinations.py tests/test_speaker.py tests/contract/test_streaming_tts.py -q
```

Initial result: `22 failed, 8 passed`.
The failures included `ModuleNotFoundError: No module named 'assist_live_server.destinations'`.

Plan command: `69 passed in 1.28s`.

Expanded command:

```sh
uv run pytest tests/test_destinations.py tests/test_speaker.py tests/test_audio_gateway.py tests/contract/test_streaming_tts.py -q
```

Expanded command: `78 passed in 1.68s`.
The tests use synthetic PCM and local fake HTTP and WebSocket services.
No paid cloud request or live Murdock request ran.
The Murdock request shape comes from release `v0.13.0`, as recorded in `configuration.md`.

The health matrix checks STT-only, TTS-only, full, and disabled configurations.
Its initial TTS-only result was `assert False is ((False or True))`.
The client and SQLite tests check unchanged transcripts, speaker metadata, and the two transcript options.
Speaker names use NFC, 1 to 64 characters, and at most 256 UTF-8 bytes.
Invalid names have no stored value and no truncated value. Counters identify each drop cause without the name.

The DNS tests check configuration answers and connection answers independently.
They also check the HTTP host, the explicit TLS name, and the destination class of the selected connection.
Standard address definitions use integer octets and prefix lengths, not deployment addresses.

The Python parser accepted 23 isolated source mutations. Each mutation caused a behavioral test failure:

1. Check DNS only at configuration load.
2. Use `is_private` as the address policy.
3. Follow an HTTP redirect.
4. Load a cloud provider because of a credential.
5. Accept a cloud provider with `local_only`.
6. Add a speaker prefix to the transcript.
7. Write speaker audio to a file.
8. Remove TTS from health.
9. Set transcript storage to `true` by default.
10. Remove NFC from speaker names.
11. Accept control characters in speaker names.
12. Accept a speaker name one character above the limit.
13. Accept an empty speaker name.
14. Accept whitespace at the start or end of a speaker name.
15. Accept a display name in a different event `stage`.
16. Accept a display name in the provider key.
17. Do not count a dropped speaker name.
18. Decrease the speaker-name byte limit by one.
19. Remove the factory destination check at request selection.
20. Put a speaker name in a log message.
21. Put a speaker name in a metric dimension.
22. Truncate a speaker name before storage.
23. Use the pinned address as the TLS name.

The working source did not change for these tests.

Repository commands:

```sh
ASSIST_LIVE_MODEL_CACHE="$MODEL_CACHE" task check
UV_LINK_MODE=copy ASSIST_LIVE_MODEL_CACHE="$MODEL_CACHE" UV_PROJECT_ENVIRONMENT="$FLOOR_ENV" uv run --locked --python 3.14.2 pytest tests/ -q
ASSIST_LIVE_MODEL_CACHE="$MODEL_CACHE" unshare -rn sh -c 'ip link set lo up && UV_OFFLINE=1 uv run --locked pytest -q'
```

The first repository, floor-Python, and network-blocked runs each had one unsatisfactory documentation check:
`test_profile_documentation_records_particle_cost_without_fixed_ref_counts`.
Formatting, lint, CI boundaries, and mypy gave satisfactory results. The network-blocked run had loopback enabled.

The ASD-STE100 check found no findings in `configuration.md`, `protocol.md`, or `STRUCTURE.md`.
The canonical public-tree check gave `PASS public data matches=0`.
No commit occurred.

Complete runs on 2026-10-26 use the current tree:

Repository gate: `733 passed in 24.46s`.

Floor Python: `733 passed in 24.43s`.

Network-blocked suite: `733 passed in 22.88s`.

The plan commands and repository gate ran from `2026-10-26T09:01:04Z` to `2026-10-26T09:01:42Z`.
The floor command ran from `2026-10-26T09:01:04Z` to `2026-10-26T09:01:29Z`.
The network-blocked command ran from `2026-10-26T09:02:22Z` to `2026-10-26T09:02:46Z`.
The documentation history-count check gave a satisfactory result in each run.
The repository check also gave `PASS server bootstrap`, `PASS isolated client and server packages`, and `PASS asset licenses`.
Gitleaks found no leaks in the working tree or history.

The worktree has no adjacent copy of the canonical checker. Its `task check` public-tree step reports `NOT CHECKED public tree`.
The canonical command below checks the working tree and history with the private denylist:

```sh
uv run --project "$WORKSPACE/assist-live" python "$WORKSPACE/assist-live/tools/check_public_tree.py" --history --root . --private-denylist "$WORKSPACE/.opencode/private/denylist.txt"
```

Result: `PASS public data matches=0`.

## Health readiness and backend synthesis

Date: 2026-10-26 (UTC).

Initial command:

```sh
uv run pytest tests/contract/test_health.py tests/contract/test_streaming_tts.py -q
```

Initial result: `9 failed, 11 passed`.
The response did not have the nested `capabilities` object. The client did not export `HealthCapabilities`.
The legacy backend also sent a 16000 Hz format for declared 22050 Hz audio.

Complete command:

```sh
uv run pytest tests/test_destinations.py tests/test_speaker.py tests/test_audio_gateway.py tests/contract/test_streaming_tts.py tests/contract/test_health.py -q
```

Health-inclusive command: `88 passed in 1.72s`.
The health body has `version`, `healthy`, and `capabilities` only.
`healthy` shows store and replay readiness without a provider condition.
The client keeps `health()` and adds `health_capabilities()`. An older body gives two `false` function flags without an error.

The legacy backend stays available for embedding code and uses the TTS gateway.
Its `audio_format` field declares the sample rate. Each sentence must end with `sentence_end`.
The gateway refuses a missing format, a missing last marker, and an incorrect sentence index.
An `error` response before audio also stops the client sender before cleanup.

Five more isolated checks gave unsatisfactory behavioral test results:

24. Make legacy output use 16000 Hz.
25. Accept a backend sentence without its last marker.
26. Accept an incorrect backend sentence index.
27. Make health readiness use an audio provider condition.
28. Advertise audio functions for an older server without `capabilities`.

All 28 changed copies gave the necessary test failure. The working source did not change during these checks.

## Speaker response and provider registry checks

Date: 2026-10-26 (UTC).

Local fake-server checks use unmatched cosine distances of `1.4` and `2.0`.
The two distances must give an empty-name `speaker_result` message and a recorded event with the distance.
Distances above `2` or below `0` get `ValueError("invalid speaker result")`.
The store refuses those invalid distances too.

The server transcript option controls the route and the SQLite writer before requests start.
Tests use all four server-option and turn-option combinations, with an opposite initial store option.
Only two `true` values let the server store transcript text. Default-value tests also change each default to `true` in an isolated source copy.

Configuration uses the STT and TTS registry helpers for names, destinations, and construction.
A helper call can select a cloud provider, but a credential alone cannot select one.
A provider supplied with `tts_provider` declares its identifier without a destination-based name guess.

Concurrent speaker results have their own destination. Missing response fields get a constant `ValueError`.
Speaker URLs with a user or a password are invalid. An empty user does not change this rule.

The repair tests ran before the source repairs and gave unsatisfactory results.
The baseline checks and each repair mutation ran against isolated copies. No mutation changed the working source.
Mutation results: `PASS numbered mutations=28` and `PASS repair mutations=16`.

## Text limits and TTS stream end

Date: 2026-10-26 (UTC).

The TTS gateway adds one last sentence marker if provider audio is after the last marker.
Aligned streams keep their own markers. Nonempty text without audio gets `tts_failed`.
Empty and whitespace-only text can end cleanly without audio or sentence markers.

The client divides large text parts at code-point boundaries.
It measures the complete JSON message, with JSON-escaped text, before it sends each part.
The fake server gets ASCII text, JSON-escaped text, and four-byte characters unchanged.

The transcript budget is 16000 JSON-escaped bytes, without the two outer quotation marks.
Deepgram and the local STT gateway use this budget. It fits the two control messages, `final` and `partial`.
Text above the budget gives one terminal `stt_failed` error. The gateway does not send oversized text.

The server keeps the dependency pin `yarl==1.25.1`.
The public `SentenceAssembler` keeps the same sentence rules.

Commands from the repository root:

```sh
uv run --locked pytest -q tests/contract/test_streaming_tts.py tests/contract/test_streaming_stt.py tests/providers/test_deepgram.py tests/providers/test_piper.py -k 'configured_cloud_tts_factory or covers_only_audio or splits_text_to_actual or transcript_cap or local_final_deliverable_bound or public_sentence_assembler'
uv run --locked pytest -q tests/providers/test_deepgram.py -k cloud_transcript_encoded_budget --tb=short
uv run --locked pytest -q tests/contract/test_streaming_tts.py -k 'configured_cloud_tts_factory and (empty or whitespace)' --tb=short
uv run --locked pytest -q tests/contract/test_streaming_tts.py tests/contract/test_streaming_stt.py tests/providers/test_deepgram.py tests/providers/test_elevenlabs.py tests/providers/test_piper.py tests/test_audio_gateway.py --tb=short
```

Initial results: `22 failed, 2 passed, 106 deselected in 4.94s` and `4 failed, 4 passed, 48 deselected in 0.19s`.

The first command gives `30 passed, 115 deselected`.
The initial results came from an earlier `-k` expression that did not select the 8 `test_local_final_deliverable_bound` tests.
If the gateway sends the local `final` message without the transcript budget, 4 of these 8 tests give a failure.

The empty-text check first gave `2 failed, 25 deselected in 0.49s` with `ProtocolError: audio refused`.

The complete test run gave `195 passed in 5.79s`.

Thirteen isolated mutations test missing or duplicate markers, silent output, text division, byte size, JSON-escaped text, transcript limits, and the public assembler.
They also test the empty-text rule and the dependency pin.
Result: `PASS numbered mutations=13`. The working source did not change during these checks.

## TTS sentence text positions

Date: 2026-10-26.

The protocol contract is in `docs/protocol.md`.
Request positions count Unicode code points, not encoded bytes or graphemes.
Piper gives positions from the sentence assembler.
ElevenLabs gives a request position only when the sentence text from `alignment` agrees with the request sentence after whitespace normalization.
The last added gateway marker gives the full input count.
Legacy backend markers have no position.

The initial position command was:

```sh
uv run --locked pytest -q tests/providers/test_piper.py -k positions
```

Result: `11 failed, 29 deselected in 0.87s`.
The error was `AttributeError: 'TTSSentenceEnd' object has no attribute 'text_end'`.

The initial protocol, cloud, and health checks gave `16 failed, 22 passed, 77 deselected in 0.48s`.
The schema refused `text_end`. The gateway had no request option. The health body had no position flag.

The property test uses random text and random code-point divisions.
For each marker, the test checks this equality:

```python
" ".join(full_text[:text_end].split()) == " ".join(" ".join(spoken_sentences).split())
```

This normalizes whitespace on each side. The right side connects the spoken sentences through the current marker.
Tests also check nondecreasing positions and the unsaid suffix.

Historical decoders at `8f3d60a` and `eb35c61` refuse unknown request and marker keys.
The historical decoders accept default server markers.
The historical server decoders accept the two default client TTS methods.
The historical decoders refuse the explicit position option.
Thus the client requests positions only after the caller reads the server's health flag.
The method option and the missing health flag default to `false`.

The mutation checks gave `PASS 25/25 behavioral mutations killed; all source files restored`.
The selected tests gave `173 passed in 3.02s`.

Commands from the repository root:

```sh
export UV_LINK_MODE=copy
export ASSIST_LIVE_MODEL_CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/assist-live/turn"
task check
UV_PROJECT_ENVIRONMENT="$FLOOR_ENV" uv run --locked --python 3.14.2 pytest -q
unshare -rn sh -c 'ip link set lo up && UV_OFFLINE=1 uv run --locked pytest -q'
uv run --project "$WORKSPACE/assist-live" python "$WORKSPACE/assist-live/tools/check_public_tree.py" --history --root . --private-denylist "$PRIVATE_DENYLIST"
```

`FLOOR_ENV` selects an isolated temporary environment. `PRIVATE_DENYLIST` selects the local privacy input.

| Check | UTC time on 2026-10-26 | Result |
| --- | --- | --- |
| `task check` | 10:42:03Z to 10:42:40Z | Exit 0. `831 passed in 30.74s`. |
| Python 3.14.2 | 10:42:03Z to 10:42:35Z | `831 passed in 31.57s`. |
| Offline network namespace | 10:42:03Z to 10:42:35Z | `831 passed in 31.55s`. |
| Privacy, history, and compatibility | 10:42:03Z to 10:42:07Z | All checks give exit 0. |
| ASD-STE100 | 10:42:03Z to 10:42:07Z | `docs/protocol.md`: 0 findings. `docs/verification.md`: 0 findings. |

The load averages before the complete gates were `6.86, 7.67, 8.74`, below the limit of `32`.
Formatting checked `101` files. Ruff gave `All checks passed!`.
Mypy gave `Success: no issues found in 43 source files`.
The bootstrap, isolated client and server packages, and asset license checks gave `PASS`.
Gitleaks found no leaks in files or checked history.

The embedded public-tree command gave `NOT CHECKED public tree` because this worktree has no sibling checkout for that tool.
The canonical command above ran the full check:

```text
PASS own Git root
PASS history secrets
PASS public data matches=0
```

The publishable-file count check found `denylist=0 home_paths=0 private_ipv4=0`.
The historical compatibility check gave `PASS` for each revision and each default TTS method.
No cloud service or paid API was necessary.

## TTS sentence position proof

Date: 2026-10-26.

The contract is in `docs/protocol.md`.
An ElevenLabs position is correct only when request and `alignment` sentence text agrees after whitespace normalization.
The first mismatch stops positions for that marker and each subsequent marker.
`normalizedAlignment` without `alignment` also stops positions for the remaining part of the stream.
The audio and sentence markers continue when text does not agree or the provider gives more sentences.

Tests include month and abbreviation expansion, decimal spelling, different sentence structures in the two `alignment` fields, and a mismatch in the middle.
They also check whitespace normalization, divided words, and the same sentence after positions stop.
The two client TTS methods ignore incoming positions without the request option.
With the option, the position checks apply.

The initial full suite gave `29 failed, 831 passed in 25.21s`.
The document test refused the history count. The other failures showed unproved positions and a turn failure for more provider sentences.
The public record does not keep a commit count for these checks.

The full suite then gave `862 passed in 27.52s`.
The mutation checks gave `PASS 9/9 repair mutations killed by the full suite; all source files restored`.
The tests find a preference for `normalizedAlignment` and missing sentence text checks.
They find position recovery after a mismatch and turn failures when the provider gives more sentences.
They also find unrequested client positions and acceptance of decimal integer tokens.

Commands from the repository root:

```sh
export UV_LINK_MODE=copy
export ASSIST_LIVE_MODEL_CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/assist-live/turn"
task check
UV_PROJECT_ENVIRONMENT="$FLOOR_ENV" uv run --locked --python 3.14.2 pytest -q
unshare -rn sh -c 'ip link set lo up && UV_OFFLINE=1 uv run --locked pytest -q'
uv run --project "$WORKSPACE/assist-live" python "$WORKSPACE/assist-live/tools/check_public_tree.py" --history --root . --private-denylist "$PRIVATE_DENYLIST"
```

The full suite runs after all file changes, with this document included.
The full checks run again after each result entry.

## Release artifact packaging

Date: 2026-10-26 (UTC).

The initial `uv run pytest tests/test_release.py -q` gave `1 failed`.
The error was `FileNotFoundError` for the missing initial app configuration file.
The test checks that the app has no HA API access.

The package check command is:

```sh
uv run pytest tests/test_release.py tests/test_main.py tests/test_ci.py -q
```

Result: `53 passed`. The tests include archive record mutations and server startup on loopback.
Bad configuration and an incorrect SHA-256 give one named failure line without secret values.

The native artifact commands are:

```sh
uv build --package assist-live-client
docker build --platform linux/amd64 --tag assist-live-server:check .

docker save --output "$RUNROOT/server-image.tar" assist-live-server:check
uv run python tools/check_release.py --wheel dist --image "$RUNROOT/server-image.tar" --assets assets/manifest.json
```

Result: `PASS release checks`. The image contains 42 recorded model-like package files, with 32980143 bytes in total.
The selected providers do not use 32960567 of these bytes. The image keeps all runtime wheel files without changes.

The client wheel uses `aiohttp<4,>=3.14.4` as its only `direct dependency`.
A new Python `3.14.2` environment installed this wheel and its dependencies only.
Result: `PASS isolated model-free client`. The environment had no server package or model runtime.

The app uses `root`. The standalone service uses UID `10001`.
The [HA options notice](https://developers.home-assistant.io/blog/2020/10/29/supervisor-update/#add-on-options) states that `/data/options.json` uses mode `600`.
It gives `root` as the default container user.

The [current HA folder document](https://developers.home-assistant.io/docs/apps/configuration/#folders) identifies `/data` as private app data with read and write access.
A local app container read root-owned mode-`0600` options and wrote its SQLite store to a root-owned `/data` directory.
Result: `PASS app started with root-owned mode-0600 options and wrote SQLite store`.
No live Supervisor installation test ran.

The [HA BuildKit document](https://developers.home-assistant.io/blog/2026/04/02/builder-migration/#update-dockerfiles) says that new builds do not use `build.yaml`.
The app uses Dockerfile base-image definitions, not the deprecated file.

The startup plan downloads models, tokenizers, and voices only. The build plan identifies installed runtime wheel sources.
The image checker compares installed versions and wheel hashes with these records.
It also checks model-like file bytes, source quotations, attribution, and license claims.

Seventeen isolated source mutations each gave an unsatisfactory behavior test result.
Result: `PASS numbered mutations=17`. The source tree did not change during the mutation checks.

The base image keeps its installer and headers without changes.
A runtime-image check makes sure that no compiler or `uv` is available.
The Dockerfile test refuses a base-image path deletion or a build-tool copy from the build environment.

Command: `ASSIST_LIVE_MODEL_CACHE="$MODEL_CACHE" task check`.
Results: `806 passed`, `Success: no issues found in 45 source files`, `PASS CI boundaries`, and `PASS asset licenses`.
The two isolated package checks gave `PASS isolated client and server packages`.
The `secret scans` found no leaks in the files or history.

The worktree has no sibling privacy-checker directory, so the task prints `NOT CHECKED public tree`.
The last privacy gate uses the canonical checker from the integration repository with `--history` and the private denylist.
This command does not change the public tree or its history.

CI has amd64 and aarch64 OCI builds that compare license inventories without a push.
No local aarch64 build or hosted run completed in this check.
The artifact workflow stays manual and fail-closed behind the publication approval `variable`.
No publication, registry login, tag, or release ran.

## Release packaging review checks

The review repair tests check telemetry before ONNX Runtime import, with an explicit `0` in the caller's environment.
They also check an empty registry, a provisioned registry, named health failures, and incorrect health refusals.
The repository-layout test accepts only `assist_live_server/config.yaml` in the release tree.
It checks the root repository descriptor and makes sure that no second app Dockerfile is available.

The app directory agrees with its `slug`. Its configuration uses `stage: experimental` and the default container init process.
The root Dockerfile supplies the service and app targets.

The local-only trial starts the service image with an empty model directory and network access for the approved startup downloads.
It records the eight pinned files, their sizes and SHA-256 values, and the download interval.
The next service run uses the same files with `--network none`.
Synthetic Piper audio supplies the Parakeet STT request through the client and server routes.

The network `syscall trace` includes provider initialization and the requests. A loopback connection supplies its positive control.
The trial also checks the image telemetry setting, the `installed distribution list`, and that no `telemetry_1ds` log line occurs.
The configuration document lists the examined package download paths and their local-only status.

The release-record mutations add an unlisted model file, change the `fbanks.npz` bytes, remove its record, remove a source quotation, and change the `license label`.
Each mutation must give a named release failure without a traceback.
The unchanged image is the positive control.

The full gates run after all source and documentation changes.
No history length is part of the privacy result record.

The app folder and its slug use `assist-live-server`. This name is different from the Python package name.

## Startup runtime imports

Date: 2026-10-26.

The Python `3.14.2` and `3.14.3` CI checks reported `5 failed, 901 passed`.
The failure line included `[W:onnxruntime:Default, device_discovery.cc:95 GetPciBusId] Skipping pci_bus_id`.
An eager detector import loaded ONNX Runtime before configuration and model checks.
The Hyper-V CI host then emitted a PCI device-discovery warning on stderr.

The command sets `ORT_DISABLE_TELEMETRY=1` before provider setup. The caller cannot override this value.
Shared path and SHA-256 checks do not import the detector.
The detector loads ONNX Runtime only when it makes a model runtime, after the model checks.
The server imports `app` after configuration, download checks, and selected provider setup.

Subprocess tests examine `sys.modules` after a live health check, disabled-provider startup, bad configuration, and bad cached hashes.
These paths must not load ONNX Runtime. Their stderr assertions stay empty.
A synthetic selected Piper module imports ONNX Runtime only after telemetry and cached-file checks.
Other subprocess tests check missing and broken runtimes, named startup failures, and detector construction failures.

Command: `uv run --locked pytest -q tests/test_main.py -k 'valid_startup or health_failure or bad_configuration or bad_cached_hash'`.
RED result: `6 failed, 10 deselected` with `AssertionError: assert ['onnxruntime', ...] == []`.
Command: `uv run --locked pytest -q tests/test_main.py`.
GREEN result: `22 passed`.

The eager-import mutation gave `6 failed, 16 deselected` in the no-import tests.
The telemetry-override mutation gave `3 failed, 3 passed, 16 deselected`.
The eager detector mutation gave `2 failed, 20 deselected` in the construction tests.
The provider-before-hash-check mutation gave `6 failed, 16 deselected`.
The last gates use source with no mutations.

## Health policy and destinations

Date: 2026-10-27 (UTC).

The authenticated health body reports `local_only` and the configured STT, TTS, and speaker destinations.
Missing fields from older servers give `None`. Servers without configuration report `local_only: null`.
The speaker field uses the completed address check, not a new DNS request.
Unauthenticated requests get `401 unauthorized` with no configuration information.

Command: `uv run --locked pytest -q tests/contract/test_health.py`.
RED result: `23 failed, 1 passed`, with `AttributeError: 'HealthCapabilities' object has no attribute 'local_only'`.
Command: `uv run --locked pytest -q tests/contract/test_health.py tests/test_speaker.py`.
GREEN result: `67 passed`.

The health tests check local-only on and off, local and cloud STT, TTS-only operation, and missing or unchecked speaker plug-ins.
Configuration checks must refuse cloud STT with local-only on before server startup.
Loaded speaker tests use synthetic local and documentation addresses. Health requests must not resolve those addresses again.

The clients at `8f3d60a`, `eb35c61`, and `b4a9202` accept the new default response without negotiation.
Their initial health routes also accept the current client. Missing policy and destination fields decode to `None`.
The first two client decoders read only `stt` and `tts`. The third also reads `text_positions`.
All three ignore unknown top-level keys and unknown keys in `capabilities`.

Sixteen mutations each gave a test failure.
They changed policy reporting, provider or speaker destinations, missing parts, authentication, DNS access, defaults, field validation, and completed address checks.
The last gates use source with no mutations.

## Current speaker destination state

Date: 2026-10-28 (UTC).

Speaker health reports a completed class only for a literal IP address. A hostname always reports `unknown`.
Each new request clears the class before resolution. Only the newest request can publish its checked class.
A policy refusal publishes its class. A resolution failure with no class keeps `unknown`.

The constructor's request-guard `mode` does not supply a completed check.
The provider factory publishes the checked startup class for a literal address.

STT and TTS sources supplied without a declared destination report `unknown`.
Their request guards keep their previous defaults. Health does not use those defaults as destination information.

Command: `uv run --locked pytest -q tests/contract/test_health.py -k 'speaker or injected_provider' --tb=line`.
RED result: `12 failed, 4 passed, 24 deselected`.
The failures include stale local state after a cloud policy refusal, state during resolution, and out-of-order checks.
Hostname, constructor-mode, and undeclared-provider checks also gave destination assertion failures.

Command: `uv run --locked pytest -q tests/contract/test_health.py tests/test_speaker.py tests/test_destinations.py tests/test_audio_gateway.py`.
GREEN result: `106 passed`.

Explicit invalid TTS destination attributes keep their earlier refusal. An attribute with `None` is not a missing attribute.

The out-of-order test uses events, not time intervals. Its result was `PASS` in 20 subprocess runs, one after the other.
The mutation without the sequence guard gave the same assertion failure in all five subprocess runs.

Ten production-site mutations each gave a named test assertion failure. The unchanged import control gave `PASS`.
The mutation checks left production files unchanged. The last gates use source with no mutations.

## Declared timing destinations and health keys

STT and TTS timing records do not include the destination when the supplied source has no declared class.
Health keeps `unknown` for these sources. The request guards keep their earlier defaults.
The timing destination values stay `local` and `cloud`.
The packaged Parakeet and Piper paths keep their local destination records.

The client keeps only `stt`, `tts`, and `speaker` destination keys.
It drops other keys without an error or output of those keys.
Invalid values for known keys cause a protocol error.
Missing fields and explicit null values keep their earlier results.

The initial destination tests gave `10 failed, 9 passed`.
The failures found invented local timing destinations and unknown health keys.
The packaged paths and the three historical health bodies gave `PASS` as controls before the change.
The complete health tests then gave `69 passed`.

Four isolated mutations each gave a named test assertion failure.
Three used the request-guard default in timing metadata.
One kept unknown destination keys in the decoded health result.
The mutation checks left production files unchanged. The last gates use source with no mutations.

## TTS voice lists and defaults

Date: 2026-10-29 (UTC).

The health response adds `capabilities.tts_voices` and `capabilities.tts_default_voice` without changes to the earlier boolean flags.
Piper reports `en_US-lessac-medium`, name `Lessac (medium)`, and language `en-US`.
The pinned voice configuration gives `language.code=en_US`, `dataset=lessac`, and `audio.quality=medium`.
The response uses BCP-47 syntax for the language.

ElevenLabs reports its configured voice. Its app options and environment settings supply the identifier, name, and language.
Backends without a list do not add voice fields. The client gives an empty tuple and no default for these fields.

Missing and empty request voices use the provider default. A nonempty unlisted voice gives `unknown_tts_voice` before synthesis.
Piper keeps its provider guard. The client keeps this named error.
The two client methods send no voice field for `None` and keep an explicit string unchanged.

Command: `uv run --locked pytest tests/contract/test_health.py tests/contract/test_tts_voices.py -q`.
Initial RED result: `37 failed, 61 passed`.
Failures included missing metadata checks, missing default selection, and an unwanted voice field.
The last tests in an isolated baseline tree gave `72 failed, 228 passed`.
The configured cloud voice and app option tests also found missing metadata and mappings.

Command: `ASSIST_LIVE_MODEL_CACHE="$MODEL_CACHE" uv run --locked pytest -q`.
GREEN result: `1024 passed`.
The tests include Piper requests through the client and raw WebSocket messages, configured ElevenLabs default requests, malformed metadata, and earlier bodies.
Clients from `8f3d60a`, `eb35c61`, `b4a9202`, and `b4fd28b` each decoded the new body with health `true` and `false`.
The current client also decodes the four earlier body shapes without voice metadata.

Eight isolated mutations each gave a behavior test failure after a satisfactory Python syntax check:

1. No default voice selection: `4 failed, 10 passed`.
2. No voice list in health: `2 failed, 13 passed, 70 deselected`.
3. An empty voice field for client `None`: `2 failed, 4 passed, 8 deselected`.
4. Acceptance of an unlisted Piper voice: `2 failed, 6 passed, 6 deselected`.
5. No configured ElevenLabs voice guard: `1 failed, 54 deselected`.
6. No voice field validation: `4 failed, 8 passed, 73 deselected`.
7. Acceptance of duplicate identifiers: `1 failed, 11 passed, 73 deselected`.
8. Acceptance of an unlisted default: `3 failed, 9 passed, 73 deselected`.

The fourth mutation disables the gateway and Piper guards together to test actual acceptance.
All mutation changes stay in disposable copies. No cloud service, paid API, or device was necessary.

Last commands from the repository root:

```sh
RUFF_CACHE_DIR="$RUFF_CACHE" uv run --locked ruff format --check .
RUFF_CACHE_DIR="$RUFF_CACHE" uv run --locked ruff check .
PRIVATE_DENYLIST="$PRIVATE_DENYLIST" task check
UV_PROJECT_ENVIRONMENT="$FLOOR_ENV" uv run --locked --python 3.14.2 pytest -q
unshare -rn sh -c 'ip link set lo up && UV_OFFLINE=1 uv run --locked pytest -q'
uv run --project "$WORKSPACE/assist-live" python "$WORKSPACE/assist-live/tools/check_public_tree.py" --history --root . --private-denylist "$PRIVATE_DENYLIST"
git diff --check
```

The Ruff cache starts empty. The ASD-STE100 checker examines the two changed Markdown files.
The worktree has no sibling privacy checker, so the explicit public-tree command supplies that check.
The last gates run after this record, with source that has no mutations.

## Turn start guard

Date: 2026-10-29 (UTC).

The detector ignores the first 400 ms for voice onset and time without voice.
It must then have 250 ms of continuous voice before command start.
Before command start, `early` and maximum-silence endpoints stay off.
A turn without command start ends at 5000 ms of received audio.
The end value stays `max_silence`: the control message has no `reason` field, and the store drops `reason` metadata.
No client or protocol schema changed.

Command: `uv run --locked pytest tests/turn/test_start_guard.py -q --tb=short`.
RED result: `10 failed`. The wake tail caused an `early` end at 700 ms.
Time without voice at the turn start caused an end at 1500 ms.
GREEN result: `10 passed`.
The tests include the two `Profile` settings, continuous voice, the wait boundary, and a follow-up turn.

Command: `uv run --locked pytest tests/contract/ -q --tb=short`.
Result: `272 passed`. The protocol assertions stay unchanged.

A local synthetic replay used the pinned Piper voice, Parakeet provider, Silero VAD, and Smart Turn.
All eight model files gave satisfactory SHA-256 checks before use.
The input used the last 250 ms of "Okay Nabu", 600 ms without voice, and "What time is it?".
The replay resampled Piper audio to 16 kHz and kept all audio for STT.
Before the change, the transcript was `you`, and the detector ended at 576 ms before the command.
After the change, the transcript was `What time is it?`, and the detector ended at 2112 ms after the command.

No wake-tail prefix occurred in that transcript.
This replay is a local check, not a CI test. CI runs the 10 deterministic guard tests.
No synthetic audio is part of the repository.

Four isolated mutations each gave a named test assertion failure.
They removed the guard, decreased the continuous-voice time, counted time without voice before command start, and removed the 5 s wait.
The unchanged control gave `10 passed`. The mutation checks left production files unchanged.

### Voice at the wait boundary

Date: 2026-10-29 (UTC).

The 5 s wait does not stop a continuous voice run that starts before the wait boundary.
The detector continues until that run qualifies or stops.
After qualification, the usual end rules apply, with the hard endpoint at 15 s.
If the run stops after 5 s without qualification, the first audio frame without voice ends the turn with `max_silence`.
The public design record is `assist-live docs/design.md`.

Command: `uv run --locked pytest tests/turn/test_start_guard.py -q -k 'open_voice_run or open_run_residual' --tb=short`.
RED result: `4 failed, 10 deselected`. Each test ended at 5000 ms while voice continued.
Command: `uv run --locked pytest tests/turn/test_start_guard.py tests/turn/test_detector.py -q --tb=short`.
GREEN result: `47 passed`.

The late-command tests start voice at 4900 ms for 1 s and 20 s.
They check command onset at 4900 ms, the usual `early` endpoint, and the 15 s hard endpoint.
A 150 ms run that stops at 5050 ms ends at its first 32 ms frame without voice, at 5082 ms.
A different test keeps 4 ms of audio pending at 4996 ms.
Its `flush()` consumes the remaining audio at 5000 ms. A second empty `flush()` adds no audio time.

Five isolated mutations each gave a named test assertion failure. The unchanged control gave `14 passed`.
The new mutation removed the open-run check. The late-command test then gave an assertion failure at 5000 ms.
The mutation checks left production files unchanged.

Command: `ASSIST_LIVE_MODEL_CACHE="$MODEL_CACHE" uv run --locked pytest "$REPLAY" -q -s --tb=short`.
`REPLAY` identifies the local synthetic test, not a CI test. `MODEL_CACHE` contains the eight checked model files.
Result: `1 passed`. The transcript was `What time is it?`, without a wake-tail prefix.
The detector ended at 2112 ms, after the command audio ended at 1813.625 ms.

## Continuous voice at turn start

Date: 2026-10-29 (UTC).

Command start uses the first voice run with a minimum length of 250 ms and an end after 400 ms.
The run length includes voice before 400 ms. Its onset is the first voiced audio frame.
A run that ends at or before 400 ms does not start a command.

The server keeps all audio for STT. Time without voice starts only after command start.
The 5 s wait and its open-run rule stay unchanged. The hard endpoint stays 15 s.

Command: `uv run --locked pytest tests/turn/test_start_guard.py -q -k 'initial_continuous or run_crossing_guard or run_ending_at_guard or wake_tail' --tb=short`.
RED result: `3 failed, 4 passed, 12 deselected`.
Initial voice for 600 ms gave no onset. Initial voice for 900 ms gave onset 400 ms, not 0 ms.
A 250 ms run from 200 ms to 450 ms also gave no onset.
The wake-tail, 200 ms run, and 400 ms end-boundary controls gave satisfactory results before the change.

Command: `uv run --locked pytest tests/turn/ tests/test_audio_gateway.py -q --tb=short`.
GREEN result: `165 passed`.
The 600 ms and 900 ms commands start at 0 ms and end with the usual rules.
The 250 ms run starts at 200 ms. The 200 ms run does not start a command.
The tests keep the wake-tail pause, open-run deadline, reset-frame, and residual-flush assertions.

Three isolated mutations each gave a named test assertion failure. The unchanged control gave `19 passed`.
The mutations accepted a wake tail, counted voice only after 400 ms, and counted time without voice before command start.
The mutation checks left production files unchanged.

## Microphone audio history

Date: 2026-10-29 (UTC).

The optional `mic_open` data field `pre_roll_ms` gives the audio history length in milliseconds.
The server accepts integers from 0 to 1000 inclusive.
It refuses booleans, other types, values less than 0 or more than 1000, and use on other `stage` values.
Events without the field stay correct. Stored events and turn queries keep the value.

Command: `uv run --locked pytest tests/test_pre_roll.py -q --tb=short`.
The initial result was `24 failed, 2 passed`. The storage assertion gave `stored pre_roll_ms was dropped`.
The last result was `27 passed`, with an HTTP turn-query check.

The empty-data `mic_open` vector kept its canonical bytes and HMAC tag.
The archived client from `b3b1e16` decoded server turn and event records with this field.
The current client also accepted `mic_open` without it.

Command: `uv run --locked python "$MUTATION_PROBE"`.
Three isolated source mutations each parsed and gave a named assertion failure.
They accepted 1001 ms, accepted the field on `wake_detected`, or accepted a boolean.
The production files stayed unchanged during these checks.

The audio control schema stays unchanged because this field belongs to signed device events, not audio control messages.

### Unmatched audio history and invalid stored rows

Date: 2026-10-29 (UTC).

Expired microphone events become `unmatched` records without `pre_roll_ms`.
The records keep `original_stage=mic_open`, quality, and uncertainty.
The same rule applies to expired pending events and events with a queue wait of more than five seconds.

Command: `uv run --locked pytest tests/test_pre_roll.py -q --tb=line`.
The initial result was `18 failed, 27 passed`. The four expiry tests gave `ValueError: invalid event data`.
Invalid pending, event, and turn rows also stopped store operations or had no refusal counters.

The regression tests check maintenance, turn opening, turn queries, exports, and subscriptions.
They check pending deletion, the two expiry paths, and store restart.
Invalid event and turn rows stay in the database. Invalid pending rows do not.
Each rejected row increments a satellite-scoped counter and logs only its type and error class.

Command: `uv run --locked python "$MUTATION_PROBE"`.
Two isolated mutations copied history incorrectly, one at each conversion site.
Each source parsed before a named expiry assertion gave an unsatisfactory result.
The production files stayed unchanged during the mutation checks.

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
- An approved SPDX `license` and `license_sources` at that revision.
- `redistribution: true`, `download: included` or `startup`, and non-empty `attribution`.
- Relative artifact `paths` and an explicit `components` list, empty if there are no parts.

Approved licenses are `Apache-2.0`, `MIT`, `BSD-2-Clause`, `BSD-3-Clause`, `CC0-1.0`, and `CC-BY-4.0`.
Other licenses stop the release. This includes unknown, non-commercial, and no-derivatives terms and GPL, AGPL, or LGPL components.

An upstream license-text or model-card URL must contain the pinned revision. A badge URL is not a source record.
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

Unconfirmed hashes, revisions, and license sources stay empty for blocked assets. They are not permission to supply those assets.
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

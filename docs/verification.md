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
Piper is the only blocked-absent decision in the bootstrap manifest.
No model or voice rights investigation has a recorded result at this time.

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

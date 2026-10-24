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

HA runtime tests, type checks, and HACS validation are not part of the bootstrap. CI adds them.
CI compares warnings with this baseline and stops on each new warning.

## Review of all three repositories

On 2026-10-24, the review checked `assist-live`, `assist-live-server`, and `assist-live-esphome` for cross-repository contracts and convention drift.
It also checked comment and prose hygiene and privacy across history.
Result: REVISE. Changes were necessary.
This change aligns repository documents with shared conventions.
It also gives the bootstrap checker a main entry point, complete failure reporting, stdout results, and a Python-floor check.

A static analysis snapshot reported 0 errors, 0 warnings, 0 info, and 0 hints across 5 source files. Date: 2026-10-24.

# Verification

The Home Assistant 2026.10.3 Python requirement is `>=3.14.2`. Source: https://pypi.org/pypi/homeassistant/2026.10.3/json, field `info.requires_python`.

RED test command: `$WORKSPACE/assist-live-server: uv run --no-project --with pytest pytest tests/test_package.py -q`.

RED result: `ModuleNotFoundError: No module named 'assist_live_client'`. Date: 2026-10-24.

Ruff I001 rejected the first literal import order. The client import came before the standard-library import. The test puts `Path` first, then the client import on a new line. The test resolves repository paths from its file location.

Bootstrap command: `$WORKSPACE/assist-live-server: uv sync --locked && uv run pytest tests/test_package.py -q && uv run python tools/check_bootstrap.py`.

Bootstrap result: `1 passed` and `PASS server bootstrap`. Date: 2026-10-24.

Import command: `$WORKSPACE/assist-live-server: uv run python -c "import assist_live_server, assist_live_client; print('import ok')"`.

Import result: `import ok`. Date: 2026-10-24.

Tree command: `$WORKSPACE/assist-live-server: uv tree`.

Tree result: root lists the server and client. The client has no dependencies. Date: 2026-10-24.

Ruff command: `$WORKSPACE/assist-live-server: uvx ruff format --check . && uvx ruff check .`.

Ruff result: `14 files already formatted` and `All checks passed!`. Date: 2026-10-24.

STE command: run `ste_check.py` on the 10 Markdown files.

STE result: 0 findings. Date: 2026-10-24.

Git root result: `PASS own Git root`. Date: 2026-10-24.

Publish-file check result: 22 files, 0 denylist hits, 0 absolute home paths, and 0 RFC1918 addresses. Date: 2026-10-24.

## Repair round 1

Ruff I001 rejected the first import order. The client import came before `Path`. The repository has no Ruff configuration, so Ruff uses its default rules. Put `Path` first. Put the client import on the next line after one empty line. Test paths come from the test file location.

Test from the client directory: `$WORKSPACE/assist-live-server/client: uv run pytest ../tests/test_package.py -q` → `1 passed`. Date: 2026-10-24.

Dependency probes in a temporary copy: `tensorflow`, `huggingface-hub`, and `faster-whisper` each printed `FAIL client model-runtime dependencies`. `pydantic-model` printed `PASS server bootstrap`. Date: 2026-10-24.

Missing descriptor probe in a temporary copy: removing `ha-app/repository.yaml` printed `FAIL app descriptor`. Date: 2026-10-24.

The probe removed the temporary copy after the tests.

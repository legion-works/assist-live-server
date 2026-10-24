# Contribution instructions

## Status

The project is experimental. No release is available.
Keep changes small. Add tests for changed behavior.
Do not add personal data, secrets, or household recordings.

## Development checks

Use Python `3.14.2` or above and `uv`.
Run these commands from the repository root:

```sh
uv sync --locked
uv run pytest tests/test_package.py -q
uv run python tools/check_bootstrap.py
uvx ruff format --check .
uvx ruff check .
```

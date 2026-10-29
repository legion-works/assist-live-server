# Contribution instructions

## Status

The project is experimental. No release is available.
Keep changes small. Add tests for changed behavior.
Do not add personal data, secrets, or household recordings.

## Development checks

Use Python `3.14.2` or above, `uv`, Task, actionlint, and Gitleaks.
The CI workflow pins the tool versions. CI tests Python `3.14.2` and `3.14.3`.
Run these commands from the repository root:

```sh
export UV_LINK_MODE=copy
export ASSIST_LIVE_MODEL_CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/assist-live/turn"
task check
```

`task check` runs formatting, lint, type, test, package, asset, and secret checks.
It fetches the pinned turn models into the external cache before tests.
The public-tree task uses the sibling `assist-live/tools/check_public_tree.py`. Without that file, it prints `NOT CHECKED public tree`.
Set `PRIVATE_DENYLIST` to an external denylist file to include private terms in that check.

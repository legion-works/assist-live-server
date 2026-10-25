# Repository structure

The repository has three deliverables in different directories.

The `server/` directory holds the server package.

The `client/` directory holds the client package.

The `ha-app/` directory holds Home Assistant app files.

The `tests/` directory holds package tests.

The `tools/` directory holds repository checks.

The `docs/` directory holds verification records.

The root Python project is a uv workspace. It installs the two Python packages for local development.

## Protocol files

- `protocol/control.schema.json`: JSON control schema.
- `protocol/device-vectors.json`: device authentication vectors.
- `docs/protocol.md`: byte layout, limits, credentials, and dependency sources.
- `server/assist_live_server/app.py`: authenticated aiohttp routes.
- `server/assist_live_server/auth.py`: credentials, signing, and replay state.
- `server/assist_live_server/protocol.py`: timing adapters and store interfaces.
- `server/assist_live_server/provision.py`: local credential-file command.
- `client/assist_live_client/client.py`: asynchronous client methods.
- `client/assist_live_client/models.py`: shared turn and timing records.
- `client/assist_live_client/protocol.py`: bounded JSON and audio codecs.
- `tests/contract/conftest.py`: isolated service fixtures.
- `tests/contract/test_protocol.py`: byte-layout and client contracts.
- `tests/contract/test_auth.py`: authentication, mapping, and replay tests.
- `tests/contract/test_heartbeat.py`: message deadlines and task cleanup.
- `tests/contract/test_queries.py`: subscription, query, export, and privacy tests.

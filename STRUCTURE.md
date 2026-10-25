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

## Timing storage

- `server/assist_live_server/clock.py`: minimum-offset clock window and uncertainty.
- `server/assist_live_server/event_gateway.py`: device conversion and the acceptance task.
- `server/assist_live_server/store.py`: SQLite writer, turn records, pending events, and retention.
- `server/assist_live_server/replay_store.py`: replay-state rows in the shared transaction.
- `server/assist_live_server/metrics.py`: Prometheus text without a new dependency.
- `tests/test_clock.py`: clock windows, units, and boot changes.
- `tests/test_store.py`: persistence, transcript policy, and the single writer.
- `tests/test_pending_events.py`: satellite access, reorder, and expiry.
- `tests/contract/test_event_store.py`: device-to-client records through the service.

## Turn detection

- `server/assist_live_server/assets.py`: shared asset-manifest loader.
- `server/hatch_build.py`: one manifest source for server wheels and source archives.
- `server/assist_live_server/turn/detector.py`: local ONNX models and guarded endpoints.
- `server/assist_live_server/turn/transcript_guard.py`: English transcript-ending checks.
- `server/assist_live_server/turn/profiles.py`: unmeasured defaults and `Profile` limits.
- `tools/tune_profiles.py`: partial-trajectory schema, replay, tuning, and held-out checks.
- `tests/turn/`: synthetic policy tests and pinned-model inference tests.
- `docs/profiles.md`: defaults, particle-ending cost, inference throttle, and stream-stall contract.

Privacy checks use the canonical `assist-live/tools/check_public_tree.py`, not a server copy.

Model files stay in an external cache. They are not in the Python packages.
The client package has no model runtime or asset manifest.

## Local voice recognition

- `server/assist_live_server/providers/stt.py`: session interface, bounded executor, and provider registry.
- `server/assist_live_server/providers/parakeet.py`: pinned local model and onset-gated complete-buffer decode.
- `server/assist_live_server/providers/streaming_local.py`: no streaming default.
- `tests/providers/`: input order, first word, onset, cancellation, buffer, and queue limits.
- `docs/providers/stt.md`: compatible-mode contract, decode cost, and replay limits.
- `server/assist_live_server/audio_gateway.py`: per-turn STT sessions, detector calls, and `partial` results.
- `tests/test_audio_gateway.py`: onset, endpoint, stall, bound, and concurrent decode tests.
- `tests/contract/test_streaming_stt.py`: streaming messages, timing events, disconnect, and audio-frame limits.

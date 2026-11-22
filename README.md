# assist-live-server

The project has three deliverables:

- `assist-live-server` is the server service package.
- `assist-live-client` is the Python client library used by the Home Assistant integration.
- `assist-live-server/` contains the Home Assistant app packaging.

The root `repository.yaml` identifies the HA app repository.

This project is experimental. The server has no release. Do not give the server a Home Assistant administrator credential.

The server supplies timing storage, turn detection, STT, and TTS. The client supplies the integration's protocol interface.
Packaging checks do not prove a live HA app installation.

## Functions

- Authenticated WebSocket audio, signed device timing through WebSocket or UDP, SQLite turn records, JSON Lines export, and Prometheus metrics.
- Local Parakeet STT with beam search for the last transcript and optional satellite-specific name hints.
- Local Piper TTS with sentence-level audio. Cloud options are Deepgram or OpenAI STT and ElevenLabs TTS.
- Guarded turn detection with the default `partial` rule or the Parakeet-only `checkpoint` rule.
- Local-only mode on by default, transcript storage off by default, and optional Murdock speaker metadata without transcript changes.
- A Python client with typed TTS audio, optional request-text positions, health metadata, timing subscriptions, and DNS-failure recovery.

See [server configuration](docs/configuration.md), [turn settings](docs/profiles.md), and [the protocol](docs/protocol.md) for options and limits.
The [release instructions](docs/release.md) give container build commands, platform checks, and the client pre-release workflow.
The client `0.1.0a1` is a pre-release on PyPI. No server package or image release is available.

See [the architecture](ARCHITECTURE.md), [the structure](STRUCTURE.md), [development checks](CONTRIBUTING.md), and [verification notes](docs/verification.md).

## License

Project code uses Apache-2.0. Third-party components keep their own licenses.
See [LICENSE](LICENSE) and [third-party notices](THIRD_PARTY_NOTICES.md).

A Legion Works project.

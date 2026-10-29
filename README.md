# assist-live-server

The project has three deliverables:

- `assist-live-server` is the server service package.
- `assist-live-client` is the Python client library used by the Home Assistant integration.
- `assist-live-server/` contains the Home Assistant app packaging.

The root `repository.yaml` identifies the HA app repository.

This project is experimental. It has no release. Do not give the server a Home Assistant administrator credential.

The server supplies timing storage, turn detection, STT, and TTS. The client supplies the integration's protocol interface.
Packaging checks do not prove a live HA app installation.

See [the architecture](ARCHITECTURE.md), [the structure](STRUCTURE.md), and [verification notes](docs/verification.md).

## License

Project code uses Apache-2.0. Third-party components keep their own licenses.
See [LICENSE](LICENSE) and [third-party notices](THIRD_PARTY_NOTICES.md).

A Legion Works project.

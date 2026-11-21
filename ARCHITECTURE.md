# Architecture

This file gives the package boundaries. The project is experimental and has no release.

## Server

The server package contains gateways, clock conversion, storage, a turn detector, and provider adapters. The server repository owns the protocol schema.

The integration uses authenticated HTTP and WebSocket routes. Signed device events use WebSocket or an explicitly enabled UDP listener.
One SQLite writer stores timing events and replay state together. Turn queries, subscriptions, JSON Lines export, and Prometheus metrics use satellite access checks.

The default providers are local Parakeet STT and sentence-level Piper TTS.
Explicit configuration can select Deepgram or OpenAI STT and ElevenLabs TTS. Local-only mode defaults to on and refuses cloud providers.
Optional Murdock speaker identification adds metadata without changes to the transcript.

Each STT turn has its own detector and provider session.
The `partial` end rule is the default. The Parakeet-only `checkpoint` rule uses 400 ms audio checkpoints and bounded decodes.
Parakeet uses beam search and optional satellite name hints for the last transcript only.
TTS removes Markdown marks before synthesis but keeps sentence positions in the initial request text.

The server stores no runtime audio. Transcript storage is on only with permission from the server configuration and the turn.
See [configuration](docs/configuration.md), [turn settings](docs/profiles.md), and [the protocol](docs/protocol.md).

## Client

The client package gives the Home Assistant integration its Python client. It has no model-runtime dependencies.
It supplies typed TTS audio, health metadata, name-list updates, turn records, and timing subscriptions.
It can use the caller's HTTP session without closing it. DNS recovery keeps the initial request deadline and TLS server name.

## Home Assistant app

The `assist-live-server/` directory packages the server for Home Assistant. It does not give the server an administrator credential.

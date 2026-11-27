# Changelog

## Unreleased

The project is experimental. The clients `0.1.0a1` and `0.1.0a2` are pre-releases on PyPI. No server package or image release is available.

### Server

- Add authenticated audio and timing routes, signed device events, durable replay protection, and per-satellite access checks.
- Add SQLite turn storage, clock estimates, JSON Lines export, Prometheus metrics, and 30-day retention by default.
- Add `stage` ownership, sentence timing, late turn opens, per-turn size limits, and safe retries of the same turn open.
- Add guarded turn detection with command-start checks and English transcript-ending checks. Human pause and prefix acceptance stays pending.
- Add the optional Parakeet `checkpoint` end rule with audio-based checkpoints, decode deadlines, backpressure, and failure counters.
- Add local Parakeet STT, beam search for the last transcript, and satellite-specific name hints for that transcript only.
- Cut STT audio at the detector's end sample. Send an empty transcript without a command start.
- Replace a shorter, unrelated `final` with earlier text when the replacement guard accepts it.
- Keep a turn open when an optional `partial` decode has no admission slot. Limit stalled audio sends and keep different checkpoint failure counters.
- Add local sentence-level Piper TTS, typed audio formats, request-text positions, and voice metadata in health responses.
- Remove Markdown marks before voice synthesis without changes to request-text positions.
- Add explicit Deepgram and OpenAI STT options and ElevenLabs TTS. Keep cloud services off by default.
- Send the 22050 Hz format of the OpenAI-compatible TTS server provider before its first synthesis request. Change 16000 Hz and each other source rate to 22050 Hz for each sentence. Empty or whitespace-only text gives only the format and a usual end.
- Add local-only destination checks, optional Murdock speaker metadata, and transcript storage with server and turn permission.
- Accept the firmware's pre-roll length in microphone-open timing events. Keep the store available when historical rows are invalid.
- Add standalone and Home Assistant app container targets for amd64 and arm64, pinned model downloads, and artifact license checks.

### Client `0.1.0a3` (next pre-release)

- Give `ProtocolError(kind="unauthorized")` for HTTP 401, also during a WebSocket upgrade.
- Stop `health()` with `ProtocolError(kind="unauthorized")` on HTTP 401. Other failures, such as HTTP 503 and timeouts, give `False`.
- Give `ProtocolError(kind="closed")` when the event subscription WebSocket closes.

### Client `0.1.0a2` (PyPI pre-release, 2026-11-25)

- Refuse redirects for the event and audio WebSockets, also when the scheme, host, and port do not change. A WebSocket handshake uses only the connector, timeout, and default headers of the supplied session.

### Client `0.1.0a1` (PyPI pre-release, 2026-11-22)

- Add the asynchronous protocol client with turn, event, query, export, STT, and TTS methods.
- Add typed TTS items, optional request-text positions, default-voice requests, and health metadata for voices, destinations, and name hints.
- Add satellite name-list updates, late-open timestamps, sentence timing records, and the `fallback_unavailable` code.
- Classify protocol errors as refused, closed, timeout, or malformed. Retry a DNS failure one time through a new system lookup before the initial deadline.
- Accept the Home Assistant-compatible `aiohttp>=3.14.4,<4` dependency without server model runtimes.

### Packaging

- Add different server and client Python packages.
- Add the Home Assistant app repository descriptor.
- Add package boundary and bootstrap checks.

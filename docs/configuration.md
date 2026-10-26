# Server configuration

The project is experimental and has no release.

## Provider selection

Load `ServerConfig` with `await ServerConfig.load(...)` before server setup.
Then call `await ProviderFactory.load(config, cache=MODEL_CACHE)`.
Give this factory to `create_app(registry, store, providers=factory)`.
The cache must contain the pinned local models. This path does not download models.

For turn storage, use `SQLiteStore(path, record_transcripts=config.record_transcripts)`.
The route and store then use the same server option.
Provider setup does not override a store that disables transcript storage.

| Option | Default | Purpose |
|---|---|---|
| `stt_provider` | `parakeet` | Select `parakeet`, `deepgram`, or `None` to disable STT. |
| `tts_provider` | `piper` | Select `piper`, `elevenlabs`, or `None` to disable TTS. |
| `local_only` | `True` | Refuse cloud providers and non-local speaker addresses. |
| `record_transcripts` | `False` | Store `final` transcripts only when the turn also opts in. |
| `speaker_url` | `None` | Disable speaker identification unless an explicit HTTP destination is set. |

Parakeet and Piper have destination `local`. Deepgram and ElevenLabs have destination `cloud`.
The factory loads only the selected providers.
`DEEPGRAM_API_KEY` and `ELEVENLABS_API_KEY` supply credentials for a selected cloud provider.
A credential does not select a provider. `local_only=True` refuses cloud selection before construction and at each request selection.

The `backend`, `audio_gateway`, and `tts_provider` arguments stay available for embedding code.
They cannot accompany `providers`. That embedding code owns its provider policy.

The configured factory is the server path that applies `local_only`.

## Speaker destination checks

With `local_only`, the speaker destination must resolve only to these address classes:

- IPv4 private networks, link-local addresses, or loopback addresses.
- IPv6 unique-local addresses, link-local addresses, or the loopback address.

Documentation, benchmark, multicast, unspecified, shared, and IPv4-mapped IPv6 ranges do not qualify.
Configuration load checks all DNS answers. Each speaker request resolves the name again before it sends audio.
A new connector uses only those checked addresses for that connection.

The request selects the first checked address. It does not retry a different address after a connection failure.
The result records the class of that connection, not other DNS answers.

The URL keeps the initial host name for the HTTP `Host` header and TLS certificate check.
The client refuses all redirects. It does not use environment proxies.
This policy is not a firewall and does not control other integrations.

## Murdock HTTP contract

The source is [Murdock release v0.13.0](https://github.com/BobMcGlobus/Murdock/releases/tag/v0.13.0).
It is the newest published release found on 2026-10-26 (UTC).
The tag resolves to commit `4f9636c92611069b3d843f9c8f0f50356a67eaa7`.

The [release route](https://github.com/BobMcGlobus/Murdock/blob/4f9636c92611069b3d843f9c8f0f50356a67eaa7/murdock/api/routes_speakers.py#L424-L446) accepts audio and gives speaker metadata.
Its router prefix is `/api/speakers`. The route is `POST /verify`.
Set `speaker_url` to the full `/api/speakers/verify` URL of the selected service.

The request is `multipart/form-data` with one field, `audio`.
That field contains a WAV file with mono, 16 kHz, signed 16-bit little-endian PCM.
The filename is `audio.wav`. Its content type is `audio/wav`.

The plug-in uses memory only. It does not write an audio file.
One request has a five-second deadline and a bounded response.

The response is a JSON object with these source fields:

```json
{
  "is_match": true,
  "matched_speaker": "speaker-a",
  "distance": 0.2,
  "threshold": 0.3,
  "all_distances": {"speaker-a": 0.2}
}
```

The plug-in interface is `identify(audio: bytes) -> SpeakerResult(name: str, distance: float)`.
It uses `matched_speaker` as the name only when `is_match` is `true`.
An unmatched result has an empty name in the message.

Invalid or unavailable speaker results do not change the STT `final` result.
No live Murdock test ran. Local fake HTTP tests check the mapping from the release source.
The operator must check the selected service's own retention settings before use.

## Speaker records and transcript policy

The client gets a `speaker_result` message after the STT `final` message.
The turn record gets a different `speaker_result` event with the actual destination.
The transcript does not get a speaker prefix or other changes.

When no plug-in is set, the empty `speaker_result` message means that the server has no speaker identification.
Audio stays only in the bounded turn buffer and request memory. The server does not store it.

A stored speaker name uses NFC. It must have 1 to 64 characters and at most 256 UTF-8 bytes.
It must have no control characters. It must not start or end with whitespace.
Only the `name` key of a `speaker_result` event can contain this display name.

The server drops an invalid name without truncation. A counter for each cause counts each drop.
Names do not go into log messages, errors, or metric dimensions. The event-retention policy also applies to names.

The server stores a `final` transcript only when the server and the turn each set `record_transcripts=True`.
The two options default to `false`. The server does not store `partial` transcripts.
STT and TTS timing records include the selected provider and the actual destination.

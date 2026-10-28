# Server configuration

The project is experimental and has no release.

## Provider selection

Load `ServerConfig` with `await ServerConfig.load(...)` before server setup.
Then call `await ProviderFactory.load(config, cache=MODEL_CACHE)`.
Give this factory to `create_app(registry, store, providers=factory)`.
The cache must contain the pinned local models. This path does not download models.

For turn storage, use `SQLiteStore(path)`.
`create_app` applies the server's `record_transcripts` option to the store before it accepts requests.
The configured factory supplies this option. Without that factory, use the option on `create_app`.
The store's initial value does not add a different switch when it runs in the server.

| Option | Default | Purpose |
|---|---|---|
| `stt_provider` | `parakeet` | Select `parakeet`, `deepgram`, or `None` to disable STT. |
| `tts_provider` | `piper` | Select `piper`, `elevenlabs`, or `None` to disable TTS. |
| `local_only` | `True` | Refuse cloud providers and non-local speaker addresses. |
| `record_transcripts` | `False` | Store `final` transcripts only when the turn also opts in. |
| `speaker_url` | `None` | Disable speaker identification unless an explicit HTTP destination is set. |

Parakeet and Piper have destination `local`. Deepgram and ElevenLabs have destination `cloud`.
The factory loads only the selected providers.

The STT and TTS `provider_names()`, `provider_destination()`, and `provider_factory()` helpers are the registry source for configuration.
The STT helper's explicit `mode="local_compatible"` filter lists local providers only. Its default lists all available providers.

`DEEPGRAM_API_KEY` and `ELEVENLABS_API_KEY` supply credentials for a selected cloud provider.
A credential does not select a provider. `local_only=True` refuses cloud selection before construction and at each request selection.

The `backend`, `audio_gateway`, and `tts_provider` arguments stay available for embedding code.
They cannot accompany `providers`. That embedding code owns its provider policy.
A provider supplied with `tts_provider` can declare `provider_id`. Without this field, records use `injected`, not an inferred provider name.

The configured factory is the server path that applies `local_only`.

### Local-only health state

Set `local_only=True` in `ServerConfig`, `ASSIST_LIVE_LOCAL_ONLY=true` for the command, or `local_only: true` in the app options.
The authenticated [health response](protocol.md#health-functions) reports that flag and the destination of each configured part.
The client gives these values through `health_capabilities().local_only` and `health_capabilities().destinations`.

With local only on, the integration must use the server only when the reported `local_only` value is `true`.
An older server without this field gives no policy confirmation. The integration must then use its fallback.
`false` and `null` also give no local-only policy confirmation.
Embedding code without `ServerConfig` gets `null`, also when its current providers are local.

The speaker destination reports a checked class only for a literal IP address.
A hostname reports `unknown`, also after a completed check.
Before or during an address check, or after a resolution failure, the class is `unknown`.
The health request does not run a new check. Each speaker request checks all DNS answers before it sends audio.
No speaker entry means that the server has no configured speaker plug-in.

### Local-only runtime paths

Startup downloads are explicit model setup, not audio requests. `download_models` controls them.
The startup command checks the pinned SHA-256 values before it loads providers.
Provider requests use the checked local cache.
The command gives the same cache path to the provider factory and the turn detector through `ASSIST_LIVE_MODEL_CACHE`.

| Path | Status with the selected local providers | Source |
|---|---|---|
| ONNX Runtime 1DS telemetry | Disabled before imports. The server command sets `ORT_DISABLE_TELEMETRY=1`. | Installed `onnxruntime/Privacy.md:38-44` and server `__main__.py`. |
| Piper voice-list and voice-download utilities | Not called. The provider makes `PiperVoice` with a checked model path and parsed local configuration. | `providers/piper.py:21-34,37-66` and installed `piper/download_voices.py:66-114`. |
| Piper HTTP voice catalog | Not called. The server uses its own aiohttp app, not Piper's HTTP server. | `__main__.py`, function `serve`, and installed `piper/http_server.py:180-190`. |
| Piper Chinese model download | Not selected. The pinned Lessac configuration uses `espeak`, not `PINYIN`. | Installed `piper/voice.py:211-221,259-260` and `piper/phonemize_chinese.py:369-383`. |
| onnx-asr model-name fallback download | Refused by its offline resolver. The provider supplies a checked local cache, not a remote-only selection. | `providers/parakeet.py:44-71` and installed `onnx_asr/resolver.py:69-74,147-171`. |
| NumPy URL data access | Not called. STT receives an array from PCM bytes. The preprocessor reads the bundled `fbanks.npz` file. | `providers/parakeet.py:85-87` and installed `onnx_asr/preprocessors/numpy_preprocessor.py:21-25` and `numpy/lib/_datasource.py:314-337`. |
| HTTP stack | Used for explicit requests, not a background `telemetry channel` or `update channel`. Local-only selection refuses cloud providers. | `config.py:114-147` and installed `aiohttp/client.py:558-576` and `aiohappyeyeballs/impl.py:15-29`. |

The image does not install the optional `huggingface_hub`, `transformers`, `requests`, or `httpx` packages.
No offline setting for a missing package is necessary.
The wheel-source check found no other `telemetry channel` or `update channel` in Piper, onnx-asr, NumPy, or the HTTP stack.
This static check does not replace the real-provider test with external networking disabled.

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
Cosine distance is finite and in the inclusive range from `0` to `2`. Murdock uses `2.0` when no speaker is available.
An unmatched result keeps an empty name in the message and a `speaker_result` event with the distance in the turn record.
The empty name does not become a stored display name.

Each immutable `SpeakerResult` also has its connection's `destination`, with `local` as the default.
Concurrent turns do not share a mutable result destination.

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

## Server command and image options

Run `assist-live-server` or `python -m assist_live_server` to start the service.
The service keeps `registry.json` and `turns.sqlite3` in its data directory.

The registry uses mode `0600`. The health check uses a credential when one is available.
With an empty registry, it accepts only the documented `401 unauthorized` response from the health route.
The service does not use an HA administrator credential.

The server command sets `ORT_DISABLE_TELEMETRY=1` before ONNX Runtime loads. It does not use a different value from the caller.
This stops ONNX Runtime from sending its default telemetry data. It also applies to a server installed from a wheel.

| Environment variable | Default | App option | Purpose |
|---|---|---|---|
| `ASSIST_LIVE_STT_PROVIDER` | `parakeet` | `stt_provider` | Select STT, or `none` to disable it. |
| `ASSIST_LIVE_TTS_PROVIDER` | `piper` | `tts_provider` | Select TTS, or `none` to disable it. |
| `ASSIST_LIVE_LOCAL_ONLY` | `true` | `local_only` | Refuse cloud providers. |
| `ASSIST_LIVE_RECORD_TRANSCRIPTS` | `false` | `record_transcripts` | Set the server transcript policy. |
| `ASSIST_LIVE_SPEAKER_URL` | empty | `speaker_url` | Set the speaker destination. |
| `ASSIST_LIVE_DOWNLOAD_MODELS` | `true` | `download_models` | Download missing selected startup files. |
| `DEEPGRAM_API_KEY` | empty | `deepgram_api_key` | Supply a runtime secret. |
| `ELEVENLABS_API_KEY` | empty | `elevenlabs_api_key` | Supply a runtime secret. |
| `ASSIST_LIVE_DATA` | `/data` | none | Set the data directory. |
| `ASSIST_LIVE_MODEL_CACHE` | data directory plus `cache` | none | Set the local model cache. |
| `ASSIST_LIVE_HOST` | `0.0.0.0` | none | Set the server address. |
| `ASSIST_LIVE_PORT` | `8099` | none | Set the TCP port. |
| `ASSIST_LIVE_ASSET_MANIFEST` | packaged manifest | none | Set the manifest for `--fetch-models` only. |

Boolean environment values must be `true` or `false`. The data directory must be writable by the service user.
The app reads `/data/options.json` and maps only the listed app options.
It does not put secrets in command arguments or print their values.

The manifest `download_plan` supplies startup models, tokenizers, and voices only.
`build_plan` supplies package provenance. The image installs these runtime wheels from `uv.lock` during the build.
The server does not download runtime wheel archives at startup.
Each download goes to its canonical cache path. A checked `hard link` also supplies the basename that the provider loaders use.
The same SHA-256 check applies to files in the cache.

Use `assist-live-server --fetch-models` to prepare the selected downloads without starting the service.

Use `assist-live-server --healthcheck` for the `/health` check on loopback.
That check reads a token from the private registry, or uses the unauthenticated response when the registry is empty.
It prints no token or key. Health failures start with `FAIL health:`.

The command gives a nonzero status and one named failure line for a startup or model-check error.
No cloud credential test is part of the local package checks.

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
| `stt_provider` | `parakeet` | Select `parakeet`, `deepgram`, `openai`, or `None` to disable STT. |
| `end_rule` | `partial` | Select `partial` or `checkpoint`. |
| `tts_provider` | `piper` | Select `piper`, `elevenlabs`, `openai_tts`, or `None` to disable TTS. |
| `local_only` | `True` | Refuse cloud providers and non-local speaker addresses. |
| `record_transcripts` | `False` | Store `final` transcripts only when the turn also opts in. |
| `speaker_url` | `None` | Disable speaker identification unless an explicit HTTP destination is set. |
| `stt_hint_bonus` | `1.0` | Parakeet name bonus, a finite number from `0` through `4`, inclusive. |

Parakeet and Piper have destination `local`. Deepgram, OpenAI, and ElevenLabs have destination `cloud`.
The resolved address sets the `openai_tts` destination, not the provider name.
The factory loads only the selected providers.

`ASSIST_LIVE_STT_HINT_BONUS` sets `stt_hint_bonus` for the server command.
`0` disables the bonus without a change to the accepted name list or digest.
Only the last Parakeet transcript uses the bonus. `partial` transcripts and cloud requests do not use names.
See the [name-hint protocol](protocol.md#stt-name-hints) and the [public design](https://github.com/legion-works/assist-live/blob/main/docs/design.md#stt-name-hints).

The STT and TTS `provider_names()`, `provider_destination()`, and `provider_factory()` helpers are the registry source for configuration.
The STT helper's explicit `mode="local_compatible"` filter lists local providers only. Its default lists all available providers.

`DEEPGRAM_API_KEY`, `OPENAI_API_KEY`, and `ELEVENLABS_API_KEY` supply credentials for a selected cloud provider.
A credential does not select a provider. `local_only=True` refuses cloud selection before construction and at each request selection.

For OpenAI STT, select `openai` and set `local_only=False`.
The provider uses one `gpt-live-transcribe` realtime transcription session for each turn.
It changes mono 16 kHz PCM16 input to 24 kHz. The server turn detector controls the commit.

`OPENAI_TRANSCRIPTION_DELAY` sets the transcription `delay` value. Its default is `low`.
The permitted values are `minimal`, `low`, `medium`, `high`, and `xhigh`.
The app options are `openai_api_key` (password field) and `openai_transcription_delay`.
See [cloud retention and protocol information](providers/cloud.md#openai-live-transcription) before you select this cloud provider.

The `backend`, `audio_gateway`, `tts_gateway`, and `tts_provider` arguments stay available for embedding code.
They cannot accompany `providers`. That embedding code owns its provider policy.
A provider supplied with `tts_provider` can declare `provider_id`. Without this field, records use `injected`, not an inferred provider name.

The configured factory is the server path that applies `local_only`.

### OpenAI-compatible TTS server

Set `ASSIST_LIVE_TTS_PROVIDER=openai_tts` to select an HTTP TTS server.
This provider does not select OpenAI STT or use `OPENAI_API_KEY`.

| Environment variable | Default | Purpose |
|---|---|---|
| `OPENAI_TTS_BASE_URL` | Necessary | HTTP or HTTPS base URL, without credentials, query parameters, or a fragment. |
| `OPENAI_TTS_DEFAULT_VOICE` | Necessary | Default voice identifier. |
| `OPENAI_TTS_LANGUAGE` | `en` | Language tag of each voice, for example `en`, `de`, or `en-US`. It must agree with the HA pipeline language. |
| `OPENAI_TTS_VOICES` | No filter | Comma-separated list of permitted voices. The default voice must be in this list when set. |
| `OPENAI_TTS_API_KEY_FILE` | No key | File with an optional bearer key. No key value goes in the configuration response. |
| `OPENAI_TTS_TIMEOUT` | `15` | Positive, finite request limit in seconds for each sentence, with address resolution and the complete response. |

Voice names contain 1 through 128 ASCII letters, digits, spaces, periods, underscores, or hyphens.
A name with only spaces is invalid. Other characters, with invisible format characters, cause a named configuration or catalog error.
Invalid names increase the `/metrics` counter `assist_live_tts_invalid_voice_names_total` and cause a count-only warning. The warning does not contain the names.

An empty key file is a configuration error. It does not disable authentication.
An `http://` URL sends the bearer key in clear text.
Provider startup gives one warning without the key or URL. Use `https://` to encrypt requests.

The 15 s limit gives a cold model time to load and synthesize a short sentence.
It is not a guarantee for a busy GPU queue.
The TTS server must supply `GET /v1/audio/voices` and `POST /v1/audio/speech`.
The reply format is always 22050 Hz mono PCM16. The server sends it before the first audio and changes each other source rate to 22050 Hz.
The [provider contract](providers/tts-local.md#openai-compatible-http-provider) gives the voice cache and audio rules.

Configuration and each connection use the same address checks as the speaker plug-in.
With `local_only=True`, each resolved address must be local.
Each connection uses the first checked address through a new pinned connector, without redirects or environment proxies.
The registry uses `cloud` as the conservative class before address resolution. Configured health reports the resolved class.

The voice list refresh interval is 60 s. A cached health request sends no HTTP request to the TTS server.
An expired health refresh has a 100 ms limit. The initial fetch has a 2 s limit.

After an unsatisfactory refresh, the last-known list stays available, so HA can select the configured voice and use its usual fallback.
Before a satisfactory fetch, the list contains the configured permitted voices, or only the default voice.
The health response has no field for the age or freshness of this list.
This route supplies no language metadata, so each voice gets the `OPENAI_TTS_LANGUAGE` tag.
A voice list with the `und` tag does not agree with an HA pipeline language, so HA refuses the TTS step.

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

The download time limit for each missing file is 120 seconds plus the pinned size divided by 256 KiB/s.
This time starts before connection setup and does not start again on a redirect.
The byte limit is the `bytes` value in the download plan. Each entry must have a positive integer size.
If a download uses more time or bytes than the limit, the server gives `model_download_time_limit` or `model_download_bytes_limit`.
It removes the temporary file without installing it.

The downloader checks the time before and after each body read. A body read can add one socket wait of a maximum of 120 seconds.
DNS, redirects, and HTTP headers and chunk metadata use the HTTP library's usual limits. They do not have a wall time limit.
Proxy settings, certificate checks, and redirects stay the same.

On a slow connection, the first start can need more time than the 10-minute start period of the image health check.
The health check then shows `unhealthy` while the download continues.
Fill the model cache before the first start (`--fetch-models`), or wait for the startup line.

Provider requests use the checked local cache.
The command gives the same cache path to the provider factory and the turn detector through `ASSIST_LIVE_MODEL_CACHE`.

Each STT turn uses the two turn detector models, also with a cloud STT provider.
When startup selects a cloud STT provider but does not select Parakeet or Piper, it checks or downloads only these two models.
In that case, a download error or a changed model does not stop the server.
The HTTP service and the selected TTS continue.

If a turn model is missing or has a different SHA-256 value, health reports `capabilities.stt=false`.
The `unavailable.stt` health field then gives `turn_models_missing` or `turn_models_invalid`, and the integration uses its fallback STT.
The server sets this status one time at startup.
If you add or repair the turn models after startup, restart the server. Health then reports STT again.

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

## End rule

`ASSIST_LIVE_END_RULE` selects the turn detector rule. The app option is `end_rule`.
The default, `partial`, keeps the current rule.
Its early-end checks use the newest available `partial` transcript.

With `checkpoint`, Parakeet decodes turn audio to each 400 ms checkpoint (6400 samples).
Each decode starts when the server accepts that audio.
The detector uses the newest checkpoint at or before its current audio position.
It waits for that result, not an older result or a sent `partial` transcript.
Only the audio and checkpoint text set the Smart Turn test positions.
A short stream stop does not divide or fill a voice detector window.

An explicit input end can finish a shorter window.

Each checkpoint has a 1 s limit, with decode admission and queue wait.
The session applies this limit, also to checkpoint calls without the turn detector.
A missed limit stops the turn with `stt_failed`, without a different satisfactory end sample.
The 15 s wall limit gives the same failure if the audio-based end is not available.
The counters are `assist_live_checkpoint_deadline_failures_total` and `assist_live_checkpoint_wall_limit_failures_total`.
Each counter counts turns with `stt_failed`, not checkpoint requests.

The two rules keep the command-start guard and the end-sample cut for the `final` decode.
Without a command start, the two rules send an empty `final` without a `final` decode.
With `checkpoint`, transcript replacement uses the checkpoint at the end sample.
The three early-end conditions and detector limits do not change.
The [transcript guard](profiles.md) accepts object-final particles but keeps turns open after opening verb-particle pairs.
This rule applies to the two end rules (see `docs/design.md` in the integration repository).

The checkpoint decodes also supply UI `partial` transcripts in sample order.
The server does not run a second set of UI decodes.
It publishes no `partial` before command start, for an unprocessed checkpoint position, or after the end decision.

It keeps at most two unfinished checkpoint requests for each turn and applies input backpressure at that bound.
This rule adds a greedy decode at each 400 ms prefix, also before command start.
`final` decodes run before queued checkpoint decodes.

Across all turns, checkpoint decodes can hold at most three of the four decode admission slots.
This keeps one slot for a `final` request. Other `final` requests can fill the pool.

At the end decision, the server cancels queued checkpoint work for positions after the end sample and releases its slots. Then it sends the end decision.
An active decode keeps its slot until the call finishes.
Partial-mode admission and queue order stay unchanged.

`assist_live_stt_final_admission_refusals_total` counts refused `final` admission requests in the two modes.
A refusal gives the current `stt_failed` error, without a new message field or a different endpoint.

An unknown rule stops startup with an error that names `ASSIST_LIVE_END_RULE`.
`checkpoint` is available only with Parakeet. With a different selected STT provider, startup also names that provider in the error.
The server also accepts disabled STT (`none`).
The configured factory selects one STT provider for all turns, not a provider for each satellite.

In the two modes, `assist_live_end_decision_wait_seconds` records the time from end-sample arrival to end decision.
This histogram has `end_rule` as its only Prometheus `label`. It contains no transcript text or turn identifiers.
See [the public design, end-of-turn and STT fallback](https://github.com/legion-works/assist-live/blob/main/docs/design.md#end-of-turn-and-stt-fallback).

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
When no command starts, the server sends the same message without a plug-in call.
If a plug-in is set, the server records `reason=no_command` for this case.
Audio stays only in the bounded turn buffer and request memory. The server does not store it.

A stored speaker name uses NFC. It must have 1 to 64 characters and at most 256 UTF-8 bytes.
It must have no control characters. It must not start or end with whitespace.
Only the `name` key of a `speaker_result` event can contain this display name.

The server drops an invalid name without truncation. A counter for each cause counts each drop.
Names do not go into log messages, errors, or metric dimensions. The event-retention policy also applies to names.

The server stores a `final` transcript only when the server and the turn each set `record_transcripts=True`.
The two options default to `false`. The server does not store `partial` transcripts.
If the transcript makes the timing record larger than 16,256 bytes, the server keeps the record without the transcript and sets `data.transcript_omitted: true`.
This limit does not change the `final` message.

The `/metrics` counter `assist_live_transcript_omitted_total` counts these stored records for the token's satellites.
The counter starts at `0` when the server starts.

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
| `ASSIST_LIVE_STT_HINT_BONUS` | `1.0` | none | Set the last Parakeet transcript's name bonus, from `0` through `4`. |
| `ASSIST_LIVE_END_RULE` | `partial` | `end_rule` | Select `partial` or the Parakeet-only `checkpoint` rule. |
| `ASSIST_LIVE_TTS_PROVIDER` | `piper` | `tts_provider` | Select TTS, or `none` to disable it. |
| `ASSIST_LIVE_LOCAL_ONLY` | `true` | `local_only` | Refuse cloud providers. |
| `ASSIST_LIVE_RECORD_TRANSCRIPTS` | `false` | `record_transcripts` | Set the server transcript policy. |
| `ASSIST_LIVE_SPEAKER_URL` | empty | `speaker_url` | Set the speaker destination. |
| `ASSIST_LIVE_DOWNLOAD_MODELS` | `true` | `download_models` | Download missing selected startup files. |
| `DEEPGRAM_API_KEY` | empty | `deepgram_api_key` | Supply a runtime secret. |
| `OPENAI_API_KEY` | empty | `openai_api_key` | Supply a runtime secret for OpenAI STT. |
| `OPENAI_TRANSCRIPTION_DELAY` | `low` | `openai_transcription_delay` | Select `minimal`, `low`, `medium`, `high`, or `xhigh`. |
| `ELEVENLABS_API_KEY` | empty | `elevenlabs_api_key` | Supply a runtime secret. |
| `ELEVENLABS_VOICE_ID` | empty | `elevenlabs_voice_id` | Set the configured TTS voice. |
| `ELEVENLABS_VOICE_NAME` | voice identifier | `elevenlabs_voice_name` | Set its display name. |
| `ELEVENLABS_VOICE_LANGUAGE` | empty | `elevenlabs_voice_language` | Set its language tag, such as `en-US`. A value is necessary with a configured voice. |
| `ELEVENLABS_ENABLE_LOGGING` | `true` | none | Set `false` to request ElevenLabs Zero Retention Mode. |
| `ASSIST_LIVE_DATA` | `/data` | none | Set the data directory. |
| `ASSIST_LIVE_MODEL_CACHE` | data directory plus `cache` | none | Set the local model cache. |
| `ASSIST_LIVE_HOST` | `0.0.0.0` | none | Set the server address. |
| `ASSIST_LIVE_PORT` | `8099` | none | Set the TCP port. |
| `ASSIST_LIVE_ASSET_MANIFEST` | packaged manifest | none | Set the manifest for `--fetch-models` only. |

Boolean environment values must be `true` or `false`. The data directory must be writable by the service user.

An empty `ELEVENLABS_VOICE_ID` keeps request-selected voices and reports no voice list in health.
An unset or empty `ELEVENLABS_VOICE_NAME` uses the voice identifier.
The server refuses a voice identifier, name, or language that contains the API key or key-shaped text. The error names the field, not the value.

`ELEVENLABS_ENABLE_LOGGING=false` sends `enable_logging=false` with each ElevenLabs request.
The provider documents that only select enterprise customers can use Zero Retention Mode. See [cloud providers](providers/cloud.md).
If ElevenLabs is the configured TTS provider, an invalid value stops the server with `invalid ELEVENLABS_ENABLE_LOGGING option`. The server does not read this setting for other TTS providers. The diagnostic does not contain the value.

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

# Protocol version 1

The project is experimental and has no release.
UDP and WebSocket are the two candidates for device timing.
The transport selection stays open until a device trial supplies sufficient data.
The two transports have no preferred status.

The public [design record](https://github.com/legion-works/assist-live/blob/main/docs/design.md) states:
"A new boot ID starts a new sequence and clock window."
It also states: "The first message selects the protocol version and functions."

## Credentials and identity

Each satellite has an anonymous `satellite_key`.
The registry maps an anonymous firmware `device` key to that satellite key.
The device and satellite keys can be different. Each device and satellite has one mapping only.

HA entity names stay in a local mapping. An entity rename does not change the key.
The registry refuses duplicate mappings and unknown devices.
It does not use arrival order or display names to select a satellite.

An integration token gives access to health, audio, events, queries, and exports for its listed satellites only.
Send it in the `Authorization: Bearer <token>` header, not in a URL.
Device HMAC secrets give access to signed telemetry only.
They cannot open audio or read records. Integration tokens cannot sign device telemetry.

No response includes a device secret. Bindings contain only anonymous satellite keys.


The local provisioning command writes a credential file with mode `0600` and prints nothing:

```sh
uv run python -m assist_live_server.provision --store "$CREDENTIAL_FILE" --satellite "$SATELLITE_KEY"
```

Keep that file external to the repository.
Copy the device secret to external ESPHome `secrets.yaml` through a local procedure with access control.
The integration receives only its different access token.

Use `Registry.rotate` to revoke the previous device secret before the new secret becomes active.
Keep replay state until that revocation. Do not copy credentials into logs.
Use TLS or an isolated network. HMAC authenticates events but does not encrypt them.

## Device events

Each UDP datagram or WebSocket message contains one UTF-8 JSON object.
The WebSocket route is `/v1/device`.
The two adapters call the same `TimingIngress.accept` path.

The device WebSocket has a global limit of 64 concurrent connections for each server instance, across all source addresses.
The server refuses a connection above this limit with HTTP `503`, before the WebSocket upgrade.
That refusal also closes the HTTP transport. The HTTP connection does not stay open.

Each connection must send its first authenticated device event in less than two seconds.
If it does not, the server sends a close frame with code `1008` and `reason` `event timeout`.
An authenticated connection does not use that first-event deadline for subsequent events.

Each authenticated device can hold a maximum of four slots. A connection cannot change its device identity.
A device connection above that limit closes with code `1008` and `reason` `event refused` after its first authenticated event.
Each event has a two-second acceptance-result deadline.
A timeout sends a close frame with code `1008` and `reason` `event timeout`.

Ping and pong frames do not restart the authentication deadline.
The server gives a peer 100 ms to reply to a close frame, then closes the transport and releases its connection slot.

One refused event closes `/v1/device` with code `1008` and `reason` `event refused`.
A slot-cap refusal and a bad-event refusal send the same close frame.
A frame above 4,096 bytes closes it with code `1009`.
The firmware must make a new connection for subsequent events after a close.
UDP has no connection close or error reply. The listener drops a refused or oversized datagram and continues with subsequent datagrams.

`create_app(..., udp_address=None)` opens no UDP listener by default.
Set `udp_address` to an explicit address and port to enable the UDP adapter.
The server gives the bound address through the typed `UDP_ADDRESS` key.
Tests use a loopback address and port `0` for an isolated trial.
Transport selection stays open. The WebSocket route and configured UDP listener use the same acceptance task.

Necessary fields:

| Field | Type |
|---|---|
| `version` | Integer `1` |
| `device` | Anonymous satellite key, 1 to 128 ASCII bytes |
| `boot` | Random unsigned 64-bit integer |
| `seq` | Unsigned 64-bit integer, starts at 0 for each boot |
| `device_ms` | Unsigned 64-bit monotonic milliseconds |
| `stage` | `wake_detected`, `mic_open`, `playback_start`, `playback_end`, or `barge_in` |
| `data` | JSON object |
| `tag` | Lowercase hexadecimal HMAC-SHA256, 64 characters |

Identifiers contain only ASCII letters, digits, hyphens, and underscores.
The gateway refuses unknown fields, duplicate JSON keys, non-finite numbers, and incorrect tags.
If the event has `data.satellite`, it must equal the provisioned satellite.

The firmware does not supply a turn identifier.
The timing store then attaches events to the correct satellite.

The tag signs these fields, in this order:

1. `version`: one unsigned byte.
2. `device`: unsigned 32-bit byte length, then UTF-8 bytes.
3. `boot`: unsigned 64-bit integer.
4. `seq`: unsigned 64-bit integer.
5. `device_ms`: unsigned 64-bit integer.
6. `stage`: unsigned 32-bit byte length, then UTF-8 bytes.
7. `data`: unsigned 32-bit byte length, then canonical JSON bytes.

All integers use network byte order, high byte first.
Canonical JSON sorts object keys, uses compact separators, keeps UTF-8 characters, and refuses NaN or infinity.
The `tag` field is not part of the signed bytes.
Do not use pipe-separated fields, comma-separated data, hexadecimal boot strings, or native integer byte order.

### Canonical JSON bytes for firmware

The canonical `data` encoding is the UTF-8 output of Python `json.dumps` with these options:
`sort_keys=True`, `separators=(",", ":")`, `ensure_ascii=False`, and `allow_nan=False`.
This encoding is not RFC 8785. Do not use a different JSON canonicalization algorithm.

- Object keys sort by Unicode code point. Array elements keep their order. Duplicate keys are not permitted.
- The output has no spaces between values and no byte-order mark. Input whitespace does not change the signed canonical bytes.
- Strings keep their Unicode values without normalization. Non-ASCII characters use UTF-8, not `\u` encoding.
- A quotation mark uses `\"`. A backslash uses `\\`. A slash stays `/`.
- Control characters use `\b`, `\t`, `\n`, `\f`, and `\r` for `U+0008`, `U+0009`, `U+000A`, `U+000C`, and `U+000D`.
- Other characters from `U+0000` to `U+001F` use `\u00xx` with lowercase hexadecimal digits. UTF-8 cannot encode lone Unicode surrogates.
- Booleans and null use `true`, `false`, and `null`.
- Integers use decimal digits, with no positive sign or padding. Integer `-0` becomes `0`.
- Numbers with a decimal point or exponent use IEEE-754 binary64 and the shortest `repr()` value from Python.
  Finite floats keep `.0` when the fixed-point value is an integer.
- The exponent encoding uses lowercase `e`, a sign, and a minimum of two digits. Floating-point `0.0` and `-0.0` stay unchanged.

Examples of canonical numeric values are `[0,1.0,-0.0,1e-07,1e+20]`.
Firmware must use integers or strings for timing metadata if it does not use the same float encoding.
The canonical-vector tests and the string/number test supply byte-level examples.

The server uses HMAC and `hmac.compare_digest` for known and unknown device keys.
An unknown key uses the constant `DUMMY_DEVICE_SECRET`. A dummy-key tag cannot authenticate an unknown device.

All device, control, and HTTP JSON paths have a maximum nesting depth of 32, with the root object at depth 1.
Delimiters in strings do not increase the depth. Byte-size limits also apply.

### Replay acceptance

The adapter captures UTC arrival time, then checks size, JSON, tag, mapping, and queue capacity.
It does not wait for queue space. A full queue drops the event and increases a counter.
Queue admission is not replay acceptance.

One task consumes the bounded queue and sends each replay check to one dedicated SQLite writer.
The writer checks and updates the replay window before it releases the event.
It accepts an unseen reordered sequence in the current boot's replay window.
It records missing sequences independently from duplicates.
A new boot retires the previous boot.
The gateway also refuses unseen sequences from a retired boot.

An unexpected acceptance error refuses the current event, increases an error counter, and makes a credential-free log entry.
The acceptance task continues after an interval, from 100 ms to a maximum of five seconds if errors continue.
The maintenance task also counts and logs errors and continues with a bounded interval.
Shutdown refuses new queue entries, counts discarded events, and resolves pending acceptance results as `false`.

The queue record includes `new_boot` so the clock consumer can start a new window.
It also includes `arrival_ns`, the UTC nanoseconds when the adapter receives the event, before the queue wait.

`REPLAY_WINDOW=4096` sets the anti-replay window width.
The algorithm follows the sliding-window rule in [RFC 4303 section 3.4.3](https://www.rfc-editor.org/rfc/rfc4303#section-3.4.3).
[RFC 6479](https://www.rfc-editor.org/rfc/rfc6479) specifies the window and bitmap model.
Each active boot keeps the highest accepted sequence and a 4,096-bit bitmap.

A new high sequence moves the window forward. A large jump clears the bitmap without a large allocation.

There is no event-count limit. Sequential traffic can continue for the full secret lifetime.

In the window, the gateway accepts an unseen sequence and a set bit identifies a duplicate.
Below the window, an event has the `stale` class, not the `duplicate` class.
The two counters operate independently. Missing-sequence counts use arithmetic, not an allocation for each missing number.

The device also keeps a set of all boot identifiers seen with the current secret.
The set increases only on a new boot, not on each event.
`MAX_BOOT_IDS=65536` limits that set.
This equals about 179 years at one reboot each day.
At this limit, the gateway refuses new boots and makes a credential-free error log.

The operator must change the secret to clear the set.
The active-boot traffic can continue at this limit.

`ReplayStore` supplies the synchronous `load_replay`, `save_replay`, and `clear_replay` hooks in the SQLite writer.
The server loads replay state before it starts the acceptance task.
Without a replay store, the device WebSocket refuses the upgrade with HTTP `503` and health is `false`.
For a configured UDP listener, replay hooks are necessary before startup. `SQLiteStore` supplies these hooks automatically.

The writer commits the replay update and its device-event row in one SQLite transaction.
An unsuccessful transaction refuses the event and keeps the previous in-memory window.
The unique `(device, boot, seq)` key supplies a second duplicate check.

The writer owns the only SQLite write connection. SQLite uses WAL with `synchronous=NORMAL`.
Checkpoints also run in the SQLite writer, not in the audio event loop.
An electrical supply failure can remove the last transactions.
Each transaction keeps or removes its replay update and event together.

If SQLite removes the two rows, acceptance of that event again adds the removed row. It does not duplicate a row that SQLite keeps.
Use `await store.rotate(registry, device, secret)` to change credentials in an active service.
This method clears the previous secret's replay rows in the SQLite writer.
It clears replay state before it writes the new credential. A replay-clear error keeps the previous credential and its file unchanged.

Credential-write errors put the previous in-memory credential and replay state back. The `rotate` method refuses a secret that a different device uses.

`Registry.rotate` refuses all calls. Only `SQLiteStore.rotate` can change a device secret.
The registry must use that store's replay connection.

A process crash can occur after the credential file changes but before the SQLite transaction commits.
After restart, the file has the new secret, but the previous replay window and device-event rows can stay.
This state refuses previous-key events and can refuse correct new-key events. It does not accept duplicates.

For recovery, stop device traffic and load the credential file and store.
Do not delete the database or clear replay state with the active secret.
Use `await store.rotate(registry, device, fresh_secret)` with a different, new secret.
Wait for the method to complete, then provision that secret to the device and resume traffic.

If the process stops again, do these steps again with a different new secret.
Keep credentials out of logs and command arguments.

Queue entries keep an immutable credential-generation snapshot. The writer refuses an entry from a revoked generation.
The [SQLite WAL documentation](https://sqlite.org/wal.html) gives the checkpoint and `synchronous` rules.

The credential file keeps credentials only. It does not supply replay persistence.
An in-memory replay store is for isolated tests only.

`protocol/device-vectors.json` has SHA-256:

```text
b74cf28042f22e9bd85520a864f335e0f61fddbd99559ce458f78cd5b540bc89
```

The firmware copy must have this same digest.
The keys in this file are public test values, not usable credentials.

## Control and audio

The `/v1/audio` WebSocket checks the integration token.
The `/v1/audio` and `/v1/events` routes share 64 connection slots, with a maximum of 16 for each integration token.
The server refuses upgrades above these limits with HTTP `503`. A closed connection releases its slot.

Open a turn first with `POST /v1/turns` and a complete `TurnOpen` record.
The initial control message selects version `1`, mode `stt` or `tts`, and functions.
The `capabilities` functions are `stt`, `tts`, `events`, and `speaker_result`.
The selected mode must occur in the `capabilities` list.

TTS can also supply `voice`.
The server replies with `negotiated` before it accepts audio.

```json
{"type":"negotiate","version":1,"turn_id":"01900000-0000-7000-8000-000000000001","mode":"stt","capabilities":["stt","speaker_result"]}
```

`protocol/control.schema.json` specifies the control messages.
Each control message has `type`, `version`, and `turn_id`.
The version is an integer. The turn identifier is a UUIDv7.
The parser refuses unknown fields and mismatched turn identifiers.
`partial` and `final` contain `text`.

`end_decision`, `heartbeat`, and `end` have no more fields.
`error` contains `code`, without private error information.
The client sends `end` after its audio or text input ends.

Each audio frame has a constant 21-byte header:

```text
Offset  Bytes        Content
0       1            Version 1
1       16           Turn UUID bytes
17      4            Unsigned sequence, network byte order
21      2 to 32,000  PCM bytes, length divisible by 2
```

Audio is mono signed 16-bit little-endian PCM at 16 kHz.
Sequences start at 0 in each direction.
The receiver refuses missing sequences, duplicates, and out-of-order audio sequences.
Device telemetry has different sequence rules because events can come out of order.

TTS sends a `format` message before audio, with `codec=pcm_s16le`, `rate=16000`, `channels=1`, and `width=2`.
It sends `sentence_end` with an unsigned `sentence` index for fallback continuation.
The client gives PCM chunks to the integration without headers.

`speaker_result` and `final.text` are different messages. The speaker message can follow the transcript before stream close:

```json
{"type":"speaker_result","version":1,"turn_id":"01900000-0000-7000-8000-000000000001","name":"test-speaker","distance":0.25,"destination":"local"}
```

This message does not change transcript bytes.
Destinations are `local` or `cloud`.
Without a speaker plug-in, the streaming STT gateway sends an empty `name`, distance `0.0`, and destination `local`.
This result does not identify a speaker.

Timing records for STT, TTS, fallback, and speaker ID use `data.part`, `data.provider`, and `data.destination` for the actual used service.

The backend supplies these records.
The protocol does not select a provider or tune the `safe` and `fast` settings.

### Streaming STT

`create_app(..., audio_gateway=AudioGateway(provider_factory))` selects the streaming STT path.
The factory receives the configured provider ID and makes one `STTSession` before the first audio frame.
The default ID is `parakeet`. Configuration can select a different ID without a second audio path.
Load a local provider before the service starts, then supply its `session` method through the factory.

Each turn has its own detector and session. Detection and STT decode run at the same time.

The detector's first VAD onset calls `STTSession.speech_started()` one time. There is no second VAD.

The gateway flushes received detector samples at a stream stall without adding audio time.
It stops input at the endpoint, at 15 seconds of audio, or 15 seconds after input starts.
It calls `finish()` one time after input stops.

The server sends `end_decision`, one `final`, then `speaker_result` before `end` and stream close.
A provider failure sends one terminal `error` with code `stt_failed`, not a `final`.
The server does not retry or use fallback after a `final`.
The `final.text` value stays unchanged. Text from `partial` is not a complete result.

The store receives `speech_start`, `end_candidate`, `end_decision`, `stt_partial`, `stt_final`, and `speaker_result` timing events.
These events use server UTC time and `quality=measured` at the server observation, not a device sample timestamp.
A candidate starts when the received time without voice is at the detector profile's minimum.
New voice starts a new candidate interval.

The store keeps `stt_partial` timing events without transcript text. The two transcript settings control `stt_final` text only.
Disconnect cancels the session and awaits the gateway tasks.
CPU work can finish in its `thread` after cancellation, but it cannot write a socket or store event.

## Heartbeats and limits

Each open audio turn has one heartbeat task, with an interval of 100 ms.
The task also runs while the backend waits for a result.
The client stops after 600 ms without a message.

The streaming STT gateway stops input after 600 ms without input, flushes received samples, and finishes recognition.
It does not add audio samples during that wait. The heartbeat continues during input and result waits.
The legacy backend path closes after 600 ms without input until it gets `end`.

The TTS text path has a different limit of 30 seconds without text, before its first part and between parts.
Heartbeats continue during text waits. The client's 600 ms server-message deadline does not change.

Close cancels and awaits the heartbeat, input, output, and client sender tasks.

| Path | Maximum message or record | Queue |
|---|---|---|
| Device UDP or WebSocket | 4,096 bytes | 128 events by default, at most 4,096 |
| Audio WebSocket | 32,021 bytes | 16 input chunks |
| JSON control or HTTP body | 16,384 bytes | Bounded read, no queue |
| Subscription or export record | 16,384 bytes | One record at a time |

WebSocket compression is off. The frame parser has a set message limit before JSON decoding.
The audio maximum is inclusive. The server accepts a correct 32,021-byte frame.
A 32,022-byte frame closes with code `1009`.

The UDP stack supplies one bounded datagram. The adapter checks its length before JSON decoding or queue allocation.

HTTP and export readers check each bounded chunk before they extend a buffer.
Full device queues refuse new events without a new replay entry.
Socket writes wait for flow control. No route makes an unbounded queue.

## Records and read routes

`TurnOpen` contains `turn_id`, `satellite`, `turn_type`, `parent_turn_id`, `conversation_id`, and `record_transcripts`.
The last field defaults to `false`.
`TimingEvent` contains `turn_id`, `satellite`, `source`, `stage`, `t_utc_ns`, `quality`, and `uncertainty_ms`.
Optional fields are `t_device_ms`, `seq`, and `data`.
Quality stays `measured`, `proxy`, or `estimate`. Estimates must have a non-negative uncertainty.

The device clock consumer uses a different clock window for each boot.
Its minimum arrival offset comes from the last 60 seconds or 50 events.
Uncertainty is the median offset minus the minimum. The timing store calculates these values.
The protocol does not change a playback proxy to a measured sample time.

The turn query keeps the six `TurnOpen` fields and adds `status` and `events`.
These two fields are optional in the client `TurnRecord` type for older store implementations.
The SQLite server supplies the two fields. `events` contains the timing objects in stored `event_id` order.
These objects equal the `event` values in subscriptions and exports. Quality and uncertainty do not change.

The status rules are:

- `open`: no closing event, or a closing event has missing `stage` values and its five-second reorder window stays open.
- `complete`: the record contains `playback_end` and all necessary `stage` values.
- `incomplete`: necessary `stage` values are missing when the reorder window expires, the turn gets to 120 seconds, or a restart or supersession ends it.

Necessary `stage` values for `tts_only` are `tts_first_text`, `tts_first_byte`, `playback_start`, and `playback_end`.
Voice turns also use these necessary values: `wake_detected`, `mic_open`, `speech_start`, `end_decision`, and `stt_final`.
The `stt_partial`, `speaker_result`, and `end_candidate` events are optional.
The server keeps unattached device events for five seconds from arrival, for the same satellite only.
Expired events keep their quality and uncertainty, get `stage=unmatched`, and keep `data.original_stage`.

| Route | Contract |
|---|---|
| `GET /health` | Version and Boolean health, with authentication |
| `GET /v1/bindings` | List of approved anonymous satellite mappings |
| `POST /v1/turns` | Open a turn in the store |
| `POST /v1/event` | Append an integration or HA-state timing event |
| `GET /v1/turns/{turn_id}` | Complete turn for an approved satellite |
| `GET /v1/export?satellite=...&since_utc_ns=...` | JSON Lines, inclusive UTC-nanosecond filter |
| WebSocket `/v1/events` | Stored event subscription |

The first subscription message is `subscribe`, with `version`, `satellite`, and `after_event_id`.
The server sends only event identifiers greater than that cursor.
Each envelope has `version`, a monotonically increasing stored `event_id`, and a complete `event` object.
Reconnect with the last consumed identifier. The client does not change that cursor automatically.
Subscriptions, queries, exports, audio, and event writes apply the same satellite authorization.

`TurnStore` specifies the store interface. `SQLiteStore` supplies the SQLite implementation with 30-day retention by default.
Retention uses server storage time, not a device-supplied timestamp. Replay state stays for the secret lifetime.
The maintenance task checks retention each minute, not in event-acceptance transactions.

`GET /metrics` supplies Prometheus text with the integration token's satellite access checks.
Ordinary diagnostics contain counts only, without audio or transcripts.
Without a store, record routes give a service-unavailable response.
Without a backend, audio gives a service-unavailable response and health is `false`.

Transcript storage is on only when server policy and `TurnOpen.record_transcripts` are the two `true` values.
The two settings default to `false`.
Only the optional `data.transcript` field can keep selected transcript text.
Do not put audio or transcript text in other timing-data fields or ordinary diagnostics.

## Dependency sources

Access date: 2026-10-25 (UTC).

Server and client use the same runtime pin, `aiohttp==3.14.5`.
[PyPI metadata](https://pypi.org/pypi/aiohttp/3.14.5/json) declares Python `>=3.10`.
Its file list includes CPython 3.14 manylinux wheels for the two server architectures:

```text
aiohttp-3.14.5-cp314-cp314-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl
SHA-256 94684b879ac1d71e4238850c99b62dc1b28d9086b156a2555f082010b85a865c
aiohttp-3.14.5-cp314-cp314-manylinux2014_aarch64.manylinux_2_17_aarch64.manylinux_2_28_aarch64.whl
SHA-256 1eb8167961ec4dfcc8cb9dd50bd0ee72519f7ef496be95203e49e27b01618382
```

The [aiohttp server source](https://docs.aiohttp.org/en/stable/_modules/aiohttp/web_ws.html) documents `max_msg_size`, compression control, and bounded socket writes.
The [client documentation](https://docs.aiohttp.org/en/stable/client_reference.html) specifies session ownership and WebSocket message limits.

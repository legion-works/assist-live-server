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
Use that same key in the firmware `device` field and the turn `satellite` field.
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
The host can use `UDPTransport` with an asyncio datagram endpoint.

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

The gateway checks size, JSON, tag, mapping, replay state, and queue capacity before it accepts an event.
It accepts an unseen reordered sequence in the current boot's replay window.
It records missing sequences independently from duplicates.
A new boot retires the previous boot.
The gateway also refuses unseen sequences from a retired boot.

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

`ReplayStore` supplies the synchronous `load_replay`, `save_replay`, and `clear_replay` hooks.
The timing store must supply durable replay state through these hooks before the service accepts device traffic.
The gateway commits replay state through the hook before it adds an event to the queue.
An unsuccessful commit refuses the event and does not change the in-memory window.

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

Timing records for STT, TTS, fallback, and speaker ID use `data.part`, `data.provider`, and `data.destination` for the actual used service.

The backend supplies these records.
The protocol does not select a provider or tune the `safe` and `fast` settings.

## Heartbeats and limits

Each open audio turn has one heartbeat task, with an interval of 100 ms.
The task also runs while the backend waits for a result.
The client stops after 600 ms without a message.
The server stops input after 600 ms without a message until it gets `end`.
Close cancels and awaits the heartbeat, input, output, and client sender tasks.

| Path | Maximum message or record | Queue |
|---|---|---|
| Device UDP or WebSocket | 4,096 bytes | 128 events by default, at most 4,096 |
| Audio WebSocket | 32,021 bytes | 16 input chunks |
| JSON control or HTTP body | 16,384 bytes | Bounded read, no queue |
| Subscription or export record | 16,384 bytes | One record at a time |

WebSocket compression is off. The frame parser has a set message limit before JSON decoding.
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

No timing store ships in this protocol module. `TurnStore` specifies the necessary interface.
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

# Local voice recognition

The project is experimental and has no release.

## Compatible mode

The local default is `nvidia/parakeet-tdt-0.6b-v2` through `onnx-asr==0.12.0`.
This is compatible mode, not streaming.
The runtime uses int8 models, `intra_op_num_threads=4`, `inter_op_num_threads=1`, and the NumPy preprocessor.

Each session accepts mono 16 kHz little-endian int16 PCM through `await push(pcm)`.
Call `await partial()` after each audio push.
A new `partial` starts only after 400 ms or more of new audio since the previous decode snapshot.
The decoder reads the complete buffer each time. It does not supply previous samples to a streaming cache.
Less frequent polling adds time to this interval.

The [selection record](../research/q6-streaming-stt.md) reports a median first `partial` of 430.98 ms on its tuning set and 431.43 ms on its held-out set.
That time starts at audio start and includes decode cost.
It usually measures filler text, not the first correct command word.
The interval itself can add a maximum of 400 ms before a decode starts.
The selection record is not a target-device or live-microphone result.

`await finish()` decodes the complete buffer, with the first sample and the short last chunk.
It does not use only the last interval. Subsequent finish calls give the same result without one more decode.
An empty session finishes with an empty string.

## Voice onset

`partial()` gives an empty string until the caller calls `speech_started()`.
It does not decode before that signal.
The gateway must connect that signal to the turn detector's VAD.
Without that connection, `partial` results stay empty.
`finish()` decodes all accepted audio, with or without an onset signal.
The gate does not remove the initial audio or the first word.

An external replay used all 250 timer recordings and the pinned Silero VAD.
It used 512-sample frames and a Silero threshold of 0.5, as the turn detector does.
The first-word time came from the automatic corpus `alignment_words` field, not a manual onset record.
The ungated side used the frozen `partial` trajectories from the selection record.

| Nonempty partials before the aligned first-word time | Ungated | Onset gate |
|---|---:|---:|
| `partial` count | 319 | 12 |
| Recordings with these results | 229/250 | 12/250 |

Silero found an onset in all 250 recordings.
The 12 remaining `partials` follow a Silero signal before the aligned first-word time.
The onset gate cannot correct a VAD signal before voice.
No threshold changed after the held-out test.

## Limits and cancellation

The audio buffer has a 15 s limit (480000 bytes).
A push above the limit gives `AudioLimitError` and accepts none of that push.
The accepted audio stays available to `finish()`. The session does not drop its start to accept a longer turn.

One process-wide executor has one worker.
This keeps the shared decoder sequential and keeps CPU inference off the event loop.
The executor accepts a maximum of four tasks, with the active task in that count.
A request above this limit gives `STTBusyError`. The caller can retry when capacity is available.

Queued finish decodes run before queued `partial` decodes from other sessions.
The executor keeps input order in each class.
It does not stop an active native decode.
`partial` work can wait while finish work is pending.

Each session has a maximum of one active `partial` decode and one pending update.
Ticks during a decode merge into that pending update.
The next decode uses the newest complete buffer, not each individual tick.
Audio pushes do not wait for a `partial` decode.
`finish()` stops new pushes and waits for the active decode.
It decodes the complete buffer and removes the pending `partial`.

`cancel()` closes the session and clears its audio and text.
Subsequent operations give `STTClosedError`.
Task cancellation also closes the session.
An active native decode cannot stop immediately, but its result cannot become a transcript.
Its executor slot stays occupied until the worker finishes.

## Compatible-mode cost

The short-recording test used a FIFO queue, before finish-first scheduling, and a warm shared model.
It used 24 different timer recordings for each configuration.
Each configuration had 213 interval decodes and 24 finish calls.
The table gives wall time for each call, with executor queue time, in milliseconds.
Percentiles use NumPy's linear interpolation. A p99 from 24 finish calls is a small-sample estimate.

| Concurrent sessions | Interval p50 | Interval p99 | Finish p50 | Finish p99 | Heartbeat lag p99 |
|---:|---:|---:|---:|---:|---:|
| 1 | 56.77 | 100.92 | 78.68 | 114.20 | 1.90 |
| 2 | 107.86 | 179.27 | 136.37 | 225.93 | 1.89 |
| 4 | 215.29 | 463.82 | 260.69 | 401.83 | 3.55 |

The heartbeat interval was 100 ms. With four sessions, interval p99 is more than the 400 ms audio interval.
The pending-update bound prevents a decode queue from each missed tick.
These replay calls ran without real-time pacing. They do not show a sustained real-time capacity.

The host used an AMD Ryzen AI Max+ 395 CPU, x86-64 Linux, with 32 logical CPUs.
The one-minute host load rose from 12.17 to 13.29 during the short-recording cost test.
Other local checks ran during part of that test.
Numerical libraries used one `thread`. ONNX used `intra_op_num_threads=4`.

### Cost at the 15 s limit

The limit test used 100 sessions for each configuration, with a warm shared model.
Each session decoded one 15 s interval snapshot and then the same complete buffer at `finish()`.
The next two tables record the FIFO queue before finish-first scheduling.

The source recording was `tas-v1-7c3006048df48ee90b38`, with the same audio in sequence to make 15 s.
Its source WAV SHA-256 was `d8f009f2b18e5302f175326bbca810463dc76351cb2043a3d0a753e575007bad`.
The source has the `CC0-1.0` license. This is a compute-cost test, not a recognition-quality test.

Wall time with queue time, in milliseconds:

| Concurrent sessions | Interval p50 | Interval p99 | Finish p50 | Finish p99 | Heartbeat lag p99 |
|---:|---:|---:|---:|---:|---:|
| 1 | 226.48 | 263.71 | 225.51 | 282.07 | 2.08 |
| 4 | 570.39 | 926.56 | 901.48 | 968.36 | 1.82 |

Worker decode time without queue time, in milliseconds:

| Concurrent sessions | Interval p50 | Interval p99 | Finish p50 | Finish p99 |
|---:|---:|---:|---:|---:|
| 1 | 226.21 | 263.41 | 225.26 | 281.82 |
| 4 | 224.57 | 250.34 | 223.37 | 255.99 |

The heartbeat interval was 100 ms, with 458 observations for one session and 453 for four sessions.
The one-minute host load was 6.82 to 8.95 for one session, then 8.95 to 8.49 for four sessions.
The host and runtime settings were the same as the short-recording test.

Full-buffer decode cost increases with audio length. Four long sessions cannot each sustain a 400 ms decode interval on this executor.
Tick coalescing limits pending work. It does not remove this latency cost.

### Finish-first scheduling at the 15 s limit

The four-session test then ran again with the same 100-session workload and finish-first scheduling.
Only this configuration ran again. The one-session measurements stay unchanged.
The measured finish p50/p99 fell from 901.48/968.36 ms to 496.30/555.33 ms.
The tradeoff is a longer wait for `partial` work.

| Four sessions | Interval p50 | Interval p99 | Finish p50 | Finish p99 | Heartbeat lag p99 |
|---|---:|---:|---:|---:|---:|
| FIFO wall time with queue time | 570.39 | 926.56 | 901.48 | 968.36 | 1.82 |
| Finish-first wall time with queue time | 844.61 | 1636.86 | 496.30 | 555.33 | 1.74 |
| Finish-first worker time without queue time | 245.96 | 281.56 | 245.03 | 282.38 | Not applicable |

Times are milliseconds. The heartbeat had 498 observations at 100 ms intervals.
The one-minute host load rose from 6.23 to 10.68.
These different tests did not keep background host load constant.
This test does not give a target-device or sustained-capacity result.

One executor worker and `intra_op_num_threads=4` keep the benchmark's runtime settings without concurrent calls into the shared model.
The host has 32 logical CPUs. Most CPU capacity stays available for the event loop, turn detector, and other services.
The heartbeat measurements check event-loop response with this workload. They do not show that these settings are optimal on other hosts.

## Model files and streaming status

`ParakeetProvider.load(cache)` loads only local files with the manifest's pinned SHA-256 values.
It checks the four model files against the benchmark and download records before it loads the runtime.
The shared turn-model loader supplies the cache-boundary and hash checks.
There is no provider download path. Keep model files external to the repository.
See [third-party notices](../../THIRD_PARTY_NOTICES.md) for the CC-BY-4.0 attribution.

The provider registry lists `parakeet` in `local_compatible` mode.
It lists no provider in `local_streaming` mode.
A request for a streaming default or a rejected candidate gives a named `ValueError`.
There is no Zipformer or FastConformer adapter.

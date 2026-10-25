# Local voice synthesis

The project is experimental and has no release.

## Engine and voice

The local provider is Piper 1.8.0, in-process on Python 3.14.
The voice is `en_US-lessac-medium`, with ONNX `intra_op_num_threads=4` and `inter_op_num_threads=1`.
The [selection record](../research/q7t-local-tts.md) gives the engine measurements and component rights records.

The manifest declares this voice. No other voice or runtime default can load.
An unknown voice gives an error by name. This also applies to an empty voice name.

`PiperProvider.load(cache)` reads the asset manifest and checks the SHA-256 of the two voice files before it loads ONNX Runtime.
Put `en_US-lessac-medium.onnx` and `en_US-lessac-medium.onnx.json` in the external cache directory.
The download URLs and SHA-256 values are in `assets/manifest.json`.
The runtime does not download a voice or use a network service.

## Text and audio contract

`synthesize(text, voice)` accepts an asynchronous text iterator and yields PCM bytes.
`stream(text, voice)` also yields the format and sentence-completion items.

The audio is mono signed 16-bit little-endian PCM at 22050 Hz. Each chunk has a maximum of 32000 bytes.
No WAV header is in the stream. Read the declared format before playback.
The route and typed client API use the same format and sentence controls. See [the protocol](../protocol.md).

Sentence assembly keeps text across input parts. Periods, question marks, and exclamation marks end sentences before whitespace.
The assembler waits for right context at a part boundary, so `3.` followed by `5` stays `3.5`.
Frequent English abbreviations, dotted initials, and decimal numbers do not cause a sentence break.

Closing quotation marks stay with their sentence. Input end flushes the last fragment, with or without punctuation.
An empty or whitespace-only input gives no audio or sentence-completion marker.
The unfinished fragment has a limit of 16384 characters.

Each complete sentence starts synthesis before the provider reads more text.
The worker makes PCM for that sentence, then the stream sends its chunks and a zero-based sentence-completion marker.
It does not wait for the complete reply. It does not make word-level audio with each text part.

## Executor and cancellation

One process-wide TTS worker serializes the shared Piper phonemizer and ONNX session calls.
The STT and TTS executors use different workers. TTS cannot wait behind queued STT `partials`.
CPU contention with STT can increase latency.

The TTS executor accepts a maximum of four sentences, with the active sentence in that count.
One more sentence gets `TTSBusyError`. The route sends `tts_failed`, so the consumer can use its fallback policy.
Backpressure on text sources prevents an unbounded inference queue.

Cancel the consumer task or close its iterator to cancel a session.
A queued sentence can cancel before inference. Native inference can finish the current sentence, but it starts no more sentences for that session.
Its slot stays occupied until inference completes. Each call keeps its output in an isolated result, not a shared audio queue.
The cancelled result cannot go into a different session.

## Fallback

Engines that make the full file before audio stay fallback engines only.
The usual local path uses sentence-level Piper output.
The legacy bytes-only client accepts only 16 kHz, 16-bit, mono PCM and refuses native Piper before audio.
Use `tts_stream()` for the 22050 Hz format and sentence markers.

A consumer that continues after some audio must start at the next complete sentence and use the same audio format.
If the fallback format is different, stop audio and report the TTS failure. Do not run the conversation again.

## Latency measurements

Date: 2026-10-25. Host: AMD Ryzen AI Max+ 395, x86-64 Linux, 32 logical CPUs.
The one-minute load at start was 13.61. The model was warm.
Each request used `Set a timer for five minutes.` and a trailing space to complete the sentence.

Time starts when the first sentence text is complete and ends at the first audio chunk, with executor queue time.
Concurrent requests started together. The source stayed open after that sentence, so this did not wait for input end.

| TTS sessions | STT sessions | Requests | First audio p50 (ms) | First audio p99 (ms) | Heartbeat lag p99 (ms) |
|---:|---:|---:|---:|---:|---:|
| 1 | 0 | 470 | 24.48 | 31.75 | 2.03 |
| 4 | 0 | 452 | 66.28 | 127.69 | 2.13 |
| 1 | 4 | 253 | 42.70 | 99.28 | 5.40 |
| 4 | 4 | 224 | 127.58 | 288.84 | 5.29 |

Each configuration ran for a minimum of 12 seconds and had 121 heartbeat samples at a 100 ms interval.
Percentiles use NumPy linear interpolation. These p99 values are small-sample estimates, not a latency guarantee.
The STT load used four Parakeet sessions with the pinned models, `intra_op_num_threads=4`, and its one-worker executor.
They decoded 400 ms input increments from synthetic Piper audio at 16 kHz and used four-second buffers without real-time pacing.
The two loaded configurations completed 114 and 104 STT interval decodes.

These results measure one host, not the target device or a sustained capacity limit.
Target hardware and a listening check stay open.

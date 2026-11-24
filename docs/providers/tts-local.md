# Local voice synthesis

The project is experimental and has no release.

## Engine and voice

The default local provider is Piper 1.8.0, in-process on Python 3.14.
The voice is `en_US-lessac-medium`, with ONNX `intra_op_num_threads=4` and `inter_op_num_threads=1`.
The [selection record](../research/q7t-local-tts.md) gives the engine measurements and component rights records.

The manifest declares this voice. No other voice or runtime default can load.
A provider call with an unknown or empty voice gives an error by name.
On the server route, an omitted or empty voice selects the configured default voice.

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

The assembler examines each input character one time and keeps boundary state across text parts.
It removes Markdown marks before synthesis but keeps sentence positions in the initial request. See [spoken-text rules](../protocol.md#spoken-text).

Abbreviation lookup uses a bounded token spelling, not the complete sentence prefix.
A constant-state check identifies dotted initials without a token-length limit. Opening quotation marks and brackets do not change abbreviation lookup.
The assembler makes text from the sentence characters only for output. It does not examine previous text again for one-character input parts.

On 2026-10-25, a 16383-character stream of `a. ` repetitions completed in 7.41 ms, with one character in each input part.
Median times at 4000 and 8000 characters were 1.806 ms and 3.648 ms. Their ratio was 2.020.

With a 100 ms heartbeat, p99 loop lag was 6.42 ms across 161 streams, with 1 ms between streams.
The maximum time for one stream was 9.99 ms. This test ran for 1.2 seconds on the development host.
These assembly times do not include Piper inference.

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

Engines that wait for the complete reply before audio stay fallback engines only.
The usual local path uses sentence-level Piper output.
The legacy bytes-only client accepts only 16 kHz, 16-bit, mono PCM and refuses native Piper before audio.
Use `tts_stream()` for the 22050 Hz format and sentence markers.

A consumer that continues after some audio must start at the next complete sentence and use the same audio format.
If the fallback format is different, stop audio and report the TTS failure. Do not run the conversation again.

## OpenAI-compatible HTTP provider

Select `openai_tts` for a different TTS server with an OpenAI-compatible TTS route.
See [configuration](../configuration.md#openai-compatible-tts-server) for its environment settings.
An address is local only when it agrees with the server's private-address rule.
Local-only mode refuses a public address at configuration load and before each connection.

The provider uses Piper's sentence splitter, Markdown rules, and positions in the initial request.
It sends one sentence at a time and waits for that response before it starts the next request.
It does not send a list as `input` and does not retry a sentence.

The TTS request is `POST /v1/audio/speech`, with JSON fields `input` and `voice`.
The response must be a complete RIFF/WAVE file with uncompressed, mono, signed PCM16 audio.
The provider reads the rate from the header and accepts source rates from 8000 through 192000 Hz.
It refuses a truncated or empty file before it sends audio from that sentence.
Different sentences of one reply can have different source rates.

Each response has a 2945536-byte limit: 60 s of 24 kHz PCM16 audio plus 64 KiB for headers.
The source audio also has a 60 s limit, independently of the response size. Audio stays in memory.
These limits give a short sentence sufficient space without a large allocation from an untrusted response.

The reply format is always mono PCM16 at 22050 Hz.
The provider sends this format after the voice check and before its first synthesis request.
An unknown voice gives only the error, without a format.
Thus the HA integration knows the format before it gets the first sentence audio.
The provider changes each source rate other than 22050 Hz to 22050 Hz, also 16000 Hz.
It changes each sentence on its own.

The legacy bytes-only client accepts only 16 kHz and refuses this provider before audio. Use `tts_stream()`.
Empty or whitespace-only text gives the format, no audio, and a usual end, without a synthesis request. The provider can refresh its voice list before that.

One fixed-width, Kaiser-windowed sinc resampler does upsampling and downsampling at the lower Nyquist limit.
It uses 128 input samples for each output sample, with float32 weights and sums.
The event loop does not run sample-rate conversion.

Each group of output samples contains a maximum of 256 samples and reads its full input overlap.
There is no complete floating-point input copy. Integer phase keeps sample positions the same at group boundaries and for different sentence lengths.
Conversion uses less than 8 MiB. This limit includes output PCM but not the source PCM or WAV.

The WAV, source PCM, and conversion memory together stay below 16 MiB for each active sentence.
These limits do not include other concurrent turns or HTTP-library buffers.

PCM chunks have a maximum of 32000 bytes, without a WAV header.
Each completed sentence has its initial request-text position for fallback continuation.

The voice route is `GET /v1/audio/voices`, with JSON `{"voices": ["voice-a", "voice-b"]}`.
The configured list of permitted voices filters this list. The default voice must be in the selected list.
The list refreshes a maximum of one time in 60 s, on a health request or a TTS request.

An unsatisfactory refresh keeps the last-known list. Before the first satisfactory fetch, configured voices stay available.
The health response supplies the voices and default voice, but no list-freshness field.

An optional bearer key comes only from its configured file.
An empty file is a named configuration error, not a request without a key.
An `http://` URL sends the bearer key in clear text. Provider startup gives one warning without the key or URL.
Use `https://` to encrypt requests.

Static errors contain no request headers, response bodies, or error data. The key does not go to health, metrics, or turn records.
Connect failures, non-200 responses, invalid WAV files, and request timeouts use the usual `tts_failed` path.
The HA integration then applies its fallback policy. A 22050 Hz reply can continue with the Piper fallback at the next complete sentence.

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

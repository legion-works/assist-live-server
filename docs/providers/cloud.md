# Cloud voice services

The project is experimental and has no release.
Only an explicit constructor call selects a cloud provider. Credentials alone do not select a cloud provider.
No cloud API request or API key use occurred.
Live checks: `NOT RUN (needs operator credentials and spend approval)`.

## Deepgram retention and data use

Source: [Your Data at Deepgram](https://developers.deepgram.com/trust-security/your-data.md).
Retrieved: 2026-10-25T17:28:49Z (UTC).

Source text from “What Deepgram stores”, row “Request metadata and usage logs”.
The default column and the `mip_opt_out=true` column contain this text:

```text
Retrievable for 90 days; summarized usage retained beyond 90 days
```

This is a retrieval window, not a guarantee that Deepgram deletes all metadata after 90 days.
The current source does not give a 90-day metadata retention limit.
The source does not give the date of a change to this statement.

Source text from the paragraph after that table:

```text
Request metadata and usage logs record how you used the API. They do not contain your audio, text, or transcripts.
```

Source text from “Opting out”:

```text
Set `mip_opt_out=true` and the request is excluded from model improvement:
```

```text
Opting out gives you zero data retention: Deepgram does not store your audio, text, transcripts, or synthesized audio after the response is returned. Request metadata and usage logs are still recorded, as described above.
```

The table in that section specifies the `mip_opt_out=true` query parameter for pre-recorded and streaming Speech-to-Text.

The “What Deepgram stores” table gives these values for Speech-to-Text with `mip_opt_out=true`:

```text
Request audio: Retained only for the duration needed to process the request
Transcript: Retained only for the duration needed to process the request
```

The text before each colon identifies a table row. The text after each colon is the source text from that table cell.

Source: [Model Improvement Partnership Program](https://developers.deepgram.com/docs/the-deepgram-model-improvement-partnership-program.md).
Retrieved: 2026-10-25T17:28:49Z (UTC).

Source text from “Want to opt out?”:

```text
Add `mip_opt_out=true` as a query parameter of all API requests that you want to be excluded from the Model Improvement Program. Data from opted-out requests is retained only for the duration necessary to process the request.
```

Source text from the section about data use:

```text
The only data we will store and use in future model training is the data that is contractually included through participation in the Deepgram Model Improvement Partnership Program. We will never redistribute data to 3rd parties without our customers' permission. Your data will never be used to market our services or to create advertising profiles.
```

## Deepgram legal terms

Source: [Terms of Service](https://deepgram.com/terms).
Retrieved: 2026-10-25T18:45:56Z (UTC). The page states “Last Updated: August 6, 2026”.
Source text from section 3.2, “License”:

```text
We use Your Content to maintain and provide our Services to you. We may also use Your Content to improve our Services, to develop other products and services, and for other business purposes, including training and testing our Models.
```

Source text from section 3.3, “Training Opt-Out”:

```text
For our APIs, you may opt out of model training on a per-request basis by setting the applicable parameter in your request as described here. This opt-out applies to the individual request in which it is set, to give you more flexibility over how Your Content is used.
```

## ElevenLabs WebSocket protocol

Source: [WebSocket API](https://elevenlabs.io/docs/api-reference/text-to-speech/v-1-text-to-speech-voice-id-stream-input.md).
Retrieved: 2026-10-25T17:28:49Z (UTC).

The API specifies `GET /v1/text-to-speech/{voice_id}/stream-input`.
The source text below contains field descriptions from its AsyncAPI specification.

`InitializeConnection.text`:

```text
The initial text that must be sent is a blank space.
```

`InitializeConnection` also has `generation_config`, which refers to `GenerationConfig`.
Source text from `GenerationConfig.chunk_length_schedule`:

```text
The default value for `chunk_length_schedule` is: [120, 160, 250, 290].
```

```text
Each item should be in the range 50-500.
```

A first value of 50 is in that range.

`SendText.text`:

```text
The text to be sent to the API for audio generation. Should always end with a single space string.
```

`SendText.flush`:

```text
Flush forces the generation of audio. Set this value to true when you have finished sending text, but want to keep the websocket connection open.
```

### Connection end

`CloseConnection.text` has an empty-string value and this description:

```text
End the stream with an empty string
```

`AudioOutput` has `audio`, `alignment`, and `normalizedAlignment` fields.
Source text from `AudioOutput.audio`:

```text
A generated partial audio chunk, encoded using the selected output_format, by default this is MP3 encoded as a base64 string.
```

Source text from `Alignment.charStartTimesMs`:

```text
Note these times are relative to the returned chunk from the model, and not the full audio response.
```

The protocol supplies character times through `alignment`, not a sentence-completion event.

## Adapter contracts

`DeepgramProvider(api_key)` makes no connection until a session connects or accepts nonempty audio.
Each session accepts mono 16 kHz little-endian signed int16 PCM.
`push`, `partial`, `finish`, `speech_started`, and `cancel` follow the local STT interface.

The audio limit is 15 seconds. A push above the limit accepts no part of that push.
`partial` stays empty before `speech_started`. This gate does not remove initial audio.
After voice onset, `partial` immediately gives the latest text. Quiet periods do not cause a provider error.
The gateway controls turn and quiet-period limits. `finish` has a bounded wait for the last service response.

Remote transcript retention has three limits:

- A maximum of 128 `is_final=true` messages, with empty messages in this count.
- A maximum of 4096 UTF-8 bytes in one committed or interim segment.
- A maximum of 16384 UTF-8 bytes in the complete transcript, with separator spaces and the current interim segment in this count.

These limits give more than eight committed segments and 1 kB of text for each second of the 15-second audio limit.
The segment limit also prevents a single large response from using the complete transcript budget.
Text above a limit causes a static session error. The receiver stops and closes the WebSocket before it accepts more segments.

`finish` sends `CloseStream`, receives committed results and metadata, and closes the connection.
Subsequent `finish` calls give the same transcript.

Each connection selects `nova-3` and `mip_opt_out=true`.
A connection can reconnect before it accepts audio.
After accepted audio, a disconnect causes a session error.
The adapter does not replay accepted audio or silently drop it.
The caller can start a new session. `aclose` waits for resource cleanup after cancellation.

`ElevenLabsProvider(api_key).stream(text, voice)` follows the typed TTS interface.
It selects `eleven_flash_v2_5` and declares mono 16 kHz little-endian signed int16 PCM.
It sends an initial space with `chunk_length_schedule=[50]`.
Each nonempty input part goes to the service immediately and unchanged.
There is no second input-text buffer. Empty parts do not close the connection.

The API recommends a trailing space for each text message.
Input parts can divide a word. The adapter does not add spaces between those parts.
At input end, it sends `{"text":" ","flush":true}` and then `{"text":""}`.
It receives audio until `isFinal=true`. `synthesize` supplies the same audio as bytes.

Sentence markers use the service's `alignment` or `normalizedAlignment` characters.
The local sentence assembler reads those characters after their audio chunk.
A marker identifies a complete sentence in that chunk, not a sample offset.
At `isFinal=true`, a remaining aligned fragment also gets a marker.
Without character times, the adapter supplies audio without invented sentence markers.
Cancellation stops the text iterator, cancels the sender, and closes the WebSocket and HTTP session.

The TTS stream uses a 10-second margin plus one second for each eight input characters sent.
This conservative rate lets the service make slow voice output without a short limit on legitimate long replies.
The adapter recomputes the allowance after each text part. It accepts a maximum of 16384 input characters in total for one stream.
The maximum allowance is thus 2058 seconds. Empty initialization and flush messages do not increase the allowance.

The deadline is the stream start time plus this allowance, not the last response time plus the allowance.
Continuous audio or `alignment` messages do not change it.
The receiver checks the deadline before it supplies each audio chunk or sentence marker.

The PCM byte limit is the allowance multiplied by the declared `rate`, `width`, and `channels`.
The default mono 16 kHz signed int16 format uses 32000 bytes for each second.
The adapter reads the declared format. It does not use a 16 kHz constant for other formats.
The same rate also selects the service's PCM output format.

One `alignment` message has a maximum of 4096 characters. All `alignment` text together has a maximum of 65536 UTF-8 bytes.
The sentence assembler also limits an unfinished fragment to 16384 characters.
Sentence punctuation does not change the total `alignment` budget.
The WebSocket limit of 262144 bytes also limits each decoded remote message and its arrays.
Data above a limit stops output, cancels the text sender, closes the connection, and gives the static provider error.

The two providers declare `destination="cloud"`. The default server and local provider factories do not call cloud constructors.

API keys go only in `Authorization: Token ...` for Deepgram and `xi-api-key` for ElevenLabs.
The adapters reject redirects and ignore proxy environment settings.
TLS is necessary. Numeric loopback endpoints for tests can use unencrypted connections.

Errors contain no provider response text. An error has no transport `__context__`.
Logs, metrics, and close messages contain no credentials.

The adapters use the same aiohttp dependency. No vendor SDK is necessary.
The server declares its `yarl` import with `yarl>=1.25.1,<2`. The lockfile keeps version 1.25.1.
The third-party notices include the wheel's license and copyright.

The asset manifest lists downloaded models, voices, and runtimes. Its accepted asset types do not include a cloud service.
These adapters download no model or voice. No cloud asset record is necessary.

## Deepgram live protocol sources

Source: [Live Audio](https://developers.deepgram.com/reference/speech-to-text/listen-streaming.mdx).
Retrieved: 2026-10-25T18:31:48Z (UTC).
The AsyncAPI specification gives `GET /v1/listen`, the `Authorization` header, binary input, and `Results` messages.
The `channel.alternatives[].transcript` field holds text. `is_final` identifies committed transcript segments.

Source: [Close Stream](https://developers.deepgram.com/docs/close-stream.mdx).
Retrieved: 2026-10-25T18:31:48Z (UTC).
Source text:

```text
Use the `CloseStream` message to close the WebSocket stream. This forces the server to immediately process any unprocessed audio data and return the final transcription results.
```

The documented payload is `{"type":"CloseStream"}`.

## ElevenLabs data use

Source: [ElevenLabs Terms of Service (non-EEA)](https://elevenlabs.io/terms-of-use).
Retrieved: 2026-10-25T17:28:49Z (UTC). The page states “Last Updated: 09 October 2026”.
These are the non-EEA terms. Other terms can apply in other jurisdictions.

Source text from section 4(d), “License to Your Content”:

```text
You hereby grant to ElevenLabs a license to use, reproduce, modify, adapt, publish, translate, create derivative works from, distribute, publicly or otherwise perform and display, and use your Content to provide the Services (including the trust and safety features therein), to improve the Services, and to develop new services and products.
```

Source text from section 4(i), “Data Deletion and Opt Out”:

```text
In addition, you may opt out of our use of your Content for training at any time by navigating to the 'Data use' menu in the 'Terms and Privacy' section of your ElevenLabs account. Your Content will no longer be used to improve our Services (including the Models) once the request has been processed by our team, except as may be necessary to provide the Services to you, but does not affect any uses of (or materials resulting from uses of) your Content prior to that date.
```

An account training opt-out is not a zero-retention guarantee.

## ElevenLabs request logging and output formats

The WebSocket API source above declares this query option:

```yaml
            enable_logging:
              type: boolean
              default: true
```

`ElevenLabsProvider` supplies `enable_logging=True` and sends that value explicitly.
An explicit `False` sends `enable_logging=false`. This option does not give account eligibility.
The default stays `True` until operator approval for a different default.

Source: [TTS API](https://elevenlabs.io/docs/api-reference/text-to-speech/convert.md).
Retrieved: 2026-10-26T07:04:23Z (UTC). Source text from the `enable_logging` query parameter:

```text
When enable_logging is set to false zero retention mode will be used for the request. This will mean history features are unavailable for this request, including request stitching. Zero retention mode may only be used by enterprise customers.
```

That API page also lists these permitted `output_format` values:

```text
`alaw_8000`, `mp3_22050_32`, `mp3_24000_48`, `mp3_44100_128`, `mp3_44100_192`, `mp3_44100_32`, `mp3_44100_64`, `mp3_44100_96`, `opus_48000_128`, `opus_48000_192`, `opus_48000_32`, `opus_48000_64`, `opus_48000_96`, `pcm_16000`, `pcm_22050`, `pcm_24000`, `pcm_32000`, `pcm_44100`, `pcm_48000`, `pcm_8000`, `ulaw_8000`, `wav_16000`, `wav_22050`, `wav_24000`, `wav_32000`, `wav_44100`, `wav_48000`, `wav_8000`
```

This list documents `pcm_16000`. The WebSocket schema itself gives `output_format` as “Any type”.
The adapter uses the TTS format list for the WebSocket request. Live checks: `NOT RUN`.

Source: [Zero Retention Mode (Enterprise)](https://elevenlabs.io/docs/eleven-api/resources/zero-retention-mode.mdx).
Retrieved: 2026-10-26T07:04:23Z (UTC). Source text from “When can a customer use Zero Retention Mode?”:

```text
Zero Retention Mode is available to select enterprise customers. However, access to this feature may be restricted if ElevenLabs determines a customer's use case to be high risk, if an account is flagged by an automated system for additional moderation or at ElevenLabs' sole discretion. In such cases, the enterprise administrator will be promptly notified of the restriction.
```

Source text from “Text to Speech”:

```text
All endpoints beginning with `/v1/text-to-speech/` and the TTS WebSocket connection are covered by ZRM.
```

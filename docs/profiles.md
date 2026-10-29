# Turn settings

The server is experimental. Human pause and executable-prefix acceptance is pending.
The `safe` and `fast` settings use the same unmeasured `Profile` defaults:

| Setting | Default |
|---|---|
| Voice-run end guard | 400 ms |
| Continuous voice for command start | 250 ms |
| Maximum wait for command start | 5 s |
| Minimum time without voice | 400 ms |
| Smart Turn limit | 0.8 |
| Maximum time without voice | 1500 ms |
| Hard endpoint | 15 s |

The detector ignores a voice run that ends at or before 400 ms.
Silero gets each audio frame, and STT decodes all audio.
The guard uses audio time, not wall time.
The public design record is `assist-live docs/design.md`.

The command starts when a voice run lasts 250 ms or more and continues after 400 ms.
The run length includes voice before 400 ms.
Its voice onset is the start of the full voice run.
Before command start, the detector does not use `early` endpoints or maximum time without voice.
After command start, the usual rules for time without voice apply.

At 5 s of audio, the detector ends the turn with `max_silence` only if no voice run continues.
An open voice run continues until it qualifies or stops.
If it stops after 5 s and before command start, the detector ends the turn at that audio frame with `max_silence`.
STT then sends the transcript of all audio.
The hard endpoint stays 15 s.

The guard applies to all turns, with follow-up turns and button input.
A voice run that ends in the first 400 ms does not start the command, so the detector waits for a subsequent command.
If no command starts, the turn ends at 5 s.
Volunteer recordings must supply the measurements for these start values.

The transcript guard is active in each `Profile`. Number endings can be complete.

Articles, prepositions, and conjunctions at the end cause a longer wait.
This word-class rule also causes a longer wait for complete particle endings, such as "turn the lights on".

The same cost applies to "off", "up", and "down" endings.
The word-class list has no particle-specific rule.
The human pause and prefix measurements must quantify this latency cost before `Profile` acceptance.

After the minimum time without voice, Smart Turn checks a changed complete transcript on the next processed frame.
For an unchanged transcript, audio growth can cause a new check each 160 ms.
The `max_silence` and `hard` endpoints do not wait for that interval.

Use one `TurnDetector` for each turn. A shared `ModelRuntime` is for sequential turns only.

The detector sets VAD state and context to their initial values at the start of each turn.
Call `flush(partial)` when the audio stream stalls or ends, to process the remaining received samples.

Only the VAD input gets 0 padding. The event clock counts received samples, not padding or wall time.
An empty flush cannot add time without voice.

`Profile` tuning uses tuning rows only. For freeze, the human held-out pause denominator and the prefix gate are necessary.
The corpus interval fields are `pause_intervals_ms` for pause rows and `executable_prefix_intervals_ms` for executable-prefix rows.

Do not use the replay-only STT export as a human pause safety result.

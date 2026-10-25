# Turn settings

The server is experimental. Human pause and executable-prefix acceptance is pending.
The `safe` and `fast` settings use the same unmeasured `Profile` defaults:

| Setting | Default |
|---|---|
| Minimum time without voice | 400 ms |
| Smart Turn limit | 0.8 |
| Maximum time without voice | 1500 ms |
| Hard endpoint | 15 s |

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

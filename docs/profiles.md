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
Silero gets each audio frame, and the STT provider receives all audio to the detector's end sample.
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

Without a command start, the gateway sends an empty `final` transcript.
It cancels the provider session without a `final` decode or a `partial` replacement.
This rule applies to each provider and each type of turn end (see `docs/design.md`).
The hard endpoint stays 15 s.

The guard applies to all turns, with follow-up turns and button input.
A voice run that ends in the first 400 ms does not start the command, so the detector waits for a subsequent command.
At 5 s, a turn without command start ends only when no voice run continues.
Volunteer recordings must supply the measurements for these start values.

A short word with less than 250 ms of continuous voice has no command start and no transcript.
An initial word that ends in the first 400 ms can also have no transcript in a follow-up turn.
P1 sets the start rule with volunteer recordings.

The transcript guard is active in each `Profile`. Number endings can be complete.

Articles, prepositions, and conjunctions at the end cause a longer wait.
The particles `on`, `off`, `up`, `down`, `out`, and `over` can complete a command after its object.
Examples are "turn the lights on", "turn it off", and "is the light on".
The other words in the incomplete-ending list, such as `in`, keep the longer wait.

A particle directly after an opening command verb keeps the turn open.
This rule also applies when only start words come before the verb.

The start words are `please`, `you`, and the fillers `uh`, `um`, `er`, `erm`, `ah`, `hmm`, `so`, `okay`, and `ok`.
The request words `can`, `could`, `would`, and `will` are start words only directly before `you`, as in "could you set".
A transcript with only start words does not complete a turn. Examples are "please", "could you", "um", and "so okay".

The words `well` and bare request words are not start words. Thus "well on" and "can on" can complete a command for those device names.
A bare `you` is a start word, because a short first word such as "can" can have no transcript.

These checks do not use satellite name hints. They can increase the time before a command ends for a name that is also a start word.

The template `[activate] [<the>] {name} [scene]` accepts a scene name alone (`home-assistant-intents` 2026.10.6, `data/en.json:4347`). A script template with an optional verb also accepts a script name alone (`data/en.json:4365`).
Thus a scene or a script named "Okay" waits for the maximum time without voice, as does "okay on" for a device with that name.
This wait prevents an `early` end after "please" before the remaining words of a command. The transcript does not change.

The guard uses these pairs from the Home Assistant English intent templates:

| Particle | Opening verbs |
|---|---|
| `on` | `turn`, `switch`, `change`, `bring`, `replay`, `skip`, `next` |
| `off` | `turn`, `switch`, `change`, `bring`, `check`, `complete`, `delete`, `remove` |
| `up` | `turn`, `close`, `shut`, `lower`, `open`, `raise`, `lift`, `light` |
| `down` | `turn`, `close`, `shut`, `lower` |
| `out` | `turn`, `switch`, `change`, `bring` |
| `over` | None in the templates |

The source is `home-assistant-intents` 2026.10.6, `data/en.json`, lines 2466, 2530, 2531, 2689, 3196, 3561, 3955–3956, and 4942–4946.
Each particle has its own verb set. Thus "is the light on" can end, but "light up" cannot.
An earlier verb from that set lets an object use a verb word, as in "turn the switch off".

A particle cannot complete a command when no words or only start words come before it, as in "on" or "please on".
The opening verbs include the verbs in the table, without "next", "skip", and "replay". They also include these verbs:

- `activate`, `add`, `adjust`, `announce`, `broadcast`, `clean`, `create`, `deactivate`, and `decrease`.
- `get`, `go`, `illuminate`, `increase`, `lock`, `make`, `play`, `press`, and `push`.
- `put`, `return`, `run`, `set`, `start`, `take`, `tell`, `transition`, `unlock`, and `vacuum`.

Each template with `set` or `make` has an object or a value after the verb, as in "make the light blue".
Hassil 3.12.1 expands 861 English templates from `home-assistant-intents` 2026.10.6 and 862 from 2026.10.27.
No complete expansion of three words or fewer ends in a new opening verb. The check shows each slot as one necessary word.
The verbs `press` and `push` occur only in 2026.10.27. The other new verbs occur in the two versions.

An opening verb at the end of a transcript cannot complete a command when no words or only start words come before it.
Examples are "turn", "please turn", "could you open", "please set", and "could you make".
After an object, a verb word can complete a command, as in "turn on the kitchen light".

The words "next", "skip", and "replay" are complete media commands in the templates, so they can end alone or after start words, as in "please skip".
The complete template verbs `dock`, `mow`, `mute`, `pause`, `resume`, `stop`, `unmute`, and `unpause` also keep this behavior.
The words `cancel` and `continue` stay complete because they can be one-word replies in a dialog.
The query verbs `are`, `do`, and `is` are not opening command verbs in this rule.
See the [public design](https://github.com/legion-works/assist-live/blob/380a0e9/docs/design.md#end-of-turn-and-stt-fallback), `docs/design.md`.

This text rule does not parse grammar. For example, "turn off the light and turn on" contains an earlier verb from that set.
The rule can accept that unfinished command. Smart Turn and the minimum time without voice apply.

The rule can also accept a particle that starts a phrase after the object.
Examples are "turn on the lights on" before "the porch" and "next track on" before a speaker name.
The human pause and prefix measurements stay necessary before `Profile` acceptance.

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

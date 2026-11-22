import pytest
from assist_live_server.turn.transcript_guard import complete_partial


@pytest.mark.parametrize(
    "ending",
    [
        "a",
        "an",
        "the",
        "to",
        "of",
        "in",
        "at",
        "from",
        "with",
        "for",
        "into",
        "under",
        "between",
        "and",
        "or",
        "but",
        "if",
        "because",
        "until",
        "while",
        "although",
        "than",
    ],
)
def test_articles_prepositions_conjunctions_fail_closed(ending):
    assert not complete_partial(f"turn lights {ending.upper()}?!")


@pytest.mark.parametrize("partial", ["", "  ", "...", "turn on the", "turn lights to…"])
def test_empty_and_incomplete_partial(partial):
    assert not complete_partial(partial)


@pytest.mark.parametrize(
    "partial",
    [
        "please",
        "uh",
        "um",
        "er",
        "erm",
        "ah",
        "hmm",
        "so",
        "okay",
        "ok",
        "you",
        "can you",
        "could you",
        "would you",
        "will you",
        "so okay",
        "Um, please…",
        "PLEASE, could you, um?",
    ],
)
def test_only_start_words_stay_incomplete(partial):
    assert not complete_partial(partial)


@pytest.mark.parametrize("partial", ["can", "could", "would", "will"])
def test_request_word_without_you_can_complete_name(partial):
    assert complete_partial(partial)


@pytest.mark.parametrize(
    "partial",
    ["turn on lights", "set volume five.", "stop!", "turn off lights in five minutes"],
)
def test_complete_partial(partial):
    assert complete_partial(partial)


def test_unsupported_language_does_not_enable_early_end():
    assert not complete_partial("lights", language="fr")


@pytest.mark.parametrize(
    "partial",
    [
        "set volume to 50",
        "wake me at 5",
        "123",
        "set volume to 0.5%",
        "wake me at 5:30.",
    ],
)
def test_number_is_a_possible_complete_ending(partial):
    assert complete_partial(partial)


@pytest.mark.parametrize("partial", ["set volume to 50 and", "wake me at 5 or"])
def test_words_after_a_number_still_control_the_guard(partial):
    assert not complete_partial(partial)


@pytest.mark.parametrize(
    "partial",
    [
        "turn the light on",
        "turn the test fan off",
        "is the light on",
        "turn the test light on",
        "turn the switch off",
        "turn it off",
        "turn the lights back on",
        "turn it up",
        "turn it down",
        "turn the lights out",
        "turn it over",
        "TURN THE LIGHT ON?!",
    ],
)
def test_particle_after_object_can_complete_command(partial):
    assert complete_partial(partial)


@pytest.mark.parametrize(
    "verb, particles",
    [
        ("turn", ["on", "off", "out", "up", "down"]),
        ("switch", ["on", "off", "out"]),
        ("change", ["on", "off", "out"]),
        ("bring", ["on", "off", "out"]),
        ("close", ["up", "down"]),
        ("shut", ["up", "down"]),
        ("lower", ["up", "down"]),
        ("open", ["up"]),
        ("raise", ["up"]),
        ("lift", ["up"]),
        ("light", ["up"]),
        ("check", ["off"]),
        ("complete", ["off"]),
        ("delete", ["off"]),
        ("remove", ["off"]),
        ("replay", ["on"]),
        ("skip", ["on"]),
    ],
)
def test_particle_directly_after_command_verb_stays_incomplete(verb, particles):
    for particle in particles:
        assert not complete_partial(f"{verb} {particle}")


@pytest.mark.parametrize(
    "partial",
    ["so turn on", "please turn on", "can you switch off", "please light up"],
)
def test_particle_after_prefixed_command_verb_stays_incomplete(partial):
    assert not complete_partial(partial)


@pytest.mark.parametrize(
    "partial",
    ["turn", "turn on the", "set a timer for", "turn on the lights in", "next on"],
)
def test_command_opening_stays_incomplete(partial):
    assert not complete_partial(partial)


@pytest.mark.parametrize(
    "partial",
    [
        "please turn",
        "uh turn",
        "so switch",
        "could you turn",
        "can you open",
        "um please close",
        "Please, turn…",
        "please turn off",
        "uh switch on",
    ],
)
def test_opening_verb_after_leading_words_stays_incomplete(partial):
    assert not complete_partial(partial)


@pytest.mark.parametrize(
    "partial", ["please on", "uh off", "could you up", "Um, please down…", "so out"]
)
def test_particle_after_only_leading_words_stays_incomplete(partial):
    assert not complete_partial(partial)


@pytest.mark.parametrize(
    "partial",
    [
        "is the fan on",
        "turn the light on",
        "please turn the light on",
        "well on",
        "well off",
        "uh well on",
        "can on",
        "can off",
        "will on",
        "please can on",
    ],
)
def test_particle_after_object_and_leading_words_can_complete(partial):
    assert complete_partial(partial)


@pytest.mark.parametrize(
    "partial",
    [
        "can you turn",
        "could you set",
        "please could you turn",
        "uh can you on",
        "Would you, um, open…",
        "will you off",
        "you turn",
    ],
)
def test_request_word_with_you_stays_incomplete(partial):
    assert not complete_partial(partial)


@pytest.mark.parametrize("partial", ["set", "please set", "could you set", "Uh, set…"])
def test_set_without_object_stays_incomplete(partial):
    assert not complete_partial(partial)


@pytest.mark.parametrize(
    "partial",
    ["set a timer for five minutes", "set the temperature to 20", "set volume five."],
)
def test_set_command_with_object_can_complete(partial):
    assert complete_partial(partial)


@pytest.mark.parametrize(
    "verb",
    [
        "activate",
        "add",
        "adjust",
        "announce",
        "broadcast",
        "clean",
        "create",
        "deactivate",
        "decrease",
        "get",
        "go",
        "illuminate",
        "increase",
        "lock",
        "make",
        "play",
        "press",
        "push",
        "put",
        "return",
        "run",
        "start",
        "take",
        "tell",
        "transition",
        "unlock",
        "vacuum",
    ],
)
@pytest.mark.parametrize("start", ["", "please ", "could you ", "So, "])
def test_template_opening_verb_without_object_stays_incomplete(verb, start):
    assert not complete_partial(f"{start}{verb}…")


@pytest.mark.parametrize(
    "partial",
    [
        "activate test scene",
        "add bread to the shopping list",
        "adjust the volume to 20",
        "announce dinner is ready",
        "broadcast dinner is ready",
        "clean the kitchen",
        "create a timer for five minutes",
        "deactivate test scene",
        "decrease the volume to 20",
        "get the temperature",
        "go to the next track",
        "illuminate the kitchen",
        "increase the volume to 20",
        "lock the test door",
        "make the light blue",
        "play the next track",
        "press the test button",
        "push the test button",
        "put the timer on hold",
        "return to base",
        "run test script",
        "start a timer for five minutes",
        "take bread off the shopping list",
        "tell me the time",
        "transition to test scene",
        "unlock the test door",
        "vacuum the kitchen",
    ],
)
def test_template_opening_verb_with_object_can_complete(partial):
    assert complete_partial(partial)


@pytest.mark.parametrize(
    "partial",
    [
        "dock",
        "mow",
        "mute",
        "pause",
        "resume",
        "stop",
        "unmute",
        "unpause",
        "cancel",
        "continue",
        "please cancel",
        "could you continue",
    ],
)
def test_complete_template_verbs_and_dialog_replies_can_complete(partial):
    assert complete_partial(partial)


@pytest.mark.parametrize(
    "partial",
    ["is the door open", "turn on the kitchen light", "turn off the switch"],
)
def test_verb_word_after_an_object_can_complete(partial):
    assert complete_partial(partial)


@pytest.mark.parametrize(
    "partial", ["skip", "Replay.", "next", "please skip", "uh next", "could you replay"]
)
def test_single_word_media_command_can_complete(partial):
    assert complete_partial(partial)


@pytest.mark.parametrize("partial", ["on", "off", "up", "down", "out", "over"])
def test_particle_without_object_stays_incomplete(partial):
    assert not complete_partial(partial)

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
    ["turn", "turn on the", "set a timer for", "turn on the lights in"],
)
def test_command_opening_stays_incomplete(partial):
    assert not complete_partial(partial)


@pytest.mark.parametrize("partial", ["skip", "Replay."])
def test_single_word_media_command_can_complete(partial):
    assert complete_partial(partial)


@pytest.mark.parametrize("partial", ["on", "off", "up", "down", "out", "over"])
def test_particle_without_object_stays_incomplete(partial):
    assert not complete_partial(partial)

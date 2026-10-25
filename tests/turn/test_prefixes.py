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
        "on",
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

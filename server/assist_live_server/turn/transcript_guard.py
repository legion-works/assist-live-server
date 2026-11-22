"""Conservative English transcript endings, independent of the selected profile."""

import re

INCOMPLETE_ENDINGS = frozenset(
    """a an the aboard about above across after against along amid among around
    as at before behind below beneath beside besides between beyond by concerning
    despite down during except for from in inside into like near of off on onto
    opposite out outside over past per regarding round since than through throughout
    till to toward towards under underneath unlike until unto up upon versus via
    with within without and but or nor so yet although because if lest once
    provided that though unless when whenever where whereas wherever whether while
    whose which""".split()
)

# HA English intent templates pair these command verbs with each particle.
PARTICLE_COMMAND_VERBS = {
    "on": frozenset("turn switch change bring replay skip next".split()),
    "off": frozenset("turn switch change bring check complete delete remove".split()),
    "up": frozenset("turn close shut lower open raise lift light".split()),
    "down": frozenset("turn close shut lower".split()),
    "out": frozenset("turn switch change bring".split()),
    "over": frozenset(),
}
# "next", "skip", and "replay" are complete media commands in the HA English templates.
# No HA English template of three words or fewer ends with one of these verbs
# (home-assistant-intents 2026.10.6 and 2026.10.27). "cancel" and "continue" stay
# out, because a person frequently says them alone as a reply.
OPENING_VERBS = (
    frozenset().union(*PARTICLE_COMMAND_VERBS.values()) - {"next", "replay", "skip"}
) | frozenset(
    """set activate add adjust announce broadcast clean create deactivate decrease
    get go illuminate increase lock make play press push put return run start take
    tell transition unlock vacuum""".split()
)
# Only these words can come before an opening verb or a particle that stays open,
# because verb words such as "light" and "open" can also end a command after an object.
# A bare "you" stays, because a short first word such as "can" can have no transcript.
LEADING_WORDS = frozenset("please uh um er erm ah hmm so okay ok you".split())
# These words are start words only before "you", because a light can be named "Can".
REQUEST_WORDS = frozenset("can could would will".split())


def transcript_words(text: str) -> list[str]:
    return re.findall(r"[^\W_]+(?:['’][^\W_]+)*", text.casefold())


def only_leading_words(words: list[str]) -> bool:
    return all(
        word in LEADING_WORDS
        or (word in REQUEST_WORDS and words[index + 1 : index + 2] == ["you"])
        for index, word in enumerate(words)
    )


def complete_partial(partial: str, *, language: str = "en") -> bool:
    if language != "en":
        return False
    words = transcript_words(partial)
    if not words or only_leading_words(words):
        return False
    if words[-1] in PARTICLE_COMMAND_VERBS:
        if only_leading_words(words[:-1]):
            return False
        verbs = PARTICLE_COMMAND_VERBS[words[-1]]
        # An earlier verb from the same set marks an object in "turn the switch off".
        return words[-2] not in verbs or any(word in verbs for word in words[:-2])
    if words[-1] in OPENING_VERBS and only_leading_words(words[:-1]):
        return False
    return words[-1] not in INCOMPLETE_ENDINGS

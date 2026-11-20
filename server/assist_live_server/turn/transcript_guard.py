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
OPENING_VERBS = frozenset().union(*PARTICLE_COMMAND_VERBS.values()) - {
    "next",
    "replay",
    "skip",
}


def transcript_words(text: str) -> list[str]:
    return re.findall(r"[^\W_]+(?:['’][^\W_]+)*", text.casefold())


def complete_partial(partial: str, *, language: str = "en") -> bool:
    if language != "en":
        return False
    words = transcript_words(partial)
    if not words:
        return False
    if words[-1] in PARTICLE_COMMAND_VERBS:
        if len(words) < 2:
            return False
        verbs = PARTICLE_COMMAND_VERBS[words[-1]]
        # An earlier verb from the same set marks an object in "turn the switch off".
        return words[-2] not in verbs or any(word in verbs for word in words[:-2])
    if len(words) == 1 and words[0] in OPENING_VERBS:
        return False
    return words[-1] not in INCOMPLETE_ENDINGS

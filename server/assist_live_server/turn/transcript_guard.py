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


def complete_partial(partial: str, *, language: str = "en") -> bool:
    if language != "en":
        return False
    words = re.findall(r"[^\W\d_]+(?:['’][^\W\d_]+)*", partial.casefold())
    return bool(words) and words[-1] not in INCOMPLETE_ENDINGS

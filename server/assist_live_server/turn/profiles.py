"""Unmeasured defaults. Human pause and prefix gates must pass before acceptance."""

import math
from dataclasses import dataclass

HARD_ENDPOINT_MS = 15000
# See assist-live docs/design.md for the rules that keep wake-word tails out.
START_GUARD_MS = 400
COMMAND_START_MS = 250
COMMAND_WAIT_MS = 5000


@dataclass(frozen=True)
class Profile:
    min_silence_ms: int = 400
    smart_turn_limit: float = 0.8
    max_silence_ms: int = 1500
    early_enabled: bool = True

    def __post_init__(self) -> None:
        if (
            type(self.min_silence_ms) is not int
            or type(self.max_silence_ms) is not int
            or not 0 < self.min_silence_ms <= self.max_silence_ms <= HARD_ENDPOINT_MS
            or not math.isfinite(self.smart_turn_limit)
            or not 0 <= self.smart_turn_limit <= 1
            or type(self.early_enabled) is not bool
        ):
            raise ValueError("invalid turn profile")


PROFILES = {"safe": Profile(), "fast": Profile()}

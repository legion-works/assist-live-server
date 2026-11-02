"""Memory-only names and canonical list digests."""

import hashlib
import json
from collections.abc import Mapping, MutableMapping
from typing import TYPE_CHECKING

from assist_live_client.protocol import MAX_HINT_BYTES as MAX_HINT_BYTES

if TYPE_CHECKING:
    from .providers.name_context import NameContext


class HintList:
    def __init__(self, names: tuple[str, ...]) -> None:
        self.names = names
        self._context: NameContext | None = None

    def context(
        self,
        vocabulary: Mapping[int, str],
        size: int,
        *,
        shared: MutableMapping[tuple[str, ...], NameContext] | None = None,
    ) -> NameContext:
        from .providers.name_context import NameContext, tokenize_names

        # The single decode worker serializes first use, including canceled turns.
        if self._context is None:
            self._context = shared.get(self.names) if shared is not None else None
            if self._context is None:
                self._context = NameContext(
                    tokenize_names(self.names, vocabulary), size
                )
                if shared is not None:
                    shared[self.names] = self._context
        return self._context


def validate_hints(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > 500:
        raise ValueError("invalid_hints")
    names: list[str] = []
    for name in value:
        if (
            not isinstance(name, str)
            or not 1 <= len(name) <= 64
            or not any(char.isalpha() for char in name)
            or name != name.lower()
            or name != name.strip()
        ):
            raise ValueError("invalid_hints")
        name.encode("utf-8")
        names.append(name)
    if len(set(names)) != len(names):
        raise ValueError("invalid_hints")
    return tuple(names)


def hint_summary(names: tuple[str, ...]) -> dict[str, str | int]:
    raw = json.dumps(names, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return {"count": len(names), "digest": hashlib.sha256(raw).hexdigest()}

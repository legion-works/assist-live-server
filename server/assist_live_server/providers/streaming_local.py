"""No local streaming candidate passed the replay selection."""

from typing import NoReturn

STREAMING_PROVIDERS: tuple[str, ...] = ()


def no_streaming_default(name: str | None = None) -> NoReturn:
    raise ValueError(
        f"no local streaming default or accepted provider: {name or 'default'}"
    )

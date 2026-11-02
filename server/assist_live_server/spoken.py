"""Remove spoken-text marks without a change to request positions."""

import unicodedata
from collections.abc import Iterator

MAX_LINK_HOLD = 2048
type Character = tuple[str, int, bool]


def _word(char: str) -> bool:
    return bool(char) and (char.isalnum() or unicodedata.category(char).startswith("M"))


class SpokenText:
    def __init__(self) -> None:
        self.received = 0
        self._prefix: list[tuple[str, int]] = []
        self._kind = "indent"
        self._rule = ""
        self._count = 0
        self._forced = False
        self._pending: list[tuple[str, int]] = []
        self._mode = ""
        self._left_word = False
        self._previous = ""
        self._label_end = 0
        self._depth = 0
        self._pipe: list[tuple[str, int]] = []
        self._cell = False
        self._tag_quote = ""
        self._last_cr = False

    def feed(self, part: str) -> Iterator[Character]:
        for char in part:
            self.received += 1
            yield from self._line(char, self.received)

    def finish(self) -> Iterator[Character]:
        yield from self._end_line(self.received, final=True)

    def _flush_prefix(self) -> Iterator[Character]:
        prefix, self._prefix = self._prefix, []
        self._kind = "body"
        for char, end in prefix:
            yield from self._inline(char, end)

    def _drop_prefix(self) -> Iterator[Character]:
        if self._prefix:
            yield "", self._prefix[-1][1], False
        self._prefix.clear()

    def _line(self, char: str, end: int) -> Iterator[Character]:
        if char == "\n":
            if self._mode in {"label", "address", "tag"}:
                yield from self._inline(char, end)
                return
            yield from self._end_line(end)
            return
        self._last_cr = char == "\r"
        kind = self._kind
        if kind == "discard":
            yield "", end, False
            return
        if kind == "body":
            yield from self._inline(char, end)
            return
        if kind == "indent":
            if char in " \t\r":
                self._prefix.append((char, end))
                return
            self._kind = (
                "hash"
                if char == "#"
                else "bullet"
                if char in "-*+"
                else "rule"
                if char == "_"
                else "fence"
                if char == "`"
                else "table"
                if char in "|:"
                else "number"
                if char.isdecimal()
                else "body"
            )
            self._rule = char
            self._count = 1
            if char == ">":
                yield from self._drop_prefix()
                self._kind = "body"
                yield "", end, False
            elif self._kind == "body":
                yield from self._flush_prefix()
                yield from self._inline(char, end)
            else:
                self._prefix.append((char, end))
            return
        if kind == "hash":
            if char == "#":
                self._prefix.append((char, end))
                return
            self._forced = True
            yield from self._drop_prefix()
            self._kind = "body"
            yield from self._inline(char, end)
            return
        if kind == "number":
            if char.isdecimal():
                self._prefix.append((char, end))
                return
            if char in ".)":
                self._kind = "number_end"
                self._prefix.append((char, end))
                return
        elif kind in {"bullet", "number_end"}:
            if char in " \t\r":
                self._forced = True
                yield from self._drop_prefix()
                self._kind = "body"
                yield "", end, False
                return
            if kind == "bullet" and char == self._rule and char in "-*":
                self._kind = "rule"
                self._prefix.append((char, end))
                self._count += 1
                return
        elif kind == "rule":
            if self._rule == "-" and char in "|:":
                self._kind = "table"
                self._prefix.append((char, end))
                return
            if char == self._rule or char in " \t\r":
                self._prefix.append((char, end))
                self._count += char == self._rule
                return
        elif kind == "table":
            if char in "|-: \t\r":
                self._prefix.append((char, end))
                return
        elif kind == "fence":
            if char == "`":
                self._prefix.append((char, end))
                self._count += 1
                if self._count == 3:
                    yield from self._drop_prefix()
                    self._kind = "discard"
                return
        yield from self._flush_prefix()
        yield from self._inline(char, end)

    def _end_line(self, end: int, *, final: bool = False) -> Iterator[Character]:
        if self._kind in {"discard", "table", "hash", "bullet", "number_end"} or (
            self._kind == "rule" and self._count >= 3
        ):
            yield from self._drop_prefix()
        else:
            yield from self._flush_prefix()
        yield from self._flush_inline("")
        self._pipe.clear()
        if self._forced:
            yield "", end if final else end - 1 - self._last_cr, True
        if not final:
            yield "\n", end, False
        self._kind = "indent"
        self._forced = False
        self._cell = False
        self._previous = ""
        self._last_cr = False

    def _literal(self, values: list[tuple[str, int]]) -> Iterator[Character]:
        for char, end in values:
            yield char, end, False
            self._previous = char
            self._cell = self._cell or not char.isspace()

    def _flush_inline(self, right: str) -> Iterator[Character]:
        pending, self._pending = self._pending, []
        mode, self._mode = self._mode, ""
        if mode == "tag":
            self._tag_quote = ""
        if mode == "mark":
            mark = pending[0][0]
            removable = mark != "~" or len(pending) == 2
            if removable and self._left_word != _word(right):
                yield "", pending[-1][1], False
                return
        yield from self._literal(pending)

    def _inline(
        self, char: str, end: int, *, links: bool = True
    ) -> Iterator[Character]:
        mode = self._mode
        if mode in {"label", "bracket", "address", "tag"}:
            self._pending.append((char, end))
            if mode == "tag" and len(self._pending) == 2:
                if not (char.isalpha() or char in "/!?"):
                    yield from self._flush_inline("")
                    return
            if mode == "label" and char == "]":
                self._label_end = len(self._pending) - 1
                self._mode = "bracket"
            elif mode == "bracket":
                if char == "(":
                    self._mode = "address"
                    self._depth = 1
                else:
                    yield from self._flush_inline("")
            elif mode == "address":
                self._depth += (char == "(") - (char == ")")
                if self._depth == 0:
                    pending, self._pending = self._pending, []
                    self._mode = ""
                    start = 2 if pending[0][0] == "!" else 1
                    for label_char, label_end in pending[start : self._label_end]:
                        yield from self._inline(label_char, label_end, links=False)
                    yield from self._flush_inline("")
                    yield "", end, False
            elif mode == "tag":
                if self._tag_quote:
                    if char == self._tag_quote:
                        self._tag_quote = ""
                elif char in "\"'":
                    self._tag_quote = char
                elif char == ">":
                    self._pending.clear()
                    self._mode = ""
                    yield "", end, False
            if len(self._pending) >= MAX_LINK_HOLD:
                # An incomplete token must not hold the stream without a limit.
                yield from self._flush_inline("")
            return
        if mode == "bang":
            if char == "[" and links:
                self._pending.append((char, end))
                self._mode = "label"
                return
            yield from self._flush_inline(char)
        elif mode == "mark":
            if char == self._pending[0][0]:
                self._pending.append((char, end))
                return
            yield from self._flush_inline(char)
        if self._pipe:
            if char in " \t":
                self._pipe.append((char, end))
                return
            if self._cell:
                yield ",", self._pipe[0][1], False
                yield from self._literal(self._pipe[1:])
            self._pipe.clear()
        if char == "|":
            self._pipe.append((char, end))
            return
        if char == "[" and links:
            self._mode = "label"
        elif char == "!" and links:
            self._mode = "bang"
        elif char == "<":
            self._mode = "tag"
        elif char in "*_~`":
            self._mode = "mark"
            self._left_word = _word(self._previous)
        else:
            yield from self._literal([(char, end)])
            return
        self._pending.append((char, end))

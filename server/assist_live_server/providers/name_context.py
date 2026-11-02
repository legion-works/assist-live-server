"""Token prefix graph with temporary bonuses for incomplete names."""

from bisect import bisect_left
from collections import OrderedDict, deque
from collections.abc import Mapping, Sequence

import numpy as np
from numpy.typing import NDArray


def tokenize_names(
    names: Sequence[str], vocabulary: Mapping[int, str]
) -> list[tuple[int, ...]]:
    pieces: dict[str, list[tuple[str, int]]] = {}
    for token, piece in vocabulary.items():
        if piece and not piece.startswith("<"):
            piece = piece.replace("▁", " ")
            pieces.setdefault(piece[0], []).append((piece, token))
    forms = set()
    for name in names:
        forms.add(name)
        forms.add(name[:1].upper() + name[1:])
        forms.add(" ".join(word[:1].upper() + word[1:] for word in name.split(" ")))
    phrases = set()
    for name in sorted(forms):
        target = " " + name
        paths: dict[int, tuple[int, ...]] = {len(target): ()}
        for position in range(len(target) - 1, -1, -1):
            candidates = [
                (token,) + paths[position + len(piece)]
                for piece, token in pieces.get(target[position], ())
                if target.startswith(piece, position) and position + len(piece) in paths
            ]
            if candidates:
                paths[position] = min(candidates, key=lambda ids: (len(ids), ids))
        if 0 in paths:
            phrases.add(paths[0])
    return sorted(phrases)


class NameContext:
    def __init__(
        self, phrases: Sequence[tuple[int, ...]], vocabulary_size: int
    ) -> None:
        children: list[dict[int, int]] = [{}]
        failure, depth, output = [0], [0], [0]
        for phrase in sorted(set(phrases)):
            state = 0
            for token in phrase:
                if token not in children[state]:
                    children[state][token] = len(children)
                    children.append({})
                    failure.append(0)
                    depth.append(depth[state] + 1)
                    output.append(0)
                state = children[state][token]
            output[state] = len(phrase)
        self.active = len(children) > 1
        queue = deque(children[0].values())
        while queue:
            state = queue.popleft()
            output[state] += output[failure[state]]
            for token, child in children[state].items():
                suffix = failure[state]
                while suffix and token not in children[suffix]:
                    suffix = failure[suffix]
                failure[child] = children[suffix].get(token, 0)
                queue.append(child)
        offsets, tokens, targets = [0], [], []
        for edges in children:
            for token, child in sorted(edges.items()):
                tokens.append(token)
                targets.append(child)
            offsets.append(len(tokens))
        self.offsets: NDArray[np.int32] = np.asarray(offsets, np.int32)
        self.edge_tokens: NDArray[np.int32] = np.asarray(tokens, np.int32)
        self.edge_targets: NDArray[np.int32] = np.asarray(targets, np.int32)
        self.failure: NDArray[np.int32] = np.asarray(failure, np.int32)
        # Memory views return Python integers without NumPy scalar conversion.
        self._offsets = memoryview(self.offsets)
        self._tokens = memoryview(self.edge_tokens)
        self._targets = memoryview(self.edge_targets)
        self._failure = memoryview(self.failure)
        self.output: NDArray[np.float32] = np.asarray(output, np.float32)
        self.potential: NDArray[np.float32] = np.array(
            [0 if complete else d for complete, d in zip(output, depth, strict=True)],
            np.float32,
        )
        self._root_bonus = np.zeros(vocabulary_size, np.float32)
        for token, child in children[0].items():
            self._root_bonus[token] = self.potential[child] + self.output[child]
        self._rows: OrderedDict[int, NDArray[np.float32]] = OrderedDict()

    def step(self, state: int, token: int) -> int:
        while True:
            start, end = self._offsets[state], self._offsets[state + 1]
            edge = bisect_left(self._tokens, token, start, end)
            if edge < end and self._tokens[edge] == token:
                return self._targets[edge]
            if state == 0:
                return 0
            state = self._failure[state]

    def bonus_row(self, state: int) -> NDArray[np.float32]:
        if state == 0:
            return self._root_bonus
        if state in self._rows:
            self._rows.move_to_end(state)
            return self._rows[state]
        # A failure arc removes the temporary bonus, but keeps complete matches.
        potential = self.potential[state]
        row = self._root_bonus - potential
        suffixes = []
        while state:
            suffixes.append(state)
            state = self._failure[state]
        for suffix in reversed(suffixes):
            for edge in range(self._offsets[suffix], self._offsets[suffix + 1]):
                token, child = self._tokens[edge], self._targets[edge]
                row[token] = self.potential[child] - potential + self.output[child]
        # A fixed limit prevents the row cache from becoming a dense tree.
        self._rows[suffixes[0]] = row
        if len(self._rows) > 32:
            self._rows.popitem(last=False)
        return row

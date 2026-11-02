"""Token prefix graph with temporary bonuses for incomplete names."""

from collections import deque
from collections.abc import Mapping, Sequence

import numpy as np
from numpy.typing import NDArray


def tokenize_names(
    names: Sequence[str], vocabulary: Mapping[int, str]
) -> list[tuple[int, ...]]:
    pieces = sorted(
        (piece.replace("▁", " "), token)
        for token, piece in vocabulary.items()
        if piece and not piece.startswith("<")
    )
    phrases = []
    for name in names:
        target = " " + name
        paths: dict[int, tuple[int, ...]] = {len(target): ()}
        for position in range(len(target) - 1, -1, -1):
            candidates = [
                (token,) + paths[position + len(piece)]
                for piece, token in pieces
                if target.startswith(piece, position) and position + len(piece) in paths
            ]
            if candidates:
                paths[position] = min(candidates, key=lambda ids: (len(ids), ids))
        if 0 in paths:
            phrases.append(paths[0])
    return phrases


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
        self.transitions: NDArray[np.int32] = np.zeros(
            (len(children), vocabulary_size), np.int32
        )
        for token, child in children[0].items():
            self.transitions[0, token] = child
        queue = deque(children[0].values())
        while queue:
            state = queue.popleft()
            self.transitions[state] = self.transitions[failure[state]]
            output[state] += output[failure[state]]
            for token, child in children[state].items():
                failure[child] = int(self.transitions[failure[state], token])
                self.transitions[state, token] = child
                queue.append(child)
        self.potential: NDArray[np.float32] = np.array(
            [0 if complete else d for complete, d in zip(output, depth, strict=True)],
            np.float32,
        )
        # A failure arc removes the temporary bonus, but keeps complete matches.
        self.increments: NDArray[np.float32] = (
            self.potential[self.transitions]
            - self.potential[:, None]
            + np.asarray(output, np.float32)[self.transitions]
        )

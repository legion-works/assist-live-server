# Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES.  All rights reserved.
# Copyright 2017 Johns Hopkins University (Shinji Watanabe)
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# Modified NumPy translation of NeMo TDT beam search. See THIRD_PARTY_NOTICES.md.
"""NeMo default TDT beam search for the pinned Parakeet runtime."""

from dataclasses import dataclass
from typing import Any

import numpy as np
from numpy.typing import NDArray
from onnx_asr.models.nemo import NemoConformerRnnt


@dataclass(eq=False)
class Hypothesis:
    score: float
    tokens: tuple[int, ...]
    state: Any
    frame: int


def merge_duplicates(hypotheses: list[Hypothesis]) -> list[Hypothesis]:
    # NeMo tdt_beam_decoding.py:708–717 sums paths with the same tokens and frame.
    merged: dict[tuple[tuple[int, ...], int], Hypothesis] = {}
    for hypothesis in sorted(hypotheses, key=lambda h: h.score, reverse=True):
        key = hypothesis.tokens, hypothesis.frame
        if key in merged:
            merged[key].score = float(
                np.logaddexp(
                    np.float32(merged[key].score), np.float32(hypothesis.score)
                )
            )
        else:
            merged[key] = hypothesis
    return list(merged.values())


def sort_nbest(hypotheses: list[Hypothesis]) -> list[Hypothesis]:
    # NeMo tdt_beam_decoding.py:377–379,823–824 counts the initial blank.
    return sorted(hypotheses, key=lambda h: h.score / (len(h.tokens) + 1), reverse=True)


def _log_softmax(logits: NDArray[np.float32]) -> NDArray[np.float32]:
    maximum = logits.max()
    return logits - (maximum + np.log(np.exp(logits - maximum).sum()))


def search(
    asr: Any, encoded: NDArray[np.float32], length: int, width: int = 16
) -> list[Hypothesis]:
    if width < 1:
        raise ValueError("TDT beam width must be positive")
    beam = min(width, asr._vocab_size - 1)
    token_count = min(beam, asr._vocab_size - 2)
    kept = [Hypothesis(0.0, (), asr._create_state(), 0)]
    cache = {}
    for frame in range(length):
        current = [h for h in kept if h.frame == frame]
        kept = [h for h in kept if h.frame > frame]
        while current:
            best = max(current, key=lambda h: h.score)
            current.remove(best)
            key = best.tokens, frame
            if key not in cache:
                logits, _, state = NemoConformerRnnt._decode(
                    asr, list(best.tokens), best.state, encoded[frame]
                )
                cache[key] = (
                    _log_softmax(logits[: asr._vocab_size]),
                    _log_softmax(logits[asr._vocab_size :]),
                    state,
                )
            token_scores, duration_scores, state = cache[key]
            tokens = np.argsort(token_scores[:-1])[-token_count:][::-1]
            durations = np.argsort(duration_scores)[-beam:][::-1]
            pairs = sorted(
                (
                    (float(token_scores[t] + duration_scores[d]), int(t), int(d))
                    for t in tokens
                    for d in durations
                ),
                reverse=True,
            )[:token_count]
            for score, token, duration in pairs:
                hypothesis = Hypothesis(
                    float(np.float32(best.score) + np.float32(score)),
                    best.tokens + (token,),
                    state,
                    frame + duration,
                )
                (current if duration == 0 else kept).append(hypothesis)
            # NeMo tdt_beam_decoding.py:428–454 permits zero steps only for tokens.
            for duration in durations:
                step = int(duration)
                if step == 0:
                    if len(durations) != 1:
                        continue
                    step = 1
                kept.append(
                    Hypothesis(
                        float(
                            np.float32(best.score)
                            + token_scores[asr._blank_idx]
                            + duration_scores[step]
                        ),
                        best.tokens,
                        best.state,
                        frame + step,
                    )
                )
            kept = merge_duplicates(kept)
            if current:
                threshold = max(h.score for h in current)
                superior = sorted(
                    (h for h in kept if h.score > threshold), key=lambda h: h.score
                )
                if len(superior) >= beam:
                    kept = superior
                    break
            else:
                kept = sorted(kept, key=lambda h: h.score, reverse=True)[:beam]
    return sort_nbest(kept)


def encode(model: Any, samples: NDArray[np.float32]) -> tuple[Any, Any, Any]:
    # onnx-asr 0.12.0 has no public encoder/joint API. The contract test guards it.
    asr = model.asr
    encoded, lengths = asr._encode(
        *asr._preprocessor(samples[None], np.array([len(samples)], np.int64))
    )
    return asr, encoded, lengths


def recognize(model: Any, samples: NDArray[np.float32]) -> str:
    asr, encoded, lengths = encode(model, samples)
    best = search(asr, encoded[0], int(lengths[0]))[0]
    return str(asr._decode_tokens(best.tokens, None, None).text).strip()

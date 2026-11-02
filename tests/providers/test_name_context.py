import asyncio
import gc
import random
import sys
import weakref
from collections import deque

import numpy as np
from assist_live_server.providers import parakeet_beam
from assist_live_server.providers.parakeet import ParakeetProvider

from .test_parakeet_beam import EndlessZeroJoint, fake_model


def graph(phrases):
    from assist_live_server.providers.name_context import NameContext

    return NameContext(phrases, 8)


def follow(context, tokens):
    state, score = 0, 0
    for token in tokens:
        if hasattr(context, "bonus_row"):
            score += float(context.bonus_row(state)[token])
            state = context.step(state, token)
        else:
            score += float(context.increments[state, token])
            state = int(context.transitions[state, token])
    return state, score


def test_bonus_on_continue_and_withdrawal_on_leave_and_finish():
    context = graph([(1, 2, 3)])
    assert follow(context, [1, 2])[1] == 2, "continue must give one bonus per token"
    assert follow(context, [1, 2, 7])[1] == 0, "leave must withdraw partial bonus"
    state, score = follow(context, [1, 2])
    assert score - context.potential[state] == 0, "finish must withdraw partial bonus"
    assert follow(context, [1, 2, 3, 7])[1] == 3, "complete name must keep its bonus"


def test_nested_names_and_shared_prefix_do_not_lose_completed_bonus():
    context = graph([(1, 2), (1, 2, 3), (1, 4)])
    state, score = follow(context, [1, 2, 7])
    assert score - context.potential[state] == 2
    state, score = follow(context, [1, 2, 3, 7])
    assert score - context.potential[state] == 5
    assert follow(context, [1, 4, 7])[1] == 2
    assert follow(context, [1, 7])[1] == 0


def test_failure_link_preserves_a_shared_suffix():
    context = graph([(1, 2, 3), (2, 4)])
    assert follow(context, [1, 2, 4, 7])[1] == 2


def test_empty_names_preserve_every_nbest_path():
    from assist_live_server.providers.name_context import NameContext

    asr = fake_model().asr
    encoded = np.array([[0], [1]], np.float32)
    baseline = parakeet_beam.search(asr, encoded, 2)
    empty = parakeet_beam.search(asr, encoded, 2, context=NameContext([], 3))
    assert [(h.tokens, h.score, h.frame) for h in empty] == [
        (h.tokens, h.score, h.frame) for h in baseline
    ], "empty hints must keep n-best paths bit-exact"


def test_hints_only_change_final_not_partials(monkeypatch):
    calls = []

    def beam(model, samples, **kwargs):
        calls.append(kwargs)
        return "hinted final"

    monkeypatch.setattr(parakeet_beam, "recognize", beam)

    async def run():
        provider = ParakeetProvider(fake_model())
        session = provider.session(hints=("command",))
        session.speech_started()
        await session.push(bytes(12800))
        assert await session.partial() == "filler", "partials must not use hint bonus"
        assert not calls, "partials must not use the hinted final decoder"
        assert await session.finish() == "hinted final"
        assert len(calls) == 1 and calls[0]["context"]

    asyncio.run(run())


class DenseReference:
    def __init__(self, phrases, size):
        children, failure, depth, output = [{}], [0], [0], [0]
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
        self.transitions = np.zeros((len(children), size), np.int32)
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
        self.potential = np.array(
            [0 if out else d for out, d in zip(output, depth, strict=True)], np.float32
        )
        self.increments = (
            self.potential[self.transitions]
            - self.potential[:, None]
            + np.asarray(output, np.float32)[self.transitions]
        )

    def bonus_row(self, state):
        return self.increments[state]

    def step(self, state, token):
        return int(self.transitions[state, token])


def retained_bytes(value, seen=None):
    seen = set() if seen is None else seen
    if id(value) in seen:
        return 0
    seen.add(id(value))
    size = sys.getsizeof(value)
    if isinstance(value, dict):
        size += sum(
            retained_bytes(k, seen) + retained_bytes(v, seen) for k, v in value.items()
        )
    elif isinstance(value, list | tuple):
        size += sum(retained_bytes(v, seen) for v in value)
    elif hasattr(value, "__dict__"):
        size += retained_bytes(vars(value), seen)
    return size


def test_three_spellings_credit_capitals_digits_and_unicode_once():
    from assist_live_server.providers.name_context import NameContext, tokenize_names

    vocab = dict(
        enumerate(
            [
                " test",
                " Test",
                " light",
                " Light",
                " bedroom",
                " Bedroom",
                " 2",
                " küche",
                " Küche",
                "<blk>",
            ]
        )
    )
    paths = tokenize_names(("test light", "bedroom 2", "küche"), vocab)
    assert set(paths) == {(0, 2), (1, 2), (1, 3), (4, 6), (5, 6), (7,), (8,)}, (
        "THREE_SPELLINGS_REQUIRED"
    )
    assert len(paths) == len(set(paths)), "SPELLING_PATHS_MUST_BE_UNIQUE"
    context = NameContext(paths, len(vocab))
    assert follow(context, (1,))[1] == 1, "SHARED_SPELLING_POSITION_CREDIT_ONCE"
    for path in set(paths):
        state, credit = follow(context, path)
        assert credit - context.potential[state] == len(path), (
            "COMPLETE_SPELLING_CREDIT_ONCE"
        )
    assert follow(context, (1, 9))[1] == 0, "CAPITAL_PREFIX_WITHDRAWAL_REQUIRED"


def test_sparse_rows_and_search_equal_dense_reference_generated():
    from assist_live_server.providers.name_context import NameContext

    rng = random.Random(614)
    for _ in range(30):
        phrases = [
            tuple(rng.randrange(2) for _ in range(rng.randrange(1, 8)))
            for _ in range(rng.randrange(1, 20))
        ]
        sparse, dense = NameContext(phrases, 3), DenseReference(phrases, 3)
        assert hasattr(sparse, "bonus_row"), "SPARSE_ROW_API_REQUIRED"
        for state in range(len(dense.potential)):
            np.testing.assert_array_equal(
                sparse.bonus_row(state), dense.bonus_row(state)
            )
            assert [sparse.step(state, t) for t in range(3)] == [
                dense.step(state, t) for t in range(3)
            ], "SPARSE_FAILURE_LINKS_EQUAL_DENSE"
        for bonus in (0, 0.25, 1, 4):
            encoded = np.array([[0], [1], [2]], np.float32)
            left, right = [
                parakeet_beam.search(
                    fake_model().asr, encoded, 3, context=ctx, bonus=bonus
                )
                for ctx in (sparse, dense)
            ]
            assert [(h.tokens, h.score, h.frame) for h in left] == [
                (h.tokens, h.score, h.frame) for h in right
            ], "SPARSE_NBEST_SCORES_EQUAL_DENSE"


def test_500_names_tree_and_full_row_cache_stay_below_four_mb():
    from assist_live_server.providers.name_context import NameContext, tokenize_names

    names = tuple(f"lamp {i}" for i in range(500))
    pieces = [" lamp", " Lamp"] + [f" {i}" for i in range(500)]
    vocab = {**dict(enumerate(pieces)), 4096: "<blk>"}
    context = NameContext(tokenize_names(names, vocab), 4097)
    if hasattr(context, "bonus_row"):
        for state in range(len(context.potential)):
            context.bonus_row(state)
    assert retained_bytes(context) <= 4_000_000, "SPARSE_TREE_FOUR_MB_REQUIRED"


def test_maximum_length_lists_have_a_compact_tree_and_bounded_cache():
    from assist_live_server.hints import validate_hints
    from assist_live_server.providers.name_context import NameContext, tokenize_names

    rng = random.Random(640500)
    names = tuple(
        "a " + "".join(rng.choice("bcdefghijk") for _ in range(62)) for _ in range(500)
    )
    assert validate_hints(list(names)) == names
    vocabulary = dict(enumerate(" abcdefghijkABCDEFGHIJK")) | {1024: "<blk>"}
    paths = tokenize_names(names, vocabulary)
    context = NameContext(paths, 1025)
    for state in range(1, 34):
        context.bonus_row(state)
    assert retained_bytes(context) <= 4_000_000, "WORST_VALID_HINTS_FOUR_MB_REQUIRED"


def test_maximum_token_bound_for_all_1500_spelling_paths_fits_four_mb():
    from assist_live_server.providers.name_context import NameContext

    # The pinned vocabulary needs at most 97 tokens for each 64-character name.
    phrases = [
        (i % 1024, i // 1024) + tuple((i + j) % 1024 for j in range(95))
        for i in range(500 * 3)
    ]
    context = NameContext(phrases, 1025)
    for state in range(1, 34):
        context.bonus_row(state)
    assert len(context._rows) == 32, "WORST_CASE_MUST_INCLUDE_FULL_ROW_CACHE"
    assert len(context.potential) == 145_025, "WORST_CASE_MUST_KEEP_DISTINCT_PATHS"
    assert retained_bytes(context) <= 4_000_000, "ALL_VALID_LISTS_FOUR_MB_REQUIRED"
    remaining_nodes = 1 + 1500 * 97 - len(context.potential)
    assert retained_bytes(context) + 24 * remaining_nodes <= 4_000_000, (
        "UNIVERSAL_NODE_BOUND_FOUR_MB_REQUIRED"
    )


def test_distinct_satellite_snapshots_share_one_build_and_release_tree(monkeypatch):
    from assist_live_server.hints import HintList
    from assist_live_server.providers import name_context

    builds = []
    original = name_context.tokenize_names

    def tokenize(names, vocabulary):
        builds.append(names)
        return original(names, vocabulary)

    monkeypatch.setattr(name_context, "tokenize_names", tokenize)

    async def run():
        provider = ParakeetProvider(fake_model())
        first, second = HintList(("command",)), HintList(("command",))
        await asyncio.gather(
            provider.prepare_hints(first), provider.prepare_hints(second)
        )
        assert builds == [("command",)], "IDENTICAL_SATELLITES_ONE_BUILD"
        assert first._context is second._context, "IDENTICAL_SATELLITES_ONE_TREE"
        reference = weakref.ref(second._context)
        first = HintList(("filler",))
        await provider.prepare_hints(first)
        assert second._context is reference(), "OTHER_SATELLITE_TREE_UNCHANGED"
        assert first._context is not second._context, "CHANGED_LIST_HAS_ITS_OWN_TREE"
        del second
        gc.collect()
        assert reference() is None, "UNUSED_TREE_MUST_BE_FREED"

    asyncio.run(run())


def test_vocabulary_segmentation_uses_minimum_pieces_and_skips_unknown():
    from assist_live_server.providers.name_context import tokenize_names

    vocab = {0: " test", 1: " light", 2: " te", 3: "st", 4: "!", 5: "<blk>"}
    assert tokenize_names(("test light", "unknown"), vocab) == [(0, 1)]


def test_hint_search_keeps_zero_duration_cap_and_frame_budget():
    from assist_live_server.providers.name_context import NameContext

    joint = EndlessZeroJoint(vocabulary_size=3, call_budget=11)
    paths = parakeet_beam.search(
        fake_model(joint).asr,
        np.array([[0], [1]], np.float32),
        2,
        width=1,
        context=NameContext([(0, 1)], 3),
    )
    assert joint.calls == {0: 11, 1: 11}, "hinted search must keep frame budget"
    assert len(paths[0].tokens) == 22, "hinted search must cap zero-duration tokens"


def test_reserved_unbiased_slots_keep_eight_acoustic_alternatives():
    paths = [parakeet_beam.Hypothesis(100 - i, (i,), None, 1) for i in range(24)]
    bonuses = {(i,): 100.0 if i < 16 else 0.0 for i in range(24)}
    kept = parakeet_beam._hint_beam(paths, bonuses, 16)
    assert [h.tokens for h in kept] == [(i,) for i in range(8)] + [
        (i,) for i in range(16, 24)
    ], "RESERVED_UNBIASED_SLOTS_REQUIRED"
    assert len(set(kept)) == 16, "reserved places must not duplicate biased places"


def test_reserved_slots_fill_from_remaining_paths_without_duplicates():
    paths = [parakeet_beam.Hypothesis(-i, (i,), None, 1) for i in range(10)]
    kept = parakeet_beam._hint_beam(paths, {(i,): 0 for i in range(10)}, 16)
    assert kept == paths, "reserved places must fill from the remaining candidates"


def test_list_graph_builds_once_across_turns_and_updates_replace_it(monkeypatch):
    from assist_live_server.hints import HintList
    from assist_live_server.providers import name_context

    builds, contexts = [], []
    original = name_context.tokenize_names

    def tokenize(names, vocabulary):
        builds.append(names)
        return original(names, vocabulary)

    def recognize(model, samples, **kwargs):
        contexts.append(kwargs["context"])
        return "synthetic final"

    monkeypatch.setattr(name_context, "tokenize_names", tokenize)
    monkeypatch.setattr(parakeet_beam, "recognize", recognize)

    async def run():
        provider = ParakeetProvider(fake_model())
        first = HintList(("command",))
        update = HintList(("filler",))
        for hints in (first, first, update, first):
            session = provider.session(hints=hints)
            await session.push(b"\x01\0")
            assert await session.finish() == "synthetic final"
        assert builds == [("command",), ("filler",)], "BUILD_ONCE_PER_LIST_REQUIRED"
        assert contexts[0] is contexts[1] is contexts[3]
        assert contexts[2] is not contexts[0], "list updates must replace the graph"

    asyncio.run(run())

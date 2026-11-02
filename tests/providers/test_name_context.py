import asyncio

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

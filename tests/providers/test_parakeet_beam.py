import asyncio
import importlib.metadata
import inspect
import os
from types import SimpleNamespace

import numpy as np
import pytest
from assist_live_server.providers import parakeet_beam
from assist_live_server.providers.parakeet import ParakeetProvider
from onnx_asr.models.nemo import NemoConformerTdt


class Joint:
    def get_inputs(self):
        return [
            SimpleNamespace(name=name, shape=[1, 1, 1])
            for name in ("input_states_1", "input_states_2")
        ]

    def get_outputs(self):
        return [SimpleNamespace(name="outputs", shape=[1, 1, 1, 8])]

    def run(self, outputs, inputs):
        frame = int(inputs["encoder_outputs"].flat[0])
        previous = int(inputs["targets"][0][0])
        if frame == 0:
            probabilities = [0.44, 0.01, 0.55]
        elif previous == 0:
            probabilities = [0.001, 0.001, 0.998]
        else:
            probabilities = [0.01, 0.59, 0.40]
        logits = np.log(
            np.array(probabilities + [0.001, 0.999, 1e-30, 1e-30, 1e-30], np.float32)
        )
        return (
            logits[None, None, None],
            inputs["input_states_1"],
            inputs["input_states_2"],
        )


def fake_model(joint=None):
    asr = object.__new__(NemoConformerTdt)
    asr.config = {}
    asr.use_low_precision = False
    asr._vocab = {0: " command", 1: " filler", 2: "<blk>"}
    asr._vocab_size = 3
    asr._blank_idx = 2
    asr._decoder_joint = joint or Joint()
    asr._preprocessor = lambda audio, lengths: (audio, lengths)
    asr._encoder = SimpleNamespace(
        run=lambda outputs, inputs: (
            np.array([[[0, 1]]], np.float32),
            np.array([2], np.int64),
        )
    )

    def recognize(samples, sample_rate):
        assert sample_rate == 16000
        return next(
            asr.recognize_batch(samples[None], np.array([len(samples)], np.int64))
        ).text

    return SimpleNamespace(asr=asr, recognize=recognize)


def test_public_final_keeps_command_that_greedy_discards():
    async def run():
        session = ParakeetProvider(fake_model()).session()
        session.speech_started()
        await session.push(bytes(12800))
        assert await session.partial() == "filler", "partials must remain greedy"
        assert await session.finish() == "command", "final must use beam width 16"
        assert await session.finish() == "command"
        assert session.final_redecodes is True

    asyncio.run(run())


def test_duplicate_paths_sum_probabilities_only_at_the_same_frame():
    hypothesis = parakeet_beam.Hypothesis
    merged = parakeet_beam.merge_duplicates(
        [
            hypothesis(float(np.log(0.2)), (0,), "best state", 2),
            hypothesis(float(np.log(0.1)), (0,), "other state", 2),
            hypothesis(-4.0, (0,), "later", 3),
            hypothesis(-5.0, (1,), "other token", 2),
        ]
    )
    assert len(merged) == 3, "duplicate paths must merge"
    assert merged[0].score == pytest.approx(np.log(0.3)), "merge must use log-add-exp"
    assert merged[0].state == "best state"
    assert {(h.tokens, h.frame) for h in merged} == {((0,), 2), ((0,), 3), ((1,), 2)}


def test_partial_and_final_receive_identical_float32_samples(monkeypatch):
    seen = []

    def recognize(samples, sample_rate):
        assert sample_rate == 16000
        seen.append(samples.copy())
        return "greedy"

    def beam(model, samples):
        seen.append(samples.copy())
        return "beam"

    monkeypatch.setattr(parakeet_beam, "recognize", beam)

    async def run():
        session = ParakeetProvider(SimpleNamespace(recognize=recognize)).session()
        session.speech_started()
        await session.push(np.arange(-32768, 32768, dtype="<i2").tobytes())
        assert await session.partial() == "greedy"
        assert await session.finish() == "beam"
        assert all(x.dtype == np.float32 for x in seen)
        np.testing.assert_array_equal(seen[0], seen[1])

    asyncio.run(run())


def test_normalization_counts_initial_blank_and_runs_after_raw_pruning():
    hypothesis = parakeet_beam.Hypothesis
    short = hypothesis(-1.0, (), None, 2)
    long = hypothesis(-1.5, (0, 1), None, 2)
    middle = hypothesis(-1.1, (0,), None, 2)
    ranked = parakeet_beam.sort_nbest([short, middle, long])
    assert ranked == [long, middle, short], (
        "final ranking must normalize by tokens plus blank"
    )
    assert [h.score for h in ranked] == [-1.5, -1.1, -1.0]
    asr = fake_model().asr
    encoded = np.array([[0], [1]], np.float32)
    assert parakeet_beam.search(asr, encoded, 2, width=1)[0].tokens == (1,)
    assert parakeet_beam.search(asr, encoded, 2, width=16)[0].tokens == (0,)


class DurationJoint(Joint):
    def __init__(self, duration):
        self.duration = duration
        self.frames = []

    def run(self, outputs, inputs):
        frame = int(inputs["encoder_outputs"].flat[0])
        self.frames.append(frame)
        previous = int(inputs["targets"][0][0])
        token = [0.999, 0.0005, 0.0005] if previous == 2 else [0.0005, 0.0005, 0.999]
        durations = self.duration + [1e-30] * (5 - len(self.duration))
        logits = np.log(np.array(token + durations, np.float32))
        return logits, inputs["input_states_1"], inputs["input_states_2"]


def test_duration_skips_encoder_frames_and_width_one_matches_greedy():
    joint = DurationJoint([0.0001, 0.0001, 0.9998])
    model = fake_model(joint)
    model.asr._encoder = SimpleNamespace(
        run=lambda outputs, inputs: (
            np.array([[[0, 1, 2]]], np.float32),
            np.array([3], np.int64),
        )
    )
    samples = np.zeros(6400, np.float32)
    asr, encoded, lengths = parakeet_beam.encode(model, samples)
    best = parakeet_beam.search(asr, encoded[0], int(lengths[0]), width=1)[0]
    assert best.tokens == (0,)
    assert best.frame == 4, "duration must skip frames rather than clamp at input end"
    assert joint.frames == [0, 2], "duration two must not decode frame one"
    greedy_tokens, _, _ = next(asr._decoding(encoded, lengths))
    assert best.tokens == tuple(greedy_tokens)
    assert model.recognize(samples, sample_rate=16000) == "command"


def test_blank_paths_keep_the_previous_predictor_state():
    seen = []

    class BlankThenTokenJoint(Joint):
        def run(self, outputs, inputs):
            frame = int(inputs["encoder_outputs"].flat[0])
            state = inputs["input_states_1"]
            seen.append((frame, int(state.flat[0])))
            tokens = [0.999, 0.0005, 0.0005] if frame == 1 else [0.0005, 0.0005, 0.999]
            return (
                np.log(np.array(tokens + [0.001, 0.999], np.float32)),
                state + 1,
                state + 1,
            )

    model = fake_model(BlankThenTokenJoint())
    best = parakeet_beam.search(
        model.asr, np.array([[0], [1], [2]], np.float32), 3, width=1
    )[0]
    assert best.tokens == (0,)
    assert seen == [(0, 0), (1, 0), (2, 1)], (
        "only nonblank paths update predictor state"
    )


def test_zero_duration_emits_token_without_advancing_or_updating_blank_state():
    joint = DurationJoint([0.999, 0.001])
    model = fake_model(joint)
    best = parakeet_beam.search(model.asr, np.array([[0]], np.float32), 1, width=1)[0]
    assert best.tokens == (0,), "zero-duration token must stay at the same frame"
    assert best.frame == 1, "zero-duration blank must use the next positive duration"
    assert joint.frames == [0, 0]


class EndlessZeroJoint(Joint):
    def __init__(self, vocabulary_size, call_budget):
        self.vocabulary_size = vocabulary_size
        self.call_budget = call_budget
        self.calls = {}

    def run(self, outputs, inputs):
        frame = int(inputs["encoder_outputs"].flat[0])
        self.calls[frame] = self.calls.get(frame, 0) + 1
        assert self.calls[frame] <= self.call_budget, (
            "joint calls per frame must stay within width times eleven"
        )
        logits = np.full(self.vocabulary_size + 5, -10000, np.float32)
        logits[0] = 0
        logits[self.vocabulary_size] = 0
        logits[self.vocabulary_size + 2 :] = -20000
        return logits, inputs["input_states_1"], inputs["input_states_2"]


def test_endless_zero_duration_search_has_a_hard_frame_call_bound():
    joint = EndlessZeroJoint(vocabulary_size=6, call_budget=44)
    model = fake_model(joint)
    model.asr._vocab_size = 6
    model.asr._blank_idx = 5
    result = parakeet_beam.search(
        model.asr, np.array([[0], [1]], np.float32), 2, width=4
    )
    assert joint.calls[0] == 44, "adversarial frame must reach the hard call budget"
    assert all(count <= 44 for count in joint.calls.values())
    assert result and all(h.frame >= 2 for h in result), (
        "frame budget must return kept advancing hypotheses, not raise"
    )


@pytest.mark.parametrize("frames", [1, 2])
def test_zero_duration_cap_still_allows_a_positive_duration_token(frames):
    joint = EndlessZeroJoint(vocabulary_size=3, call_budget=11)
    result = parakeet_beam.search(
        fake_model(joint).asr,
        np.arange(frames, dtype=np.float32)[:, None],
        frames,
        width=1,
    )
    assert len(result[0].tokens) == 11 * frames, (
        "ten zero-duration tokens must be followed by a positive-duration token"
    )
    assert result[0].frame >= frames
    assert joint.calls == {frame: 11 for frame in range(frames)}


def test_zero_duration_cap_still_allows_blank_advance():
    class BlankAtCapJoint(EndlessZeroJoint):
        def run(self, outputs, inputs):
            logits, state1, state2 = super().run(outputs, inputs)
            if int(state1.flat[0]) == 10:
                logits[0] = -10000
                logits[2] = 0
            return logits, state1 + 1, state2 + 1

    joint = BlankAtCapJoint(vocabulary_size=3, call_budget=11)
    result = parakeet_beam.search(
        fake_model(joint).asr, np.array([[0]], np.float32), 1, width=1
    )
    assert len(result[0].tokens) == 10, "blank must still advance the capped hypothesis"
    assert result[0].frame == 1
    assert joint.calls == {0: 11}


class LayoutJoint(Joint):
    def __init__(self, width, duration_count=5):
        self.width = width
        self.duration_count = duration_count
        self.metadata_calls = 0
        self.joint_calls = 0

    def get_outputs(self):
        self.metadata_calls += 1
        return [SimpleNamespace(name="outputs", shape=[1, 1, 1, self.width])]

    def run(self, outputs, inputs):
        self.joint_calls += 1
        logits, state1, state2 = super().run(outputs, inputs)
        return logits[..., : 3 + self.duration_count], state1, state2


@pytest.mark.parametrize("width,duration_count", [(7, 5), (9, 5), ("dynamic", 4)])
def test_encode_rejects_an_unexpected_duration_layout(width, duration_count):
    model = fake_model(LayoutJoint(width, duration_count))
    with pytest.raises(ValueError, match="Parakeet TDT duration layout") as error:
        parakeet_beam.encode(model, np.zeros(6400, np.float32))
    assert type(error.value).__name__ == "ParakeetDurationLayoutError"


@pytest.mark.parametrize("width,expected_calls", [(8, 0), ("dynamic", 1)])
def test_encode_validates_duration_layout_once_per_model(width, expected_calls):
    joint = LayoutJoint(width)
    model = fake_model(joint)
    for _ in range(2):
        parakeet_beam.encode(model, np.zeros(6400, np.float32))
    assert joint.metadata_calls == 1, "duration layout must be checked once per model"
    assert joint.joint_calls == expected_calls, (
        "only a symbolic joint width needs one validation inference"
    )


def reference_search(asr, encoded, length, width):
    """Keep Python tuple selection as the reference for candidate order."""
    hypothesis = parakeet_beam.Hypothesis
    beam = min(width, asr._vocab_size - 1)
    token_count = min(beam, asr._vocab_size - 2)
    kept = [hypothesis(0.0, (), asr._create_state(), 0)]
    cache = {}
    for frame in range(length):
        current = [h for h in kept if h.frame == frame]
        kept = [h for h in kept if h.frame > frame]
        for _ in range(beam * 11):
            if not current:
                break
            best = max(current, key=lambda h: h.score)
            current.remove(best)
            key = best.tokens, frame
            if key not in cache:
                logits, _, state = parakeet_beam.NemoConformerRnnt._decode(
                    asr, list(best.tokens), best.state, encoded[frame]
                )
                cache[key] = (
                    parakeet_beam._log_softmax(logits[: asr._vocab_size]),
                    parakeet_beam._log_softmax(logits[asr._vocab_size :]),
                    state,
                )
            token_scores, duration_scores, state = cache[key]
            tokens = np.argsort(token_scores[:-1])[-token_count:][::-1]
            durations = np.argsort(duration_scores)[-beam:][::-1]
            token_durations = durations
            if best.zero_duration_tokens >= 10:
                token_durations = np.argsort(duration_scores[1:])[-beam:][::-1] + 1
            pairs = sorted(
                (
                    (float(token_scores[t] + duration_scores[d]), int(t), int(d))
                    for t in tokens
                    for d in token_durations
                ),
                reverse=True,
            )[:token_count]
            for score, token, duration in pairs:
                item = hypothesis(
                    float(np.float32(best.score) + np.float32(score)),
                    best.tokens + (token,),
                    state,
                    frame + duration,
                    best.zero_duration_tokens + 1 if duration == 0 else 0,
                )
                (current if duration == 0 else kept).append(item)
            for duration in durations:
                step = int(duration)
                if step == 0:
                    if len(durations) != 1:
                        continue
                    step = 1
                kept.append(
                    hypothesis(
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
            kept = parakeet_beam.merge_duplicates(kept)
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
        else:
            kept = sorted(kept, key=lambda h: h.score, reverse=True)[:beam]
    return parakeet_beam.sort_nbest(kept)


class RandomJoint(Joint):
    def __init__(self, seed):
        self.seed = seed
        self.trace = []

    def run(self, outputs, inputs):
        frame = int(inputs["encoder_outputs"].flat[0])
        previous = int(inputs["targets"][0][0])
        state = int(inputs["input_states_1"].flat[0])
        self.trace.append((frame, previous, state))
        rng = np.random.default_rng([self.seed, frame, previous, state])
        logits = rng.normal(0, 2, 24).astype(np.float32)
        if self.seed % 3 == 0:
            logits[19] += 12
        if self.seed % 3 == 1:
            logits = np.round(logits)
        next_state = np.full_like(
            inputs["input_states_1"], (state * 19 + previous + 1) % 10007
        )
        return logits, next_state, next_state


@pytest.mark.parametrize("width", [1, 4, 16])
@pytest.mark.parametrize("seed", range(200))
def test_random_joint_preserves_baseline_tokens_frames_scores_and_pop_order(
    seed, width
):
    joint = RandomJoint(seed)
    asr = fake_model(joint).asr
    asr._vocab_size = 19
    asr._blank_idx = 18
    length = 1 + seed % 4
    encoded = np.arange(length, dtype=np.float32)[:, None]
    expected = reference_search(asr, encoded, length, width)
    expected_trace = joint.trace.copy()
    joint.trace.clear()
    actual = parakeet_beam.search(asr, encoded, length, width)
    assert joint.trace == expected_trace, "joint calls must keep baseline pop order"
    assert [(h.tokens, h.frame, h.zero_duration_tokens) for h in actual] == [
        (h.tokens, h.frame, h.zero_duration_tokens) for h in expected
    ]
    np.testing.assert_allclose(
        [h.score for h in actual], [h.score for h in expected], atol=1e-5, rtol=0
    )
    for left, right in zip(actual, expected, strict=True):
        for state, expected_state in zip(left.state, right.state, strict=True):
            np.testing.assert_array_equal(state, expected_state)


def test_zero_duration_child_precedes_queued_hypotheses():
    class ChildFirstJoint(Joint):
        def __init__(self):
            self.previous = []

        def run(self, outputs, inputs):
            previous = int(inputs["targets"][0][0])
            self.previous.append(previous)
            logits = np.array([0, -4, -5, -6, -7, -20, 0, -30], np.float32)
            return logits, inputs["input_states_1"], inputs["input_states_2"]

    joint = ChildFirstJoint()
    asr = fake_model(joint).asr
    asr._vocab_size = 6
    asr._blank_idx = 5
    parakeet_beam.search(asr, np.array([[0]], np.float32), 1, width=4)
    assert joint.previous[:3] == [5, 0, 0], (
        "a new zero-duration child must precede the queued lower-score paths"
    )


def test_equal_pair_scores_keep_descending_token_then_duration_order():
    class TiedJoint(Joint):
        def run(self, outputs, inputs):
            logits = np.array([0, 0, 0, 0, 0, -100, -100, 0, 0, 0, 0], np.float32)
            return logits, inputs["input_states_1"], inputs["input_states_2"]

    asr = fake_model(TiedJoint()).asr
    asr._vocab_size = 6
    asr._blank_idx = 5
    result = parakeet_beam.search(asr, np.array([[0]], np.float32), 1, width=4)
    assert [(h.tokens, h.frame) for h in result] == [
        ((4,), 4),
        ((4,), 3),
        ((4,), 2),
        ((4,), 1),
    ], "equal scores must select the larger token, then the larger duration"


def test_zero_token_candidates_preserve_blank_only_baseline():
    class BlankOnlyJoint(Joint):
        def run(self, outputs, inputs):
            logits = np.array([0, 0, -100, 0, -100, -100, -100], np.float32)
            return logits, inputs["input_states_1"], inputs["input_states_2"]

    asr = fake_model(BlankOnlyJoint()).asr
    asr._vocab_size = 2
    asr._blank_idx = 1
    encoded = np.array([[0]], np.float32)
    expected = reference_search(asr, encoded, 1, width=1)
    actual = parakeet_beam.search(asr, encoded, 1, width=1)
    assert [(h.tokens, h.frame, h.score) for h in actual] == [
        (h.tokens, h.frame, h.score) for h in expected
    ], "zero token candidates must not select any token pair"


def test_pinned_private_api_contract():
    assert importlib.metadata.version("onnx-asr") == "0.12.0"
    expected = {
        "_create_state": ["self"],
        "_encode": ["self", "features", "features_lens"],
        "_decode_tokens": ["self", "ids", "indices", "logprobs"],
        "_decode": ["self", "prev_tokens", "prev_state", "encoder_out"],
    }
    for name, parameters in expected.items():
        assert (
            list(inspect.signature(getattr(NemoConformerTdt, name)).parameters)
            == parameters
        )
    assert (
        list(inspect.signature(parakeet_beam.NemoConformerRnnt._decode).parameters)
        == expected["_decode"]
    )
    assert list(inspect.signature(NemoConformerTdt._decoding).parameters) == [
        "self",
        "encoder_out",
        "encoder_out_lens",
        "kwargs",
    ]


def test_real_pinned_adapter_matches_public_greedy_on_synthetic_audio():
    cache = os.environ.get("PARAKEET_MODEL_CACHE")
    if cache is None:
        pytest.skip("set PARAKEET_MODEL_CACHE for the pinned model contract")

    async def run():
        provider = await ParakeetProvider.load(cache)
        samples = np.random.default_rng(42).normal(0, 0.01, 24000).astype(np.float32)
        asr, encoded, lengths = parakeet_beam.encode(provider._model, samples)
        members = (
            "_preprocessor",
            "_encode",
            "_create_state",
            "_vocab_size",
            "_blank_idx",
            "_decode_tokens",
            "_decoder_joint",
        )
        assert all(hasattr(asr, member) for member in members)
        metadata = asr._decoder_joint.get_outputs()
        output = next(item for item in metadata if item.name == "outputs")
        assert output.shape[-1] == asr._vocab_size + 5
        assert callable(asr._preprocessor)
        assert isinstance(asr._vocab_size, int)
        assert asr._blank_idx == asr._vocab_size - 1
        state = asr._create_state()
        assert len(state) == 2 and all(x.dtype == np.float32 for x in state)
        logits, _, next_state = parakeet_beam.NemoConformerRnnt._decode(
            asr, [], state, encoded[0, 0]
        )
        assert logits.shape == (asr._vocab_size + 5,)
        assert [x.shape for x in state] == [x.shape for x in next_state]
        tokens, _, _ = next(asr._decoding(encoded, lengths))
        public = provider._model.recognize(samples, sample_rate=16000)
        assert asr._decode_tokens(tokens, None, None).text == public
        original = asr._decode_tokens
        observed = []

        def capture(ids, indices, logprobs):
            ids = list(ids)
            observed.append(ids)
            return original(ids, indices, logprobs)

        asr._decode_tokens = capture
        try:
            assert provider._model.recognize(samples, sample_rate=16000) == public
            assert observed == [list(tokens)], (
                "adapter and public greedy tokens must agree"
            )
        finally:
            asr._decode_tokens = original

    asyncio.run(run())

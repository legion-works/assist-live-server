import numpy as np
import pytest
from assist_live_server.turn.detector import TurnDetector


def detector(profile="safe", probability=1):
    return TurnDetector(
        profile,
        vad_probability=lambda audio: float(np.any(audio)),
        turn_probability=lambda _: probability,
    )


def feed(d, ms, voice, partial="Yeah."):
    events = []
    for offset in range(0, ms, 10):
        count = min(10, ms - offset) * 16
        block = np.full(count, int(voice), dtype="<i2").tobytes()
        events.extend(d.push(block, partial))
        events.extend(d.flush(partial))
    return events


@pytest.mark.parametrize("profile", ["safe", "fast"])
def test_wake_tail_and_pause_do_not_end_before_command(profile):
    d = detector(profile)
    assert feed(d, 300, True) == []
    assert feed(d, 600, False) == []
    assert d.speech_started_at_ms is None
    assert d.silent_samples == 0
    assert feed(d, 800, True) == []
    assert d.speech_started_at_ms == 900
    assert feed(d, 399, False) == []
    assert feed(d, 1, False) == [
        {
            "type": "end_decision",
            "reason": "early",
            "audio_ms": 2100,
            "silence_ms": 400,
        }
    ]


def test_two_seconds_before_command_do_not_use_maximum_silence():
    d = detector(probability=0)
    assert feed(d, 2000, False, "the") == []
    assert d.silent_samples == 0
    assert feed(d, 800, True, "the") == []
    assert d.speech_started_at_ms == 2000
    assert feed(d, 1499, False, "the") == []
    assert feed(d, 1, False, "the")[0] == {
        "type": "end_decision",
        "reason": "max_silence",
        "audio_ms": 4300,
        "silence_ms": 1500,
    }


@pytest.mark.parametrize("profile", ["safe", "fast"])
@pytest.mark.parametrize("chunk_ms", [10, 5000])
def test_no_command_wait_is_exactly_five_seconds_of_audio(profile, chunk_ms):
    calls = []
    d = TurnDetector(
        profile,
        vad_probability=lambda audio: calls.append(len(audio)) or 0,
        turn_probability=lambda _: pytest.fail("turn model before command"),
    )
    events = []
    for offset in range(0, 4999, chunk_ms):
        events.extend(d.push(bytes(min(chunk_ms, 4999 - offset) * 32), "Yeah."))
    events.extend(d.flush("Yeah."))
    assert events == []
    assert d.speech_started_at_ms is None
    assert d.silent_samples == 0
    assert d.flush("Yeah.") == []
    events = d.push(bytes(32), "Yeah.") + d.flush("Yeah.")
    assert events == [
        {
            "type": "end_decision",
            "reason": "max_silence",
            "audio_ms": 5000,
            "silence_ms": 0,
        }
    ]
    assert calls and set(calls) == {512}


def test_command_crossing_guard_starts_at_first_voiced_frame():
    d = detector()
    assert feed(d, 100, False) == []
    assert feed(d, 300, True) == []
    assert d.speech_started_at_ms is None
    assert feed(d, 1, True) == []
    assert d.speech_started_at_ms == 100
    assert feed(d, 499, True) == []
    assert feed(d, 400, False)[0]["audio_ms"] == 1300


def test_two_hundred_ms_after_guard_is_not_a_command():
    d = detector()
    assert feed(d, 400, False) == []
    assert feed(d, 200, True) == []
    assert d.speech_started_at_ms is None
    assert feed(d, 1000, False) == []
    assert feed(d, 249, True) == []
    assert d.speech_started_at_ms is None
    assert feed(d, 1, True) == []
    assert d.speech_started_at_ms == 1600


def test_follow_up_first_end_has_at_most_four_hundred_ms_extra_wait():
    d = detector()
    assert feed(d, 800, True) == []
    assert d.speech_started_at_ms == 0
    events = feed(d, 800, False)
    assert len(events) == 1
    assert events[0]["reason"] == "early"
    assert 1200 <= events[0]["audio_ms"] <= 1600


@pytest.mark.parametrize("voice_ms", [1000, 20000])
def test_open_voice_run_at_wait_qualifies_instead_of_ending(voice_ms):
    d = detector()
    assert feed(d, 4900, False) == []
    assert feed(d, 100, True) == []
    assert d.samples == 5000 * 16
    assert d.speech_started_at_ms is None
    assert feed(d, 150, True) == []
    assert d.speech_started_at_ms == 4900
    events = feed(d, voice_ms - 250, True)
    if voice_ms == 1000:
        assert events == []
        events = feed(d, 400, False)
        assert events == [
            {
                "type": "end_decision",
                "reason": "early",
                "audio_ms": 6300,
                "silence_ms": 400,
            }
        ]
    else:
        assert events == [
            {
                "type": "end_decision",
                "reason": "hard",
                "audio_ms": 15000,
                "silence_ms": 0,
            }
        ]


def test_open_voice_run_that_resets_after_wait_ends_at_reset_block():
    d = detector()
    assert feed(d, 4900, False) == []
    assert feed(d, 150, True) == []
    assert d.speech_started_at_ms is None
    assert d.push(bytes(1024), "Yeah.") == [
        {
            "type": "end_decision",
            "reason": "max_silence",
            "audio_ms": 5082,
            "silence_ms": 0,
        }
    ]
    assert d.speech_started_at_ms is None


def test_flush_consumes_open_run_residual_at_five_second_deadline():
    d = detector()
    assert feed(d, 4900, False) == []
    assert d.push(np.ones(99 * 16, dtype="<i2").tobytes(), "Yeah.") == []
    assert d.samples == 4996 * 16
    assert d.push(np.ones(16, dtype="<i2").tobytes(), "Yeah.") == []
    assert d.samples == 4996 * 16
    assert len(d.pending) == 4 * 32
    assert d.flush("Yeah.") == []
    assert d.samples == 5000 * 16
    assert d.pending == b""
    assert d.speech_started_at_ms is None
    assert d.flush("Yeah.") == []
    assert d.samples == 5000 * 16


@pytest.mark.parametrize("voice_ms", [600, 900])
def test_initial_continuous_command_keeps_its_full_run_start(voice_ms):
    d = detector()
    assert feed(d, voice_ms, True) == []
    assert d.speech_started_at_ms == 0
    assert feed(d, 400, False) == [
        {
            "type": "end_decision",
            "reason": "early",
            "audio_ms": voice_ms + 400,
            "silence_ms": 400,
        }
    ]


def test_two_hundred_fifty_ms_run_crossing_guard_qualifies():
    d = detector()
    assert feed(d, 200, False) == []
    assert feed(d, 249, True) == []
    assert d.speech_started_at_ms is None
    assert feed(d, 1, True) == []
    assert d.speech_started_at_ms == 200
    assert feed(d, 400, False)[0]["audio_ms"] == 850


def test_two_hundred_ms_run_crossing_guard_does_not_qualify():
    d = detector()
    assert feed(d, 300, False) == []
    assert feed(d, 200, True) == []
    assert d.speech_started_at_ms is None
    assert feed(d, 4490, False) == []
    assert feed(d, 10, False)[0]["audio_ms"] == 5000


def test_voice_run_ending_at_guard_is_ignored():
    d = detector()
    assert feed(d, 400, True) == []
    assert d.speech_started_at_ms is None
    assert feed(d, 600, False) == []
    assert d.speech_started_at_ms is None

import asyncio
import time

import numpy as np
import pytest
from assist_live_client import TimingEvent, TurnOpen
from assist_live_server.store import SQLiteStore, safe_data

TURN = "01900000-0000-7000-8000-000000000001"


def event(stage, source="server", data=None, turn_id=TURN):
    return TimingEvent(
        turn_id, "satellite-a", source, stage, 1, "measured", None, data=data or {}
    )


FIELDS = [
    ("fallback_used", "reason", "health_timeout", "unknown"),
    ("fallback_used", "part", "stt", "stt_fallback"),
    ("end_decision", "reason", "early", "unknown"),
    ("end_decision", "decision_source", "server", "device"),
    ("satellite_state", "state", "listening", "unknown"),
    ("speech_start", "onset_ms", 32, -1),
    ("tts_first_byte", "request", 0, -1),
    ("tts_sentence", "index", 0, -1),
    ("tts_sentence", "text_end", 10, -1),
    ("tts_sentence", "text_ms", 10, -1),
    ("tts_sentence", "first_byte_ms", 20, -1),
    ("tts_sentence", "audio_ms", 30, -1),
    ("tts_last_byte", "sentences", 2, -1),
    ("tts_last_byte", "audio_ms", 60, -1),
]


@pytest.mark.parametrize("stage,key,valid,invalid", FIELDS)
def test_bounded_field_round_trip(stage, key, valid, invalid):
    assert safe_data(event(stage, data={key: valid})) == {key: valid}, (
        "safe_data_dropped_field",
        stage,
        key,
    )


@pytest.mark.parametrize("stage,key,valid,invalid", FIELDS)
@pytest.mark.parametrize("kind", ["out_of_list", "wrong_type"])
def test_bounded_field_refuses_invalid(stage, key, valid, invalid, kind):
    value = invalid if kind == "out_of_list" else True
    with pytest.raises(ValueError, match="invalid event data"):
        event(stage, data={key: value})


@pytest.mark.parametrize("value", [float("nan"), float("inf"), "1", None])
def test_sentence_time_refuses_nonfinite_or_nonnumber(value):
    with pytest.raises(ValueError, match="invalid event data"):
        event("tts_sentence", data={"text_ms": value})


PUBLIC_FALLBACK_REASONS = """health_timeout health_error server_unhealthy
stt_unsupported tts_unsupported not_local_only destination_not_local open_refused
open_error stream_refused stream_closed stream_error message_timeout
fallback_unavailable""".split()


def test_public_fallback_reasons_match_shared_table():
    from assist_live_client.models import FALLBACK_REASONS

    assert set(PUBLIC_FALLBACK_REASONS) == set(FALLBACK_REASONS), (
        "fallback_reason_list_matches_shared_table"
    )


@pytest.mark.parametrize("reason", PUBLIC_FALLBACK_REASONS)
def test_all_fallback_reasons_are_kept(reason):
    assert safe_data(event("fallback_used", data={"reason": reason})) == {
        "reason": reason
    }


@pytest.mark.parametrize("reason", ["early", "hard", "max_silence", "input_end"])
def test_all_detector_rules_are_kept(reason):
    assert safe_data(event("end_decision", data={"reason": reason})) == {
        "reason": reason
    }


@pytest.mark.parametrize("state", ["idle", "listening", "processing", "responding"])
def test_all_public_satellite_states_are_kept(state):
    assert safe_data(event("satellite_state", data={"state": state})) == {
        "state": state
    }


@pytest.mark.parametrize(
    "stage",
    [
        "tts_first_text",
        "tts_text_received",
        "tts_audio_received",
        "tts_first_byte",
        "tts_sentence",
        "tts_last_byte",
        "tts_failed",
    ],
)
def test_request_is_bounded_on_each_tts_event(stage):
    assert safe_data(event(stage, data={"request": 0})) == {"request": 0}
    for invalid in (-1, True, 1.5, "0", 2**64):
        with pytest.raises(ValueError, match="invalid event data"):
            event(stage, data={"request": invalid})


def test_optional_fields_and_null_position_remain_compatible():
    for stage, _, _, _ in FIELDS:
        event(stage)
    assert safe_data(event("tts_sentence", data={"text_end": None})) == {
        "text_end": None
    }


def test_owner_table_covers_every_stage():
    from assist_live_client import models

    assert hasattr(models, "STAGE_OWNERS"), "missing_shared_owner_table"
    assert set(models.STAGE_OWNERS) == set(models.STAGES)
    assert models.STAGE_OWNERS["tts_first_text"] == {
        "server": "integration",
        "fallback": "integration",
    }
    assert models.STAGE_OWNERS["tts_sentence"]["fallback"] is None


def test_server_owns_reclassified_unmatched_event(tmp_path):
    async def run():
        now = [10_000_000_000]
        store = SQLiteStore(tmp_path / "turns.db", now_ns=lambda: now[0])
        try:
            await store.append(event("mic_open", "device", turn_id=None))
            now[0] += 6_000_000_000
            await store.maintain()
            rows = [row async for row in store.export("satellite-a", 0)]
            assert rows[0]["event"]["stage"] == "unmatched"
            assert rows[0]["event"]["source"] == "server", "unmatched_wrong_owner"
            assert rows[0]["event"]["data"]["original_stage"] == "mic_open"
        finally:
            store.close()

    asyncio.run(run())


def test_bench_duplicate_copies_are_refused_by_owner(tmp_path):
    async def run():
        store = SQLiteStore(tmp_path / "turns.db")
        try:
            await store.open_turn(TurnOpen(TURN, "satellite-a", "voice", None, None))
            for stage in ("end_decision", "stt_final", "tts_first_byte"):
                await store.append(event(stage))
                with pytest.raises(ValueError, match="wrong_owner"):
                    await store.append(event(stage, "integration"))
            await store.append(event("tts_first_text", "integration"))
            with pytest.raises(ValueError, match="wrong_owner"):
                await store.append(event("tts_first_text"))
            for stage, source in (
                ("tts_text_received", "server"),
                ("stt_final_received", "integration"),
                ("tts_audio_received", "integration"),
            ):
                await store.append(event(stage, source))
            rows = (await store.get_turn(TURN))["events"]
            assert len(rows) == 7, "duplicate_owner_stage_stored"
            assert len({r["stage"] for r in rows}) == 7
            assert (await store.diagnostics())["wrong_owner_total"] == 4
        finally:
            store.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "part,stage", [("stt", "stt_final"), ("tts", "tts_first_byte")]
)
def test_fallback_requires_stored_marker_for_the_same_part(tmp_path, part, stage):
    async def run():
        store = SQLiteStore(tmp_path / "turns.db")
        try:
            await store.open_turn(TurnOpen(TURN, "satellite-a", "voice", None, None))
            for marker_part in (None, "tts" if part == "stt" else "stt", part):
                if marker_part:
                    await store.append(
                        event(
                            "fallback_used",
                            "integration",
                            {"part": marker_part, "reason": "stream_closed"},
                        )
                    )
                if marker_part != part:
                    refused = False
                    try:
                        await store.append(event(stage, "integration"))
                    except ValueError as error:
                        assert str(error) == "wrong_owner"
                        refused = True
                    assert refused, "fallback_path_not_required"
                else:
                    await store.append(event(stage, "integration"))
                    with pytest.raises(ValueError, match="wrong_owner"):
                        await store.append(event(stage, "server"))
                    assert (await store.get_turn(TURN))["events"][-1]["source"] == (
                        "integration"
                    ), "fallback_owner_not_stored"
        finally:
            store.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "delta,expected",
    [
        (-10_000_000_000, None),
        (-86_400_000_000_000, None),
        (-86_400_000_000_001, "late_open_too_old"),
        (1_000_000_000, None),
        (1_000_000_001, "late_open_in_future"),
    ],
)
def test_late_open_bounds_and_initial_time(tmp_path, delta, expected):
    from assist_live_client import models

    assert "opened_utc_ns" in models.TurnOpen.__dataclass_fields__, "late_open_missing"

    async def run():
        now = 2 * 86_400_000_000_000
        store = SQLiteStore(tmp_path / "turns.db", retention_days=1, now_ns=lambda: now)
        try:
            turn = TurnOpen(
                TURN, "satellite-a", "voice", None, None, opened_utc_ns=now + delta
            )
            if expected:
                with pytest.raises(ValueError, match=expected):
                    await store.open_turn(turn)
            else:
                await store.open_turn(turn)
                row = await store.get_turn(TURN)
                assert row["opened_utc_ns"] == now + delta, "initial_time_lost"
                assert row["late_open"] is (delta < 0)
                assert row["status"] == (
                    "incomplete" if delta < -120_000_000_000 else "open"
                )
        finally:
            store.close()

    asyncio.run(run())


def test_audio_onset_and_decision_fields():
    from assist_live_server.audio_gateway import AudioGateway
    from assist_live_server.providers.parakeet import ParakeetSession
    from assist_live_server.turn.detector import TurnDetector

    async def run():
        rows = []

        async def record(stage, data):
            rows.append((stage, data))

        async def audio():
            yield bytes(1024 * 3)
            yield np.ones(512 * 14, dtype="<i2").tobytes()

        gateway = AudioGateway(
            lambda _: ParakeetSession(lambda _: "test transcript"),
            detector_factory=lambda: TurnDetector(
                vad_probability=lambda a: float(np.any(a)), turn_probability=lambda _: 0
            ),
        )
        [r async for r in gateway.stt(TURN, audio(), record=record)]
        onset = next(d for s, d in rows if s == "speech_start")
        assert onset.get("onset_ms") == 96, "onset_off_by_frame"
        decision = next(d for s, d in rows if s == "end_decision")
        assert decision.get("decision_source") == "server", "decision_source_missing"
        assert decision["reason"] == "input_end"

    asyncio.run(asyncio.wait_for(run(), 3))


def test_tts_sentence_known_timing_and_last_byte(monkeypatch):
    from assist_live_client import TTSAudio, TTSFormat, TTSSentenceEnd
    from assist_live_server import tts_gateway

    clock = [1_000_000_000]
    monkeypatch.setattr(time, "monotonic_ns", lambda: clock[0])
    monkeypatch.setattr(time, "time_ns", lambda: clock[0] + 10_000_000_000)

    class Provider:
        async def stream(self, text, voice):
            yield TTSFormat("pcm_s16le", 16000, 1, 2)
            assert await anext(text) == "First. "
            clock[0] += 20_000_000
            yield TTSAudio(bytes(3200))
            clock[0] += 10_000_000
            yield TTSAudio(bytes(3200))
            clock[0] += 10_000_000
            yield TTSSentenceEnd(0, 6)
            assert await anext(text) == "Second. "
            clock[0] += 50_000_000
            yield TTSAudio(bytes(1600))
            yield TTSSentenceEnd(1, 14)
            with pytest.raises(StopAsyncIteration):
                await anext(text)

    async def run():
        rows = []

        async def text():
            yield "First. "
            clock[0] += 60_000_000
            yield "Second. "

        async def record(stage, data):
            rows.append((stage, data.copy()))

        [
            r
            async for r in tts_gateway.TTSGateway(lambda _: Provider()).stream(
                TURN, text(), "voice", record=record
            )
        ]
        sentences = [d for s, d in rows if s == "tts_sentence"]
        assert len(sentences) == 2, "sentence_timings_missing"
        assert all(
            value is None or type(value) is int
            for data in sentences
            for value in data.values()
        ), "sentence_data_not_numeric"
        for actual, expected in zip(
            sentences,
            [
                {
                    "index": 0,
                    "text_end": 6,
                    "text_ms": 0,
                    "first_byte_ms": 20,
                    "audio_ms": 200,
                    "t_utc_ns": 11_030_000_000,
                },
                {
                    "index": 1,
                    "text_end": 14,
                    "text_ms": 100,
                    "first_byte_ms": 150,
                    "audio_ms": 50,
                    "t_utc_ns": 11_150_000_000,
                },
            ],
            strict=True,
        ):
            assert {k: actual.get(k) for k in expected} == expected, (
                "sentence_timing_wrong"
            )
        last = next(d for s, d in rows if s == "tts_last_byte")
        assert last["sentences"] == 2 and last["audio_ms"] == 250, "last_byte_wrong"
        assert rows[0][0] == "tts_text_received", "server_first_text_not_renamed"
        assert all(d.get("request") == 0 for _, d in rows), "request_missing"

    asyncio.run(run())


def test_tts_sentence_cap_counts_all_sentences():
    from assist_live_client import TTSAudio, TTSFormat, TTSSentenceEnd
    from assist_live_server.tts_gateway import TTSGateway

    class Provider:
        async def stream(self, text, voice):
            [part async for part in text]
            yield TTSFormat("pcm_s16le", 16000, 1, 2)
            for i in range(258):
                yield TTSAudio(bytes(32))
                yield TTSSentenceEnd(i)

    async def run():
        rows = []

        async def record(stage, data):
            rows.append((stage, data.copy()))

        async def text():
            yield "Test. " * 258

        [
            r
            async for r in TTSGateway(lambda _: Provider()).stream(
                TURN, text(), "voice", record=record
            )
        ]
        assert sum(s == "tts_sentence" for s, _ in rows) == 256, "sentence_cap_broken"
        last = next(d for s, d in rows if s == "tts_last_byte")
        assert last["sentences"] == 258 and last["audio_ms"] == 258, "cap_lost_totals"

    asyncio.run(run())

import asyncio
import json
from pathlib import Path
from uuid import UUID

import pytest


def test_audio_network_order_and_bounds(turn_id):
    from assist_live_client.protocol import decode_audio, encode_audio

    frame = encode_audio(turn_id, 0x01020304, b"\x00\x01")
    assert frame == b"\x01" + UUID(turn_id).bytes + b"\x01\x02\x03\x04\x00\x01"
    assert decode_audio(frame, turn_id) == (0x01020304, b"\x00\x01")
    for invalid in [
        b"",
        frame[:20],
        b"\x02" + frame[1:],
        frame + b"x",
        frame[:21] + b"x" * 65536,
    ]:
        with pytest.raises(ValueError):
            decode_audio(invalid, turn_id)
    with pytest.raises(ValueError):
        decode_audio(frame, str(UUID(int=0)))


def test_control_schema_and_parser(turn_id):
    from assist_live_client.protocol import decode_control
    from jsonschema import Draft202012Validator

    root = Path(__file__).resolve().parents[2]
    schema = json.loads((root / "protocol/control.schema.json").read_text())
    validator = Draft202012Validator(schema)
    good = {
        "type": "negotiate",
        "version": 1,
        "turn_id": turn_id,
        "capabilities": ["stt"],
        "mode": "stt",
    }
    assert not list(validator.iter_errors(good))
    assert decode_control(json.dumps(good), turn_id) == good
    for invalid in [
        {**good, "version": 2},
        {**good, "extra": "bad"},
        {k: v for k, v in good.items() if k != "capabilities"},
        {**good, "capabilities": ["unknown"]},
        {**good, "version": True},
        {**good, "turn_id": turn_id.upper()},
        {**good, "turn_id": turn_id.replace("-", "")},
    ]:
        assert list(validator.iter_errors(invalid))
        with pytest.raises(ValueError):
            decode_control(json.dumps(invalid))
    for invalid in ['{"version":1,"version":2}', "[]", '{"x":NaN}', "x" * 16385]:
        with pytest.raises(ValueError):
            decode_control(invalid)


def test_models_reject_invalid_records(turn_id):
    from assist_live_client.models import TimingEvent, TurnOpen

    assert not TurnOpen(turn_id, "satellite-a", "voice", None, None).record_transcripts
    with pytest.raises(ValueError):
        TurnOpen("not-a-uuid", "satellite-a", "voice", None, None)
    with pytest.raises(ValueError):
        TimingEvent(turn_id, "satellite-a", "server", "unknown", 1, "measured", None)


def test_vector_digest_matches_document():
    import hashlib

    root = Path(__file__).resolve().parents[2]
    digest = hashlib.sha256(
        (root / "protocol/device-vectors.json").read_bytes()
    ).hexdigest()
    assert digest in (root / "docs/protocol.md").read_text()


def test_client_audio_and_speaker_round_trip(service, turn_id):
    async def run():
        from assist_live_client import TurnOpen

        async with service() as (client, state):
            await client.open_turn(
                TurnOpen(turn_id, "satellite-a", "voice", None, None)
            )

            async def audio():
                yield b"\x00\x00" * 4

            replies = [reply async for reply in client.stt(turn_id, audio())]
            assert [r["text"] for r in replies if r["type"] == "final"] == [
                "test transcript"
            ]
            speaker = next(r for r in replies if r["type"] == "speaker_result")
            assert speaker["name"] == "test-speaker"
            assert speaker["destination"] == "local"
            assert state.audio == b"\x00\x00" * 4

            async def text():
                yield "test text"

            chunks = [
                chunk async for chunk in client.tts(turn_id, text(), "test-voice")
            ]
            assert chunks == [b"\x01\x00"]

    asyncio.run(run())


def test_valid_control_rejects_a_different_expected_turn(turn_id):
    from uuid import uuid7

    from assist_live_client.protocol import decode_control
    from jsonschema import Draft202012Validator

    other = str(uuid7())
    assert other != turn_id
    message = {"type": "heartbeat", "version": 1, "turn_id": other}
    root = Path(__file__).resolve().parents[2]
    validator = Draft202012Validator(
        json.loads((root / "protocol/control.schema.json").read_text())
    )
    assert not list(validator.iter_errors(message))
    assert decode_control(json.dumps(message), other) == message
    with pytest.raises(ValueError, match="turn identifier mismatch"):
        decode_control(json.dumps(message), turn_id)


def test_json_depth_bound_ignores_quoted_delimiters():
    from assist_live_client.protocol import parse_json

    allowed = '{"a":' * 32 + "0" + "}" * 32
    assert isinstance(parse_json(allowed, 4096), dict)
    deeper = '{"a":' + allowed + "}"
    with pytest.raises(ValueError, match="depth"):
        parse_json(deeper, 4096)
    allowed_array = '{"a":' + "[" * 31 + "0" + "]" * 31 + "}"
    assert isinstance(parse_json(allowed_array, 4096), dict)
    deeper_array = '{"a":' + "[" * 32 + "0" + "]" * 32 + "}"
    with pytest.raises(ValueError, match="depth"):
        parse_json(deeper_array, 4096)
    value = {"text": '[{\\"' * 80}
    assert parse_json(json.dumps(value), 4096) == value


def test_canonical_json_firmware_strings_and_numbers():
    from assist_live_server.auth import canonical_event

    data = {
        "unicode": "é",
        "quote": '"\n\\\x01',
        "numbers": [0, 1.0, -0.0, 1e-7, 1e20],
        "array": [True, False, None],
    }
    value = {
        "version": 1,
        "device": "device-a",
        "boot": 1,
        "seq": 0,
        "device_ms": 1,
        "stage": "mic_open",
        "data": data,
    }
    expected = (
        r'{"array":[true,false,null],"numbers":[0,1.0,-0.0,1e-07,1e+20],'
        r'"quote":"\"\n\\\u0001","unicode":"é"}'
    ).encode()
    assert canonical_event(value).endswith(expected)

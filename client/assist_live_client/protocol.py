import json
import math
import struct
from typing import Any
from uuid import UUID

from .models import turn_uuid, unsigned

VERSION = 1
MAX_CONTROL = 16384
MAX_PCM = 32000
MAX_AUDIO = 21 + MAX_PCM
MESSAGE_DEADLINE = 0.6
HEARTBEAT_INTERVAL = 0.1
CAPABILITIES = frozenset({"stt", "tts", "speaker_result", "events"})


class ProtocolError(Exception):
    pass


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _constant(value: str) -> None:
    raise ValueError("non-finite JSON number")


def parse_json(raw: str | bytes, maximum: int) -> dict[str, Any]:
    if len(raw) > maximum:
        raise ValueError("message too large")
    if isinstance(raw, str) and len(raw.encode()) > maximum:
        raise ValueError("message too large")
    try:
        text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        depth = 0
        quoted = escaped = False
        for character in text:
            if quoted:
                if escaped:
                    escaped = False
                elif character == "\\":
                    escaped = True
                elif character == '"':
                    quoted = False
            elif character == '"':
                quoted = True
            elif character in "{[":
                depth += 1
                if depth > 32:
                    raise ValueError("JSON depth limit")
            elif character in "}]":
                depth -= 1
        value = json.loads(raw, object_pairs_hook=_pairs, parse_constant=_constant)
        if not isinstance(value, dict):
            raise ValueError("JSON object required")
        json.dumps(value, allow_nan=False)
        return value
    except (RecursionError, UnicodeError, OverflowError) as error:
        raise ValueError("invalid JSON") from error


CONTROL_FIELDS: dict[str, tuple[set[str], set[str]]] = {
    "negotiate": ({"mode", "capabilities"}, {"voice"}),
    "negotiated": ({"capabilities"}, set()),
    "partial": ({"text"}, set()),
    "final": ({"text"}, set()),
    "end_decision": (set(), set()),
    "heartbeat": (set(), set()),
    "error": ({"code"}, set()),
    "end": (set(), set()),
    "text": ({"text"}, set()),
    "speaker_result": ({"name", "distance", "destination"}, set()),
    "format": ({"codec", "rate", "channels", "width"}, set()),
    "sentence_end": ({"sentence"}, set()),
}


def decode_control(raw: str | bytes, turn_id: str | None = None) -> dict[str, Any]:
    value = parse_json(raw, MAX_CONTROL)
    kind = value.get("type")
    if not isinstance(kind, str) or kind not in CONTROL_FIELDS:
        raise ValueError("unknown control type")
    required, optional = CONTROL_FIELDS[kind]
    required = required | {"type", "version", "turn_id"}
    if not required <= value.keys() or value.keys() - required - optional:
        raise ValueError("invalid control fields")
    if type(value["version"]) is not int or value["version"] != VERSION:
        raise ValueError("unsupported protocol version")
    turn_uuid(value["turn_id"])
    if turn_id is not None and value["turn_id"] != turn_id:
        raise ValueError("turn identifier mismatch")
    if "capabilities" in value and (
        not isinstance(value["capabilities"], list)
        or not all(
            isinstance(c, str) and c in CAPABILITIES for c in value["capabilities"]
        )
        or len(set(value["capabilities"])) != len(value["capabilities"])
    ):
        raise ValueError("unsupported capability")
    if kind == "negotiate" and (
        value["mode"] not in {"stt", "tts"}
        or value["mode"] not in value["capabilities"]
    ):
        raise ValueError("unsupported mode")
    for key in ("text", "name", "destination", "voice", "codec", "code"):
        if key in value and not isinstance(value[key], str):
            raise ValueError("invalid text field")
    if kind == "speaker_result" and (
        value["destination"] not in {"local", "cloud"}
        or type(value["distance"]) not in {int, float}
        or not math.isfinite(value["distance"])
        or value["distance"] < 0
    ):
        raise ValueError("invalid speaker result")
    for key in ("rate", "channels", "width", "sentence"):
        if key in value:
            unsigned(value[key], 32)
    if kind == "format" and (
        value["codec"] != "pcm_s16le"
        or value["rate"] not in {16000, 22050}
        or value["channels"] != 1
        or value["width"] != 2
    ):
        raise ValueError("unsupported audio format")
    return value


def encode_control(kind: str, turn_id: str, **fields: object) -> dict[str, Any]:
    value = {"type": kind, "version": VERSION, "turn_id": turn_id, **fields}
    return decode_control(
        json.dumps(value, ensure_ascii=False, allow_nan=False), turn_id
    )


def encode_audio(turn_id: str, sequence: int, pcm: bytes) -> bytes:
    unsigned(sequence, 32)
    if not 0 < len(pcm) <= MAX_PCM or len(pcm) % 2:
        raise ValueError("invalid PCM length")
    return struct.pack("!B16sI", VERSION, UUID(turn_id).bytes, sequence) + pcm


def decode_audio(frame: bytes, turn_id: str) -> tuple[int, bytes]:
    if not 21 < len(frame) <= MAX_AUDIO or (len(frame) - 21) % 2:
        raise ValueError("invalid audio length")
    version, identifier, sequence = struct.unpack_from("!B16sI", frame)
    if version != VERSION or identifier != UUID(turn_id).bytes:
        raise ValueError("audio version or turn mismatch")
    return sequence, frame[21:]

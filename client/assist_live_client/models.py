import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal, NotRequired, TypedDict
from uuid import UUID

MAX_TURN_EVENTS = 1024
MAX_TURN_EVENT_BYTES = 1_048_576
# JSON adds 19 spaces for 10 event fields and 21 for 11 data fields.
# The bound is 1 MiB + 1024 * (40 + 2) + 4096 bytes for turn metadata.
MAX_TURN_RESPONSE = MAX_TURN_EVENT_BYTES + MAX_TURN_EVENTS * 42 + 4096

LEGACY_STAGES = frozenset(
    """wake_detected mic_open speech_start end_candidate end_decision
stt_partial stt_final speaker_result tts_first_text tts_first_byte playback_start
playback_end satellite_state fallback_used stt_failed tts_failed turn_superseded
unmatched barge_in late_result_queued late_result_delivering late_result_spoken
late_result_cancelled late_result_uncertain late_result_notify_sent
late_result_notify_failed late_result_expired""".split()
)
STAGES = LEGACY_STAGES | {
    "tts_sentence",
    "tts_last_byte",
    "stt_final_received",
    "tts_audio_received",
    "tts_text_received",
}

# See assist-live `docs/design.md`, "Measurement rules".
STAGE_OWNERS: dict[str, dict[str, str | None]] = {
    stage: {"server": owner, "fallback": fallback}
    for stages, owner, fallback in (
        (
            "wake_detected mic_open playback_start playback_end barge_in",
            "device",
            "device",
        ),
        (
            "speech_start end_candidate end_decision stt_partial stt_final "
            "stt_failed tts_failed",
            "server",
            "integration",
        ),
        (
            "tts_first_text satellite_state fallback_used turn_superseded "
            "stt_final_received tts_audio_received",
            "integration",
            "integration",
        ),
        ("tts_first_byte", "server", "integration"),
        ("tts_sentence tts_last_byte", "server", None),
        ("speaker_result tts_text_received unmatched", "server", "server"),
        (
            "late_result_queued late_result_delivering late_result_spoken "
            "late_result_cancelled late_result_uncertain late_result_notify_sent "
            "late_result_notify_failed late_result_expired",
            "integration",
            "integration",
        ),
    )
    for stage in stages.split()
}

FALLBACK_REASONS = frozenset(
    """health_timeout health_error server_unhealthy
stt_unsupported tts_unsupported not_local_only destination_not_local open_refused
open_error stream_refused stream_closed stream_error message_timeout""".split()
)
END_REASONS = frozenset({"early", "hard", "max_silence", "input_end"})
SATELLITE_STATES = frozenset({"idle", "listening", "processing", "responding"})
EVENT_CODES = {
    "fallback_used": {"reason": FALLBACK_REASONS, "part": frozenset({"stt", "tts"})},
    "end_decision": {
        "reason": END_REASONS,
        "decision_source": frozenset({"server", "ha_audio_end"}),
    },
    "satellite_state": {"state": SATELLITE_STATES},
}
EVENT_NUMBERS = {
    "speech_start": frozenset({"onset_ms"}),
    "tts_sentence": frozenset(
        {"index", "text_end", "text_ms", "first_byte_ms", "audio_ms"}
    ),
    "tts_last_byte": frozenset({"sentences", "audio_ms"}),
}


def identifier(value: object) -> str:
    if not isinstance(value, str) or not 1 <= len(value.encode()) <= 128:
        raise ValueError("invalid identifier")
    if not all(c.isascii() and (c.isalnum() or c in "-_") for c in value):
        raise ValueError("invalid identifier")
    return value


def turn_uuid(value: object) -> str:
    if (
        not isinstance(value, str)
        or UUID(value).version != 7
        or str(UUID(value)) != value
    ):
        raise ValueError("invalid turn identifier")
    return value


def unsigned(value: object, bits: int = 64) -> int:
    if type(value) is not int or not 0 <= value < 2**bits:
        raise ValueError("invalid unsigned integer")
    return value


def event_data(stage: str, data: object) -> None:
    if not isinstance(data, dict):
        raise ValueError("invalid event data")
    for key, values in EVENT_CODES.get(stage, {}).items():
        if key in data and (not isinstance(data[key], str) or data[key] not in values):
            raise ValueError("invalid event data")
    numbers = EVENT_NUMBERS.get(stage, frozenset())
    if stage.startswith("tts_"):
        numbers = numbers | {"request"}
    for key in numbers:
        if key not in data or (key == "text_end" and data[key] is None):
            continue
        if type(data[key]) is not int or not 0 <= data[key] < 2**64:
            raise ValueError("invalid event data")
    if "pre_roll_ms" in data and (
        stage != "mic_open"
        or type(data["pre_roll_ms"]) is not int
        or not 0 <= data["pre_roll_ms"] <= 1000
    ):
        raise ValueError("invalid event data")


@dataclass(frozen=True)
class TTSFormat:
    """The format of all audio in one TTS stream."""

    codec: str
    rate: int
    channels: int
    width: int


@dataclass(frozen=True)
class TTSAudio:
    data: bytes


@dataclass(frozen=True)
class TTSSentenceEnd:
    """All audio for this zero-based sentence index has arrived."""

    sentence: int
    text_end: int | None = None


type TTSItem = TTSFormat | TTSAudio | TTSSentenceEnd


@dataclass(frozen=True)
class TTSVoice:
    id: str
    name: str
    language: str

    def __post_init__(self) -> None:
        if any(
            not isinstance(value, str) or not value.strip()
            for value in (self.id, self.name, self.language)
        ):
            raise ValueError("invalid TTS voice")


@dataclass(frozen=True)
class HealthCapabilities:
    healthy: bool
    stt: bool = False
    tts: bool = False
    text_positions: bool = False
    local_only: bool | None = None
    destinations: Mapping[str, str] | None = None
    tts_voices: tuple[TTSVoice, ...] = ()
    tts_default_voice: str | None = None


@dataclass(frozen=True)
class TurnOpen:
    turn_id: str
    satellite: str
    turn_type: Literal["voice", "tts_only"]
    parent_turn_id: str | None
    conversation_id: str | None
    record_transcripts: bool = False
    opened_utc_ns: int | None = None

    def __post_init__(self) -> None:
        turn_uuid(self.turn_id)
        identifier(self.satellite)
        if self.opened_utc_ns is not None:
            unsigned(self.opened_utc_ns)
        if self.parent_turn_id is not None:
            turn_uuid(self.parent_turn_id)
        if self.turn_type not in {"voice", "tts_only"}:
            raise ValueError("invalid turn type")
        if type(self.record_transcripts) is not bool:
            raise ValueError("invalid transcript option")
        if self.conversation_id is not None and (
            not isinstance(self.conversation_id, str) or len(self.conversation_id) > 256
        ):
            raise ValueError("invalid conversation identifier")


class TurnRecord(TypedDict):
    turn_id: str
    satellite: str
    turn_type: Literal["voice", "tts_only"]
    parent_turn_id: str | None
    conversation_id: str | None
    record_transcripts: bool
    opened_utc_ns: NotRequired[int | None]
    late_open: NotRequired[bool]
    status: NotRequired[Literal["open", "complete", "incomplete"]]
    events: NotRequired[list[TimingRecord]]


class TimingRecord(TypedDict):
    turn_id: str | None
    satellite: str
    source: Literal["device", "ha_state", "integration", "server"]
    stage: str
    t_utc_ns: int
    quality: Literal["measured", "proxy", "estimate"]
    uncertainty_ms: float | None
    t_device_ms: int | None
    seq: int | None
    data: dict[str, object]


@dataclass(frozen=True)
class TimingEvent:
    turn_id: str | None
    satellite: str
    source: Literal["device", "ha_state", "integration", "server"]
    stage: str
    t_utc_ns: int
    quality: Literal["measured", "proxy", "estimate"]
    uncertainty_ms: float | None
    t_device_ms: int | None = None
    seq: int | None = None
    data: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.turn_id is not None:
            turn_uuid(self.turn_id)
        identifier(self.satellite)
        unsigned(self.t_utc_ns)
        if self.source not in {"device", "ha_state", "integration", "server"}:
            raise ValueError("invalid event source")
        if self.stage not in STAGES or self.quality not in {
            "measured",
            "proxy",
            "estimate",
        }:
            raise ValueError("invalid timing event")
        if self.uncertainty_ms is not None and (
            type(self.uncertainty_ms) not in {float, int}
            or not math.isfinite(self.uncertainty_ms)
            or self.uncertainty_ms < 0
        ):
            raise ValueError("invalid uncertainty")
        if self.quality == "estimate" and self.uncertainty_ms is None:
            raise ValueError("missing uncertainty")
        for value in (self.t_device_ms, self.seq):
            if value is not None:
                unsigned(value)
        event_data(self.stage, self.data)

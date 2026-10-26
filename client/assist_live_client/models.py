import math
from dataclasses import dataclass, field
from typing import Literal, NotRequired, TypedDict
from uuid import UUID

STAGES = frozenset(
    """wake_detected mic_open speech_start end_candidate end_decision
stt_partial stt_final speaker_result tts_first_text tts_first_byte playback_start
playback_end satellite_state fallback_used stt_failed tts_failed turn_superseded
unmatched barge_in late_result_queued late_result_delivering late_result_spoken
late_result_cancelled late_result_uncertain late_result_notify_sent
late_result_notify_failed late_result_expired""".split()
)


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
class HealthCapabilities:
    healthy: bool
    stt: bool = False
    tts: bool = False
    text_positions: bool = False


@dataclass(frozen=True)
class TurnOpen:
    turn_id: str
    satellite: str
    turn_type: Literal["voice", "tts_only"]
    parent_turn_id: str | None
    conversation_id: str | None
    record_transcripts: bool = False

    def __post_init__(self) -> None:
        turn_uuid(self.turn_id)
        identifier(self.satellite)
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
        if not isinstance(self.data, dict):
            raise ValueError("invalid event data")

"""Guarded endpoints with pinned local ONNX models and a bounded audio buffer."""

import hashlib
import importlib
import math
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.request import urlopen

import numpy as np
from numpy.typing import NDArray

from ..assets import load_asset_manifest
from .profiles import HARD_ENDPOINT_MS, PROFILES, Profile
from .transcript_guard import complete_partial

ort = importlib.import_module("onnxruntime")

Samples = NDArray[np.float32]
Probability = Callable[[Samples], float]
MODEL_IDS = ("silero-vad", "smart-turn-v3.2-cpu")
WINDOW = 128000


def model_records(manifest: Path | str | None = None) -> dict[str, dict[str, Any]]:
    document = load_asset_manifest(manifest)
    entries = document["download_plan"]
    if not isinstance(entries, list) or any(
        not isinstance(entry, dict) for entry in entries
    ):
        raise ValueError("model download plan must contain objects")
    plan = {entry["id"]: entry for entry in entries}
    records = {row["id"]: row for row in document["assets"]}
    if any(name not in plan or name not in records for name in MODEL_IDS):
        raise ValueError("both turn models must be in the download plan")
    selected = {}
    for name in MODEL_IDS:
        row = records[name]
        entry = plan[name]
        if sum(item["id"] == name for item in entries) != 1 or set(entry) != {
            "id",
            "url",
            "sha256",
            "path",
        }:
            raise ValueError("invalid or duplicate model download plan entry")
        if (
            not isinstance(row.get("revision"), str)
            or not row["revision"]
            or not isinstance(entry["sha256"], str)
            or len(entry["sha256"]) != 64
            or any(char not in "0123456789abcdef" for char in entry["sha256"])
            or any(entry[key] != row.get(key) for key in ("url", "sha256"))
        ):
            raise ValueError("model download plan pins differ from asset record")
        path = entry["path"]
        if (
            not isinstance(path, str)
            or not Path(path).parts
            or Path(path).is_absolute()
            or ".." in Path(path).parts
        ):
            raise ValueError("model download path must be relative to cache")
        selected[name] = {**row, **entry}
    return selected


def model_path(cache: Path, record: dict[str, Any]) -> Path:
    root = cache.resolve()
    path = (root / record["path"]).resolve()
    if not path.is_relative_to(root):
        raise ValueError("model download path must stay inside cache")
    return path


def verified_model(cache: Path, record: dict[str, Any]) -> Path:
    path = model_path(cache, record)
    if not path.is_file():
        raise ValueError(
            f"FAIL model cache: missing {record['id']}; run --fetch-models"
        )
    if hashlib.sha256(path.read_bytes()).hexdigest() != record["sha256"]:
        raise ValueError(f"model SHA-256 mismatch: {record['id']}")
    return path


def fetch_models(cache: Path | str, manifest: Path | str) -> None:
    cache = Path(cache)
    cache.mkdir(parents=True, exist_ok=True)
    for record in model_records(manifest).values():
        path = model_path(cache, record)
        if path.exists():
            verified_model(cache, record)
            continue
        with urlopen(record["url"], timeout=120) as response:
            data = response.read()
        if hashlib.sha256(data).hexdigest() != record["sha256"]:
            raise ValueError(f"downloaded model SHA-256 mismatch: {record['id']}")
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".download")
        temporary.write_bytes(data)
        temporary.replace(path)
        verified_model(cache, record)


def whisper_features(samples: Samples) -> Samples:
    samples = np.asarray(samples, dtype=np.float32)
    if samples.ndim != 1 or not np.isfinite(samples).all():
        raise ValueError("finite mono samples required")
    audio = np.pad(samples[-WINDOW:], (max(0, WINDOW - len(samples)), 0))
    audio = (audio - audio.mean()) / np.sqrt(audio.var() + 1e-7)
    # Slaney filters and the periodic Hann window match the frozen cost probe.
    mel_max = 15 + math.log(8000 / 1000) * (27 / math.log(6.4))
    mel = np.linspace(0, mel_max, 82)
    hz = np.where(
        mel < 15, mel * (200 / 3), 1000 * np.exp((mel - 15) * math.log(6.4) / 27)
    )
    frequencies = np.linspace(0, 8000, 201)
    differences = np.diff(hz)
    slopes = hz[None, :] - frequencies[:, None]
    filters = np.maximum(
        0,
        np.minimum(-slopes[:, :-2] / differences[:-1], slopes[:, 2:] / differences[1:]),
    )
    filters *= (2 / (hz[2:] - hz[:-2]))[None, :]
    audio64 = np.pad(audio, (200, 200), mode="reflect").astype(np.float64)
    frames = np.lib.stride_tricks.sliding_window_view(audio64, 400)[::160]
    spectrum = np.fft.rfft(frames * np.hanning(401)[:-1]).astype(np.complex64)
    power = np.abs(spectrum, dtype=np.float64) ** 2
    log_mel = np.log10(np.maximum(1e-10, filters.T @ power.T))[:, :-1]
    return np.asarray(
        ((np.maximum(log_mel, log_mel.max() - 8) + 4) / 4)[None], dtype=np.float32
    )


class ModelRuntime:
    def __init__(self, cache: Path | str, manifest: Path | str | None = None) -> None:
        records = model_records(manifest)
        paths = {
            name: verified_model(Path(cache), record)
            for name, record in records.items()
        }
        options = ort.SessionOptions()
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        self.vad = ort.InferenceSession(
            str(paths[MODEL_IDS[0]]),
            sess_options=options,
            providers=["CPUExecutionProvider"],
        )
        self.smart_turn = ort.InferenceSession(
            str(paths[MODEL_IDS[1]]),
            sess_options=options,
            providers=["CPUExecutionProvider"],
        )
        self.state = np.zeros((2, 1, 128), dtype=np.float32)
        self.context = np.zeros((1, 64), dtype=np.float32)

    def vad_probability(self, samples: Samples) -> float:
        if samples.shape != (512,):
            raise ValueError("Silero requires 512 samples at 16 kHz")
        inputs = np.concatenate((self.context, samples[None]), axis=1)
        probability, self.state = self.vad.run(
            None,
            {
                "input": inputs,
                "state": self.state,
                "sr": np.array(16000, dtype=np.int64),
            },
        )
        self.context = samples[None, -64:].copy()
        return float(probability.item())

    def turn_probability(self, samples: Samples) -> float:
        outputs = self.smart_turn.run(
            None, {"input_features": whisper_features(samples)}
        )
        return float(outputs[0].item())


class TurnDetector:
    """One turn per instance. Input is mono 16 kHz little-endian int16 PCM.

    Events use the consumed audio clock, not the wall clock. A final event stops
    this instance. Construct a new instance and model state for the next turn.
    """

    def __init__(
        self,
        profile: str | Profile = "safe",
        *,
        vad_probability: Probability | None = None,
        turn_probability: Probability | None = None,
        models: ModelRuntime | None = None,
    ) -> None:
        self.profile = PROFILES[profile] if isinstance(profile, str) else profile
        if vad_probability is None or turn_probability is None:
            if vad_probability is not None or turn_probability is not None:
                raise ValueError("inject both probability functions")
            if models is None:
                cache = os.environ.get("ASSIST_LIVE_MODEL_CACHE")
                if not cache:
                    raise ValueError("set ASSIST_LIVE_MODEL_CACHE")
                models = ModelRuntime(cache)
            vad_probability = models.vad_probability
            turn_probability = models.turn_probability
        self.vad_probability = vad_probability
        self.turn_probability = turn_probability
        self.pending = bytearray()
        self.audio = bytearray()
        self.samples = 0
        self.silent_samples = 0
        self.done = False

    def candidate(self, silence_ms: int, probability: float, partial: str) -> bool:
        return (
            self.profile.early_enabled
            and silence_ms >= self.profile.min_silence_ms
            and math.isfinite(probability)
            and 0 <= probability <= 1
            and probability >= self.profile.smart_turn_limit
            and complete_partial(partial)
        )

    def push(self, pcm: bytes, partial: str) -> list[dict[str, Any]]:
        if len(pcm) % 2:
            raise ValueError("PCM must contain whole int16 samples")
        if self.done:
            return []
        self.pending.extend(pcm)
        while (
            len(self.pending) >= 1024
            or self.samples + len(self.pending) // 2 >= HARD_ENDPOINT_MS * 16
        ):
            count = min(512, HARD_ENDPOINT_MS * 16 - self.samples)
            block = self.pending[: count * 2]
            del self.pending[: count * 2]
            self.samples += count
            self.audio.extend(block)
            del self.audio[: -WINDOW * 2]
            reason = ""
            if self.samples >= HARD_ENDPOINT_MS * 16:
                reason = "hard"
            else:
                samples = np.frombuffer(block, dtype="<i2").astype(
                    np.float32
                ) / np.float32(32768)
                voice = self.vad_probability(samples)
                if not math.isfinite(voice) or not 0 <= voice <= 1:
                    raise ValueError("invalid VAD probability")
                self.silent_samples = self.silent_samples + count if voice < 0.5 else 0
                silence_ms = self.silent_samples / 16
                if silence_ms >= self.profile.max_silence_ms:
                    reason = "max_silence"
                elif (
                    self.profile.early_enabled
                    and silence_ms >= self.profile.min_silence_ms
                    and complete_partial(partial)
                ):
                    audio = np.frombuffer(self.audio, dtype="<i2").astype(
                        np.float32
                    ) / np.float32(32768)
                    if self.candidate(
                        int(silence_ms), self.turn_probability(audio), partial
                    ):
                        reason = "early"
            if reason:
                self.done = True
                self.pending.clear()
                return [
                    {
                        "type": "end_decision",
                        "reason": reason,
                        "audio_ms": self.samples / 16,
                        "silence_ms": self.silent_samples / 16,
                    }
                ]
        return []

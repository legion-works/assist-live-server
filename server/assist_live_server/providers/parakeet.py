"""Parakeet compatible mode with complete-buffer decode after voice onset."""

import asyncio
import importlib
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any

import numpy as np

from ..assets import load_asset_manifest
from ..turn.detector import verified_model
from .stt import AudioLimitError, STTClosedError, run_decode

INTERVAL_BYTES = 12800
MAX_AUDIO_BYTES = 480000
MODEL_ID = "nvidia/parakeet-tdt-0.6b-v2"
MODEL_FILES = {
    "config.json",
    "vocab.txt",
    "encoder-model.int8.onnx",
    "decoder_joint-model.int8.onnx",
}


def _load_onnx(*args: Any, **kwargs: Any) -> Any:
    return importlib.import_module("onnx_asr").load_model(*args, **kwargs)


def _load_model(cache: Path, manifest: Path | str | None) -> Any:
    document = load_asset_manifest(manifest)
    candidate = document["stt_benchmark"]["parakeet"]
    asset = next(row for row in document["assets"] if row["id"] == "stt-parakeet")
    files = candidate["files"]
    if (
        candidate["model_id"] != MODEL_ID
        or candidate["revision"] != asset["revision"]
        or asset["status"] != "included"
        or len(files) != len(MODEL_FILES)
        or {row["file"] for row in files} != MODEL_FILES
    ):
        raise ValueError("Parakeet manifest selection or model files differ")
    for record in files:
        entries = [
            row
            for row in document["download_plan"]
            if Path(row["path"]).name == record["file"]
        ]
        if (
            len(entries) != 1
            or any(entries[0][key] != record[key] for key in ("url", "sha256"))
            or f"/resolve/{candidate['revision']}/" not in record["url"]
        ):
            raise ValueError("Parakeet manifest download pins differ")
        verified_model(cache, {**record, "path": record["file"], "id": record["file"]})
    ort = importlib.import_module("onnxruntime")
    options = ort.SessionOptions()
    options.intra_op_num_threads = 4
    options.inter_op_num_threads = 1
    return _load_onnx(
        "nemo-parakeet-tdt-0.6b-v2",
        cache,
        quantization="int8",
        sess_options=options,
        providers=["CPUExecutionProvider"],
        preprocessor_config={
            "use_numpy_preprocessors": True,
            "max_concurrent_workers": 1,
        },
    )


class ParakeetProvider:
    def __init__(self, model: Any) -> None:
        self._model = model

    @classmethod
    async def load(
        cls, cache: Path | str, manifest: Path | str | None = None
    ) -> ParakeetProvider:
        model = await run_decode(lambda: _load_model(Path(cache), manifest))
        return cls(model)

    def _decode(self, pcm: bytes) -> str:
        samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768
        return str(self._model.recognize(samples, sample_rate=16000)).strip()

    def session(self) -> ParakeetSession:
        return ParakeetSession(self._decode)


class ParakeetSession:
    final_redecodes = True

    def __init__(self, decode: Callable[[bytes], str]) -> None:
        self._decode = decode
        self._audio = bytearray()
        self._onset = False
        self._cancelled = False
        self._finished = False
        self._partial = ""
        self._decoded_bytes = 0
        self._decoding_bytes = 0
        self._pending = False
        self._partial_task: asyncio.Task[str] | None = None
        self._finishing = False
        self._finish_lock = asyncio.Lock()

    def _check_open(self) -> None:
        if self._cancelled:
            raise STTClosedError("STT session cancelled")

    def speech_started(self) -> None:
        self._check_open()
        self._onset = True

    def cancel(self) -> None:
        self._cancelled = True
        self._audio.clear()
        self._partial = ""

    async def push(self, pcm: bytes) -> None:
        self._check_open()
        if self._finished or self._finishing:
            raise STTClosedError("STT session finished or finishing")
        if len(pcm) % 2:
            raise ValueError("mono int16 PCM must contain complete samples")
        if len(self._audio) + len(pcm) > MAX_AUDIO_BYTES:
            raise AudioLimitError("STT audio exceeds 15 s; finish accepted audio")
        self._audio.extend(pcm)

    async def _decode_partials(self) -> str:
        try:
            while True:
                self._pending = False
                pcm = bytes(self._audio)
                self._decoding_bytes = len(pcm)
                text = await run_decode(partial(self._decode, pcm))
                self._check_open()
                self._partial = text
                self._decoded_bytes = len(pcm)
                if not self._pending:
                    return text
        finally:
            self._partial_task = None

    async def partial(self) -> str:
        try:
            self._check_open()
            if self._finished or self._finishing:
                return self._partial
            if self._partial_task is not None:
                if len(self._audio) - self._decoding_bytes >= INTERVAL_BYTES:
                    self._pending = True
            elif (
                self._onset and len(self._audio) - self._decoded_bytes >= INTERVAL_BYTES
            ):
                self._decoding_bytes = len(self._audio)
                self._partial_task = asyncio.create_task(self._decode_partials())
            else:
                return self._partial
            return await asyncio.shield(self._partial_task)
        except asyncio.CancelledError:
            self.cancel()
            if self._partial_task is not None:
                self._partial_task.cancel()
            raise

    async def finish(self) -> str:
        try:
            async with self._finish_lock:
                self._check_open()
                if self._finished:
                    return self._partial
                self._finishing = True
                self._pending = False
                try:
                    if self._partial_task is not None:
                        await asyncio.shield(self._partial_task)
                    pcm = bytes(self._audio)
                    text = (
                        await run_decode(lambda: self._decode(pcm), final=True)
                        if pcm
                        else ""
                    )
                    self._check_open()
                    self._partial = text
                    self._finished = True
                    self._audio.clear()
                    return text
                finally:
                    self._finishing = False
        except asyncio.CancelledError:
            self.cancel()
            if self._partial_task is not None:
                self._partial_task.cancel()
            raise

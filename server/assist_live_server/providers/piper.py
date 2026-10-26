"""Pinned Piper voice, native PCM, and sentence-completion markers."""

import importlib
import json
from collections.abc import AsyncGenerator, AsyncIterator
from functools import partial
from pathlib import Path
from typing import Any

from assist_live_client.models import TTSAudio, TTSFormat, TTSItem, TTSSentenceEnd
from assist_live_client.protocol import MAX_PCM

from ..assets import load_asset_manifest
from ..turn.detector import verified_model
from .tts import run_synthesis, sentences

VOICE = "en_US-lessac-medium"
VOICE_IDS = ("piper-lessac-medium", "piper-lessac-config")


def _load_voice(model: Path, config: Path) -> Any:
    piper = importlib.import_module("piper")
    configuration = importlib.import_module("piper.config")
    ort = importlib.import_module("onnxruntime")
    options = ort.SessionOptions()
    options.intra_op_num_threads = 4
    options.inter_op_num_threads = 1
    # PiperVoice.load does not expose SessionOptions in 1.8.0.
    return piper.PiperVoice(
        config=configuration.PiperConfig.from_dict(json.loads(config.read_text())),
        session=ort.InferenceSession(
            str(model), sess_options=options, providers=["CPUExecutionProvider"]
        ),
    )


def _load_model(cache: Path, manifest: Path | str | None) -> Any:
    document = load_asset_manifest(manifest)
    selection = document["tts_selection"]
    if any(
        selection[key] != value
        for key, value in (
            ("engine", "piper"),
            ("version", "1.8.0"),
            ("voice", VOICE),
            ("threads", 4),
        )
    ):
        raise ValueError("Piper manifest selection differs")
    runtime = next(row for row in document["assets"] if row["id"] == "piper-runtime")
    records = {row["id"]: row for row in runtime["components"]}
    paths = []
    for name, suffix in zip(VOICE_IDS, (".onnx", ".onnx.json"), strict=True):
        entries = [row for row in document["download_plan"] if row["id"] == name]
        row = records[name]
        if (
            len(entries) != 1
            or runtime["status"] != "included"
            or row["status"] != "included"
            or any(entries[0][key] != row[key] for key in ("url", "sha256"))
            or f"/resolve/{row['revision']}/" not in row["url"]
            or Path(entries[0]["path"]).name != VOICE + suffix
        ):
            raise ValueError("Piper manifest voice download pins differ")
        paths.append(verified_model(cache, {**entries[0], "path": VOICE + suffix}))
    return _load_voice(*paths)


class PiperProvider:
    provider_id = "piper"

    def __init__(self, model: Any) -> None:
        if model.config.sample_rate != 22050:
            raise ValueError("Piper selected voice must use 22050 Hz")
        self._model = model
        self.audio_format = TTSFormat("pcm_s16le", 22050, 1, 2)

    @classmethod
    async def load(
        cls, cache: Path | str, manifest: Path | str | None = None
    ) -> PiperProvider:
        model = await run_synthesis(lambda: _load_model(Path(cache), manifest))
        return cls(model)

    def _sentence(self, text: str) -> list[bytes]:
        result = []
        for chunk in self._model.synthesize(text):
            pcm = chunk.audio_int16_bytes
            for offset in range(0, len(pcm), MAX_PCM):
                result.append(pcm[offset : offset + MAX_PCM])
        return result

    async def stream(
        self, text: AsyncIterator[str], voice: str
    ) -> AsyncGenerator[TTSItem]:
        if voice != VOICE:
            raise ValueError(f"unknown TTS voice: {voice!r}")
        yield self.audio_format
        sentence_stream = sentences(text)
        try:
            index = 0
            async for sentence, text_end in sentence_stream:
                chunks = await run_synthesis(partial(self._sentence, sentence))
                for chunk in chunks:
                    yield TTSAudio(chunk)
                yield TTSSentenceEnd(index, text_end)
                index += 1
        finally:
            await sentence_stream.aclose()

    async def synthesize(
        self, text: AsyncIterator[str], voice: str
    ) -> AsyncIterator[bytes]:
        stream = self.stream(text, voice)
        try:
            async for item in stream:
                if isinstance(item, TTSAudio):
                    yield item.data
        finally:
            await stream.aclose()

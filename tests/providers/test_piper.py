import asyncio
import hashlib
import json
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from assist_live_client.models import TTSAudio, TTSFormat, TTSSentenceEnd
from assist_live_server.providers.piper import PiperProvider
from assist_live_server.providers.tts import provider_factory, provider_names

VOICE = "en_US-lessac-medium"


class Voice:
    config = SimpleNamespace(sample_rate=22050)

    def __init__(self):
        self.sentences = []

    def synthesize(self, text):
        self.sentences.append(text)
        yield SimpleNamespace(audio_int16_bytes=text.encode("utf-16le"))


async def parts(*values):
    for value in values:
        yield value


def test_first_sentence_starts_before_text_end():
    async def run():
        gate = asyncio.Event()
        finished = False

        async def text():
            nonlocal finished
            yield "First sentence. "
            await gate.wait()
            yield "Last fragment"
            finished = True

        voice = Voice()
        stream = PiperProvider(voice).synthesize(text(), VOICE)
        try:
            assert await asyncio.wait_for(anext(stream), 1)
            assert not finished
            gate.set()
            assert await anext(stream)
            with pytest.raises(StopAsyncIteration):
                await anext(stream)
            assert voice.sentences == ["First sentence.", "Last fragment"]
        finally:
            await stream.aclose()

    asyncio.run(run())


@pytest.mark.parametrize(
    ("chunks", "sentences"),
    [
        (("Hello", ".", " Next", "!", " ", "Tail"), ["Hello.", "Next!", "Tail"]),
        (("Set 3.", "5 degrees. ", "Done."), ["Set 3.5 degrees.", "Done."]),
        (
            ("Dr.", " Smith uses e.", "g. 3.5 units. ", "Yes? "),
            ["Dr. Smith uses e.g. 3.5 units.", "Yes?"],
        ),
        (("One?", '!" ', "Two... ", "Tail"), ['One?!"', "Two...", "Tail"]),
        ((" ", ""), []),
    ],
)
def test_split_boundaries_abbreviations_numbers_and_final_flush(chunks, sentences):
    async def run():
        voice = Voice()
        items = [
            item async for item in PiperProvider(voice).stream(parts(*chunks), VOICE)
        ]
        assert voice.sentences == sentences
        assert items[0] == TTSFormat("pcm_s16le", 22050, 1, 2)
        assert [i.sentence for i in items if isinstance(i, TTSSentenceEnd)] == list(
            range(len(sentences))
        )
        assert [
            i.data.decode("utf-16le") for i in items if isinstance(i, TTSAudio)
        ] == sentences

    asyncio.run(run())


@pytest.mark.parametrize("voice", ["", "unlisted", "en_US-lessac-high"])
def test_unknown_voice_fails_by_name_without_synthesis(voice):
    async def run():
        model = Voice()
        with pytest.raises(ValueError, match="unknown TTS voice") as error:
            await anext(PiperProvider(model).stream(parts("Hello. "), voice))
        assert repr(voice) in str(error.value)
        assert not model.sentences

    asyncio.run(run())


def test_only_piper_is_registered():
    assert provider_names() == ("piper",)
    assert provider_factory("piper") == PiperProvider.load
    with pytest.raises(ValueError, match="unknown TTS provider"):
        provider_factory("kokoro")


def test_running_cancel_stops_at_current_sentence_and_isolates_next_session():
    entered, release = threading.Event(), threading.Event()
    first_thread = threading.get_ident()

    class BlockedVoice(Voice):
        def synthesize(self, text):
            assert threading.get_ident() != first_thread
            if text == "Cancelled.":
                entered.set()
                assert release.wait(5)
            yield from super().synthesize(text)

    async def run():
        model = BlockedVoice()
        provider = PiperProvider(model)
        stream = provider.synthesize(parts("Cancelled. Never. "), VOICE)
        task = asyncio.create_task(anext(stream))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            next_stream = provider.synthesize(parts("Other. "), VOICE)
            next_audio = asyncio.create_task(anext(next_stream))
            await asyncio.sleep(0)
            assert not next_audio.done()
            release.set()
            assert await asyncio.wait_for(next_audio, 5) == "Other.".encode("utf-16le")
            await next_stream.aclose()
            assert model.sentences == ["Cancelled.", "Other."]
        finally:
            release.set()
            await stream.aclose()

    asyncio.run(run())


def test_tts_does_not_wait_for_queued_stt_partials():
    from assist_live_server.providers.stt import run_decode

    entered, release = threading.Event(), threading.Event()

    def blocked():
        entered.set()
        assert release.wait(5)

    async def run():
        tasks = [asyncio.create_task(run_decode(blocked))]
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            tasks.extend(asyncio.create_task(run_decode(blocked)) for _ in range(3))
            await asyncio.sleep(0)
            stream = PiperProvider(Voice()).synthesize(parts("Independent. "), VOICE)
            assert await asyncio.wait_for(anext(stream), 1)
            await stream.aclose()
        finally:
            release.set()
            await asyncio.gather(*tasks)

    asyncio.run(run())


def test_loader_checks_both_hashes_before_runtime(tmp_path, monkeypatch):
    from assist_live_server.providers import piper

    root = Path(__file__).resolve().parents[2]
    manifest = json.loads((root / "assets/manifest.json").read_text())
    records = [r for r in manifest["download_plan"] if r["id"] in piper.VOICE_IDS]

    def update(rows):
        for row in rows:
            if row["id"] in piper.VOICE_IDS:
                row["sha256"] = hashlib.sha256(row["id"].encode()).hexdigest()
            update(row.get("components", []))

    update(manifest["assets"])
    for row in records:
        row["sha256"] = hashlib.sha256(row["id"].encode()).hexdigest()
        (tmp_path / Path(row["path"]).name).write_bytes(row["id"].encode())
    pins = tmp_path / "manifest.json"
    pins.write_text(json.dumps(manifest))
    calls = []
    monkeypatch.setattr(
        piper, "_load_voice", lambda *args: calls.append(args) or Voice()
    )

    async def run():
        assert isinstance(await PiperProvider.load(tmp_path, pins), PiperProvider)
        assert len(calls) == 1
        for row in records:
            path = tmp_path / Path(row["path"]).name
            path.write_bytes(b"corrupt")
            with pytest.raises(ValueError, match="SHA-256"):
                await PiperProvider.load(tmp_path, pins)
            path.write_bytes(row["id"].encode())
        assert len(calls) == 1

    asyncio.run(run())


def test_audio_chunks_fit_wire_limit_and_preserve_bytes():
    class LargeVoice(Voice):
        def synthesize(self, text):
            yield SimpleNamespace(audio_int16_bytes=b"\x01\x02" * 40000)

    async def run():
        chunks = [
            chunk
            async for chunk in PiperProvider(LargeVoice()).synthesize(
                parts("Large. "), VOICE
            )
        ]
        assert all(0 < len(chunk) <= 32000 and len(chunk) % 2 == 0 for chunk in chunks)
        assert b"".join(chunks) == b"\x01\x02" * 40000

    asyncio.run(run())


def test_executor_bounds_cancelled_work_and_releases_all_slots():
    from assist_live_server.providers.tts import TTSBusyError, run_synthesis

    entered, release = threading.Event(), threading.Event()

    def active():
        entered.set()
        assert release.wait(5)

    async def run():
        tasks = [asyncio.create_task(run_synthesis(active))]
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            tasks.extend(
                asyncio.create_task(run_synthesis(lambda: "queued")) for _ in range(3)
            )
            await asyncio.sleep(0)
            with pytest.raises(TTSBusyError):
                await run_synthesis(lambda: "overflow")
            tasks[0].cancel()
            with pytest.raises(asyncio.CancelledError):
                await tasks[0]
            with pytest.raises(TTSBusyError):
                await run_synthesis(lambda: "running slot must stay occupied")
            tasks[1].cancel()
            with pytest.raises(asyncio.CancelledError):
                await tasks[1]
            replacement = asyncio.create_task(run_synthesis(lambda: "replacement"))
            await asyncio.sleep(0)
            release.set()
            assert await replacement == "replacement"
        finally:
            release.set()
            await asyncio.gather(*tasks, return_exceptions=True)
        for _ in range(5):
            assert await run_synthesis(lambda: "recovered") == "recovered"

    asyncio.run(run())


@pytest.mark.parametrize("opening", ['"', "'", "“", "‘", "(", "["])
def test_opening_quotes_and_brackets_do_not_break_abbreviations(opening):
    from assist_live_server.providers.tts import sentences

    closing = {'"': '"', "'": "'", "“": "”", "‘": "’", "(": ")", "[": "]"}[opening]
    text = f"{opening}Dr. Smith arrived late.{closing}, she noted."

    async def run():
        assert [s async for s in sentences(parts(*text))] == [text]

    asyncio.run(run())


def test_curly_quoted_abbreviation_from_reply():
    from assist_live_server.providers.tts import sentences

    text = "“Dr. Watson came home.”"

    async def run():
        assert [s async for s in sentences(parts(*text))] == [text]

    asyncio.run(run())


@pytest.mark.parametrize("prefix", ["", "word"])
def test_long_dotted_initial_token_keeps_its_full_prefix_meaning(prefix):
    from assist_live_server.providers.tts import sentences

    token = prefix + "a." * 100

    async def run():
        result = [s async for s in sentences(parts(*f"{token} next. "))]
        assert result == ([token, "next."] if prefix else [f"{token} next."])

    asyncio.run(run())


def test_unfinished_fragment_cap_survives_small_parts():
    from assist_live_server.providers.tts import sentences

    async def run():
        text = "a. " * 5461 + "a"
        assert len(text) == 16384
        assert [s async for s in sentences(parts(text))] == [text]
        with pytest.raises(ValueError, match="16384"):
            [s async for s in sentences(parts(text, "a"))]

    asyncio.run(run())


def _assembly_metrics(mode):
    from assist_live_server.providers.tts import sentences

    async def collect(size):
        text = ("a. " * ((size + 2) // 3))[:size]
        start = time.perf_counter()
        result = [s async for s in sentences(parts(*text))]
        elapsed = (time.perf_counter() - start) * 1000
        assert result == [text.strip()]
        return elapsed

    async def measure():
        if mode == "cap":
            return {"characters": 16383, "ms": await collect(16383)}
        if mode == "scaling":
            samples = {4000: [], 8000: []}
            for _ in range(3):
                for size in samples:
                    samples[size].append(await collect(size))
            small, large = (statistics.median(values) for values in samples.values())
            return {"n_ms": small, "2n_ms": large, "ratio": large / small}
        lags = []
        started, stopped = asyncio.Event(), asyncio.Event()

        async def heartbeat():
            target = time.perf_counter() + 0.1
            started.set()
            while not stopped.is_set():
                await asyncio.sleep(max(0, target - time.perf_counter()))
                lags.append(max(0, time.perf_counter() - target) * 1000)
                target += 0.1

        pulse = asyncio.create_task(heartbeat())
        await started.wait()
        batches = []
        until = time.perf_counter() + 1.2
        try:
            while time.perf_counter() < until:
                batches.append(await collect(16383))
                await asyncio.sleep(0.001)
        finally:
            stopped.set()
            await pulse
        return {
            "batches": len(batches),
            "max_ms": max(batches),
            "heartbeat_samples": len(lags),
            "lag_p99_ms": statistics.quantiles(lags, n=100, method="inclusive")[98],
        }

    return asyncio.run(measure())


def _isolated_assembly_metrics(mode):
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import json, runpy, sys; "
            "print(json.dumps(runpy.run_path(sys.argv[1])['_assembly_metrics'](sys.argv[2])))",
            str(Path(__file__).resolve()),
            mode,
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def test_abbreviation_dense_character_stream_stays_below_200_ms():
    measured = _isolated_assembly_metrics("cap")
    assert measured["ms"] < 200, measured


def test_character_stream_scaling_stays_below_three():
    measured = _isolated_assembly_metrics("scaling")
    assert measured["ratio"] < 3, measured


def test_abbreviation_dense_stream_keeps_heartbeat_lag_below_20_ms():
    measured = _isolated_assembly_metrics("heartbeat")
    assert measured["heartbeat_samples"] >= 10, measured
    assert measured["lag_p99_ms"] < 20, measured

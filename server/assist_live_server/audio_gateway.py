"""Keep one provider and one detector for each audio turn."""

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from assist_live_client.protocol import encode_control, encode_transcript

from .destinations import Destination, require_destination
from .hints import HintList
from .providers.parakeet import MAX_AUDIO_BYTES
from .providers.stt import STTSession
from .speaker import SpeakerPlugin
from .turn.detector import TurnDetector
from .turn.transcript_guard import transcript_words

TURN_TIMEOUT = 15.0
HARD_END_GRACE = 0.5
STALL_INTERVAL = 0.1
LOGGER = logging.getLogger(__name__)

type Recorder = Callable[[str, dict[str, object]], Awaitable[None]]


class AudioGateway:
    def __init__(
        self,
        provider_factory: Callable[[str], STTSession],
        *,
        provider: str = "parakeet",
        detector_factory: Callable[[], TurnDetector] = TurnDetector,
        destination: Destination | None = None,
        local_only: bool = True,
        speaker: SpeakerPlugin | None = None,
        hinted_factory: Callable[[HintList | tuple[str, ...]], STTSession]
        | None = None,
        hint_snapshot: Callable[[str], Awaitable[HintList]] | None = None,
    ) -> None:
        self.provider_factory = provider_factory
        self.provider = provider
        self.detector_factory = detector_factory
        self.destination: Destination = destination or "local"
        self.declared_destination = destination
        self.local_only = local_only
        self.speaker = speaker
        self.hinted_factory = hinted_factory
        self.hint_snapshot = hint_snapshot
        self.stt_hints_lookup_misses_total = 0
        self.stt_final_replaced_total = 0

    async def stt(
        self,
        turn_id: str,
        audio: AsyncIterator[bytes],
        *,
        record: Recorder | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        session: STTSession | None = None
        tasks: list[asyncio.Task[None]] = []
        output: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=16)
        changed = asyncio.Event()
        stopped = False
        partial = ""
        onset = False
        candidate = False
        total = 0
        speaker_audio = bytearray()

        async def note(stage: str, **data: object) -> None:
            if record is not None:
                await record(
                    stage,
                    {
                        "provider": self.provider,
                        "part": "stt",
                        **(
                            {"destination": self.declared_destination}
                            if self.declared_destination is not None
                            else {}
                        ),
                        **data,
                    },
                )

        async def decide(reason: str) -> None:
            nonlocal stopped
            if stopped:
                return
            stopped = True
            await note("end_decision", reason=reason, decision_source="server")
            await output.put(encode_control("end_decision", turn_id))

        async def work() -> None:
            nonlocal session, stopped
            try:
                # Select the provider before the iterator can supply its first frame.
                require_destination(self.destination, self.local_only)
                hints: HintList | tuple[str, ...] = ()
                if self.hinted_factory is not None:
                    try:
                        if self.hint_snapshot is None:
                            raise LookupError
                        hints = await self.hint_snapshot(turn_id)
                    except Exception as error:
                        self.stt_hints_lookup_misses_total += 1
                        LOGGER.debug("%s", type(error).__name__)
                session = (
                    self.hinted_factory(hints)
                    if self.hinted_factory is not None
                    else self.provider_factory(self.provider)
                )
                detector = await asyncio.to_thread(self.detector_factory)
                active_session = session
                pending_audio = bytearray()
                sent_bytes = 0
                in_flight: asyncio.Task[None] | None = None

                async def process_detection(pcm: bytes | None) -> None:
                    nonlocal onset, candidate, sent_bytes
                    if pcm is not None:
                        pending_audio.extend(pcm)
                    events = await asyncio.to_thread(
                        lambda: (
                            detector.flush(partial)
                            if pcm is None
                            else detector.push(pcm, partial)
                        )
                    )
                    # docs/design.md: no provider can receive audio after the end.
                    count = detector.samples * 2 - sent_bytes
                    if count:
                        await active_session.push(bytes(pending_audio[:count]))
                        del pending_audio[:count]
                        sent_bytes += count
                        changed.set()
                    if not onset and detector.speech_started_at_ms is not None:
                        onset = True
                        active_session.speech_started()
                        await note(
                            "speech_start", onset_ms=int(detector.speech_started_at_ms)
                        )
                        changed.set()
                    silence_ms = detector.silent_samples / 16
                    if silence_ms < detector.profile.min_silence_ms:
                        candidate = False
                    elif not candidate:
                        candidate = True
                        await note("end_candidate")
                    if events:
                        await decide(events[-1]["reason"])

                async def detect(pcm: bytes | None) -> None:
                    nonlocal in_flight
                    # Recovery must not race the detector or repeat a provider push.
                    in_flight = asyncio.create_task(process_detection(pcm))
                    await asyncio.shield(in_flight)
                    in_flight = None

                async def hard_end() -> None:
                    try:
                        async with asyncio.timeout(HARD_END_GRACE):
                            if in_flight is not None:
                                await asyncio.shield(in_flight)
                            await detect(None)
                    except TimeoutError:
                        # A running detector makes another flush unsafe.
                        pass
                    await decide("hard")

                async def collect() -> None:
                    nonlocal total
                    pending: asyncio.Task[bytes] | None = None

                    async def next_frame() -> bytes:
                        return await anext(audio)

                    try:
                        async with asyncio.timeout(TURN_TIMEOUT):
                            while not stopped:
                                if pending is None:
                                    pending = asyncio.create_task(next_frame())
                                done, _ = await asyncio.wait(
                                    {pending}, timeout=STALL_INTERVAL
                                )
                                if not done:
                                    await detect(None)
                                    continue
                                try:
                                    pcm = pending.result()
                                except StopAsyncIteration:
                                    await detect(None)
                                    await decide("input_end")
                                    break
                                pending = None
                                if len(pcm) % 2:
                                    raise ValueError("whole PCM samples required")
                                pcm = pcm[: MAX_AUDIO_BYTES - total]
                                for offset in range(0, len(pcm), 1024):
                                    block = pcm[offset : offset + 1024]
                                    if self.speaker is not None:
                                        speaker_audio.extend(block)
                                    total += len(block)
                                    await detect(block)
                                    if stopped:
                                        break
                                if total >= MAX_AUDIO_BYTES:
                                    await hard_end()
                    except TimeoutError:
                        await hard_end()
                    finally:
                        if in_flight is not None:
                            in_flight.cancel()
                            await asyncio.gather(in_flight, return_exceptions=True)
                        if pending is not None:
                            pending.cancel()
                            await asyncio.gather(pending, return_exceptions=True)

                async def decode() -> None:
                    nonlocal partial
                    while not stopped:
                        await changed.wait()
                        changed.clear()
                        if stopped:
                            break
                        text = await active_session.partial()
                        if not stopped and text and text != partial:
                            partial = text
                            await output.put(
                                encode_transcript("partial", turn_id, text)
                            )

                collector = asyncio.create_task(collect())
                decoder = asyncio.create_task(decode())
                tasks.extend([collector, decoder])
                done, _ = await asyncio.wait(
                    {collector, decoder}, return_when=asyncio.FIRST_COMPLETED
                )
                for task in done:
                    task.result()
                await collector
                text = ""
                replaced = False
                if onset:
                    text = await active_session.finish()
                else:
                    # docs/design.md: noise without a command must not become text.
                    decoder.cancel()
                    await asyncio.gather(decoder, return_exceptions=True)
                    active_session.cancel()
                    close = getattr(active_session, "aclose", None)
                    if close is not None:
                        await close()
                if onset and getattr(active_session, "final_redecodes", False) is True:
                    partial_words = transcript_words(partial)
                    final_words = transcript_words(text)
                    if (
                        len(partial_words) >= 2
                        and len(final_words) < len(partial_words)
                        and not set(final_words).intersection(partial_words)
                    ):
                        text = partial
                        replaced = True
                reply = encode_transcript("final", turn_id, text)
                if replaced:
                    self.stt_final_replaced_total += 1
                    # The route removes this internal marker before wire validation.
                    reply["_final_replaced"] = True
                await output.put(reply)
                if self.speaker is not None:
                    try:
                        require_destination(self.speaker.destination, self.local_only)
                        result = await self.speaker.identify(bytes(speaker_audio))
                        await output.put(
                            encode_control(
                                "speaker_result",
                                turn_id,
                                name=result.name,
                                distance=result.distance,
                                destination=result.destination,
                            )
                        )
                    except Exception:
                        # Speaker metadata is optional. A final transcript stays final.
                        pass
                else:
                    await output.put(
                        encode_control(
                            "speaker_result",
                            turn_id,
                            name="",
                            distance=0.0,
                            destination="local",
                        )
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                # Provider errors can contain text or credentials. Send a fixed code.
                await output.put(encode_control("error", turn_id, code="stt_failed"))
            finally:
                speaker_audio.clear()
                stopped = True
                if session is not None:
                    session.cancel()
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
            await output.put(None)

        worker = asyncio.create_task(work(), name="assist-live-stt")
        try:
            while (message := await output.get()) is not None:
                yield message
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)

"""Keep one provider and one detector for each audio turn."""

import asyncio
import logging
import threading
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from assist_live_client.protocol import encode_control, encode_transcript

from .destinations import Destination, require_destination
from .hints import HintList
from .metrics import EndDecisionWait
from .providers.parakeet import MAX_AUDIO_BYTES
from .providers.stt import STTBusyError, STTSession
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
        end_rule: str = "partial",
    ) -> None:
        if end_rule not in {"partial", "checkpoint"}:
            raise ValueError("invalid ASSIST_LIVE_END_RULE")
        self.end_rule = end_rule
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
        self.stt_partial_refusals_total = 0
        self.end_decision_wait = EndDecisionWait()
        self.checkpoint_deadline_failures_total = 0
        self.checkpoint_wall_limit_failures_total = 0

    def checkpoint_failure(self, cause: str) -> None:
        if cause == "deadline":
            self.checkpoint_deadline_failures_total += 1
        elif cause == "wall_limit":
            self.checkpoint_wall_limit_failures_total += 1
        else:
            raise ValueError(f"unknown checkpoint failure cause: {cause}")

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
        decision: str | None = None
        partial = ""
        onset = False
        candidate = False
        total = 0
        speaker_audio = bytearray()
        arrivals: list[tuple[int, float]] = []
        received_samples = 0
        detector: TurnDetector

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

        def select_end(reason: str) -> None:
            nonlocal decision, stopped
            if stopped:
                return
            stopped = True
            decision = reason
            if self.end_rule == "partial" and detector.samples:
                arrival = next(
                    (stamp for end, stamp in arrivals if end >= detector.samples),
                    None,
                )
                if arrival is not None:
                    self.end_decision_wait.observe(
                        asyncio.get_running_loop().time() - arrival
                    )

        async def publish_end(reason: str) -> None:
            await note("end_decision", reason=reason, decision_source="server")
            await output.put(encode_control("end_decision", turn_id))

        async def checkpoint_end(reason: str) -> None:
            select_end(reason)
            await publish_end(reason)

        async def work() -> None:
            nonlocal session, stopped, detector, partial, onset
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
                detector_finished = threading.Event()
                push_started = False

                async def push_detected_audio() -> None:
                    nonlocal sent_bytes, push_started
                    # docs/design.md: no provider can receive audio after the end.
                    count = detector.samples * 2 - sent_bytes
                    if count:
                        accepted = bytes(pending_audio[:count])
                        push_started = True
                        await active_session.push(accepted)
                        if self.speaker is not None:
                            speaker_audio.extend(accepted)
                        del pending_audio[:count]
                        sent_bytes += count
                        changed.set()

                async def process_detection(pcm: bytes | None) -> None:
                    nonlocal onset, candidate, push_started
                    if pcm is not None:
                        pending_audio.extend(pcm)
                    detector_finished.clear()
                    push_started = False

                    def detection() -> list[dict[str, Any]]:
                        events = (
                            detector.flush(partial)
                            if pcm is None
                            else detector.push(pcm, partial)
                        )
                        detector_finished.set()
                        return events

                    events = await asyncio.to_thread(detection)
                    await push_detected_audio()
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
                        select_end(events[-1]["reason"])

                async def detect(pcm: bytes | None) -> None:
                    nonlocal in_flight
                    # Recovery must not race the detector or repeat a provider push.
                    in_flight = asyncio.create_task(process_detection(pcm))
                    await asyncio.shield(in_flight)
                    in_flight = None

                async def hard_end() -> None:
                    nonlocal in_flight
                    try:
                        async with asyncio.timeout(HARD_END_GRACE) as grace_deadline:
                            if in_flight is not None:
                                await asyncio.shield(in_flight)
                            await detect(None)
                    except TimeoutError:
                        if not grace_deadline.expired():
                            raise
                        if in_flight is not None:
                            in_flight.cancel()
                            await asyncio.gather(in_flight, return_exceptions=True)
                            in_flight = None
                        # Cancellation cannot stop a thread or undo a provider push.
                        if detector_finished.is_set() and not push_started:
                            try:
                                async with asyncio.timeout(HARD_END_GRACE) as catchup:
                                    await push_detected_audio()
                            except TimeoutError:
                                if not catchup.expired():
                                    raise
                    select_end("hard")

                async def collect() -> None:
                    nonlocal total, received_samples
                    pending: asyncio.Task[bytes] | None = None
                    received_at = 0.0

                    async def next_frame() -> bytes:
                        nonlocal received_at
                        frame = await anext(audio)
                        received_at = asyncio.get_running_loop().time()
                        return frame

                    try:
                        async with asyncio.timeout(TURN_TIMEOUT) as wall_deadline:
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
                                    select_end("input_end")
                                    break
                                pending = None
                                received_samples += len(pcm) // 2
                                arrivals.append(
                                    (
                                        received_samples,
                                        received_at,
                                    )
                                )
                                if len(pcm) % 2:
                                    raise ValueError("whole PCM samples required")
                                pcm = pcm[: MAX_AUDIO_BYTES - total]
                                for offset in range(0, len(pcm), 1024):
                                    block = pcm[offset : offset + 1024]
                                    total += len(block)
                                    await detect(block)
                                    if stopped:
                                        break
                                if total >= MAX_AUDIO_BYTES:
                                    await hard_end()
                    except TimeoutError:
                        if not wall_deadline.expired():
                            raise
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
                        try:
                            text = await active_session.partial()
                        except STTBusyError:
                            self.stt_partial_refusals_total += 1
                            continue
                        if not stopped and text and text != partial:
                            partial = text
                            await output.put(
                                encode_transcript("partial", turn_id, text)
                            )

                decoder: asyncio.Task[None] | None = None
                if self.end_rule == "checkpoint":
                    from .checkpoint_turn import CheckpointSession, CheckpointTurn

                    async def publish(text: str) -> None:
                        nonlocal partial
                        if not stopped and text and text != partial:
                            partial = text
                            await output.put(
                                encode_transcript("partial", turn_id, text)
                            )

                    if not isinstance(active_session, CheckpointSession):
                        raise ValueError(
                            "checkpoint provider cannot decode exact prefixes"
                        )
                    turn = CheckpointTurn(
                        active_session,
                        detector,
                        note=note,
                        decide=checkpoint_end,
                        publish=publish,
                        failure=self.checkpoint_failure,
                        observe=self.end_decision_wait.observe,
                        speaker_audio=speaker_audio
                        if self.speaker is not None
                        else None,
                        limit=MAX_AUDIO_BYTES,
                    )
                    partial = await turn.run(
                        audio, timeout=TURN_TIMEOUT, grace=HARD_END_GRACE
                    )
                    onset = turn.onset
                else:
                    collector = asyncio.create_task(collect())
                    decoder = asyncio.create_task(decode())
                    tasks.extend([collector, decoder])
                    done, _ = await asyncio.wait(
                        {collector, decoder}, return_when=asyncio.FIRST_COMPLETED
                    )
                    for task in done:
                        task.result()
                    await collector
                    # The audio deadline must not cancel decision publication.
                    if decision is None:
                        raise RuntimeError("end decision missing")
                    await publish_end(decision)
                text = ""
                replaced = False
                if onset:
                    text = await active_session.finish()
                else:
                    # docs/design.md: noise without a command must not become text.
                    if decoder is not None:
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
                # A provider can refuse a result while the end decision is
                # sent. Without a command, finish() does not show the refusal.
                if getattr(active_session, "transcript_refused", False) is True:
                    raise RuntimeError("STT transcript refused")
                reply = encode_transcript("final", turn_id, text)
                if replaced:
                    self.stt_final_replaced_total += 1
                    # The route removes this internal marker before wire validation.
                    reply["_final_replaced"] = True
                await output.put(reply)
                if self.speaker is not None and onset:
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
                    speaker_reply = encode_control(
                        "speaker_result",
                        turn_id,
                        name="",
                        distance=0.0,
                        destination="local",
                    )
                    if self.speaker is not None:
                        speaker_reply["_speaker_skipped"] = True
                    await output.put(speaker_reply)
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

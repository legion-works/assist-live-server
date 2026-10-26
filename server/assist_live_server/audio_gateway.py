"""Keep one provider and one detector for each audio turn."""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from assist_live_client.protocol import encode_control

from .destinations import Destination, require_destination
from .providers.parakeet import MAX_AUDIO_BYTES
from .providers.stt import STTSession
from .speaker import SpeakerPlugin
from .turn.detector import TurnDetector

TURN_TIMEOUT = 15.0
STALL_INTERVAL = 0.1

type Recorder = Callable[[str, dict[str, object]], Awaitable[None]]


class AudioGateway:
    def __init__(
        self,
        provider_factory: Callable[[str], STTSession],
        *,
        provider: str = "parakeet",
        detector_factory: Callable[[], TurnDetector] = TurnDetector,
        destination: Destination = "local",
        local_only: bool = True,
        speaker: SpeakerPlugin | None = None,
    ) -> None:
        self.provider_factory = provider_factory
        self.provider = provider
        self.detector_factory = detector_factory
        self.destination = destination
        self.local_only = local_only
        self.speaker = speaker

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
                        "destination": self.destination,
                        **data,
                    },
                )

        async def decide(reason: str) -> None:
            nonlocal stopped
            if stopped:
                return
            stopped = True
            await note("end_decision", reason=reason)
            await output.put(encode_control("end_decision", turn_id))

        async def work() -> None:
            nonlocal session, stopped
            try:
                # Select the provider before the iterator can supply its first frame.
                require_destination(self.destination, self.local_only)
                session = self.provider_factory(self.provider)
                detector = await asyncio.to_thread(self.detector_factory)
                active_session = session

                async def detect(pcm: bytes | None) -> None:
                    nonlocal onset, candidate
                    events = await asyncio.to_thread(
                        lambda: (
                            detector.flush(partial)
                            if pcm is None
                            else detector.push(pcm, partial)
                        )
                    )
                    if not onset and detector.speech_started_at_ms is not None:
                        onset = True
                        active_session.speech_started()
                        await note("speech_start")
                        changed.set()
                    silence_ms = detector.silent_samples / 16
                    if silence_ms < detector.profile.min_silence_ms:
                        candidate = False
                    elif not candidate:
                        candidate = True
                        await note("end_candidate")
                    if events:
                        await decide(events[-1]["reason"])

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
                                    await active_session.push(block)
                                    if self.speaker is not None:
                                        speaker_audio.extend(block)
                                    total += len(block)
                                    changed.set()
                                    await detect(block)
                                    if stopped:
                                        break
                                if total >= MAX_AUDIO_BYTES:
                                    await decide("hard")
                    except TimeoutError:
                        await decide("hard")
                    finally:
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
                                encode_control("partial", turn_id, text=text)
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
                text = await active_session.finish()
                await output.put(encode_control("final", turn_id, text=text))
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

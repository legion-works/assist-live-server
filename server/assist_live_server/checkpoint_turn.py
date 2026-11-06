"""Keep checkpoint decisions on the audio clock. See docs/design.md."""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, Protocol, runtime_checkable

from .providers.parakeet import CHECKPOINT_TIMEOUT, INTERVAL_BYTES
from .providers.stt import STTCheckpointDeadlineError, STTSession
from .turn.detector import TurnDetector


class CheckpointDeadline(RuntimeError):
    pass


@runtime_checkable
class CheckpointSession(STTSession, Protocol):
    async def checkpoint(self, pcm: bytes) -> str: ...


class CheckpointTurn:
    def __init__(
        self,
        session: CheckpointSession,
        detector: TurnDetector,
        *,
        note: Callable[..., Awaitable[None]],
        decide: Callable[[str], Awaitable[None]],
        publish: Callable[[str], Awaitable[None]],
        failure: Callable[[str], None],
        observe: Callable[[float], None],
        speaker_audio: bytearray | None,
        limit: int,
    ) -> None:
        self.session = session
        self.detector = detector
        self.note = note
        self.decide = decide
        self.publish = publish
        self.failure = failure
        self.observe = observe
        self.speaker_audio = speaker_audio
        self.limit = limit
        self.audio = bytearray()
        self.arrivals: list[tuple[int, float]] = []
        self.checkpoints: dict[int, asyncio.Task[str]] = {}
        self.tasks: list[asyncio.Task[Any]] = []
        self.queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=16)
        self.publications: asyncio.Queue[tuple[int, asyncio.Task[str]]] = (
            asyncio.Queue()
        )
        self.progress = asyncio.Condition()
        self.processed_samples = 0
        self.ended = False
        self.onset = False
        self.failure_recorded = False

    def fail(self, cause: str) -> None:
        if not self.failure_recorded:
            self.failure_recorded = True
            self.failure(cause)

    async def text_at(self, samples: int) -> str:
        position = samples // (INTERVAL_BYTES // 2) * (INTERVAL_BYTES // 2)
        if not position:
            return ""
        return await asyncio.shield(self.checkpoints[position])

    async def run(
        self, audio: AsyncIterator[bytes], *, timeout: float, grace: float
    ) -> str:
        loop = asyncio.get_running_loop()
        failed: asyncio.Future[None] = loop.create_future()

        def completed(task: asyncio.Task[Any]) -> None:
            if not task.cancelled() and (error := task.exception()) is not None:
                if not failed.done():
                    failed.set_exception(error)

        async def checkpoint(prefix: bytes) -> str:
            try:
                async with asyncio.timeout(CHECKPOINT_TIMEOUT):
                    return await self.session.checkpoint(prefix)
            except TimeoutError, STTCheckpointDeadlineError:
                self.fail("deadline")
                raise CheckpointDeadline from None

        async def collect() -> None:
            async for pcm in audio:
                stamp = loop.time()
                if len(pcm) % 2:
                    raise ValueError("whole PCM samples required")
                pcm = pcm[: self.limit - len(self.audio)]
                for offset in range(0, len(pcm), 1024):
                    if self.ended:
                        return
                    block = pcm[offset : offset + 1024]
                    previous = len(self.audio)
                    self.audio.extend(block)
                    self.arrivals.append((len(self.audio) // 2, stamp))
                    first = (previous // INTERVAL_BYTES + 1) * INTERVAL_BYTES
                    for end in range(first, len(self.audio) + 1, INTERVAL_BYTES):
                        task = asyncio.create_task(checkpoint(bytes(self.audio[:end])))
                        self.checkpoints[end // 2] = task
                        self.tasks.append(task)
                        task.add_done_callback(completed)
                        self.publications.put_nowait((end // 2, task))
                        # The input bound limits prefix copies during checkpoint waits.
                        pending = [t for t in self.checkpoints.values() if not t.done()]
                        if len(pending) >= 2:
                            await asyncio.shield(pending[0])
                    await self.queue.put(block)
                if len(self.audio) == self.limit:
                    break
            await self.queue.put(None)

        async def publish() -> None:
            while True:
                position, task = await self.publications.get()
                text = await asyncio.shield(task)
                async with self.progress:
                    while not (
                        self.ended
                        or (self.onset and position <= self.processed_samples)
                    ):
                        await self.progress.wait()
                    if self.ended:
                        return
                await self.publish(text)

        def text(samples: int) -> str:
            # Collection must continue while this worker waits for checkpoint text.
            return asyncio.run_coroutine_threadsafe(
                self.text_at(samples), loop
            ).result()

        self.detector.checkpoint_text = text
        sent = 0
        candidate = False
        wall_deadline: asyncio.Timeout | None = None

        async def detect() -> str:
            nonlocal sent, candidate
            while True:
                block = await self.queue.get()
                events = await asyncio.to_thread(
                    self.detector.flush if block is None else self.detector.push,
                    *([""] if block is None else [block, ""]),
                )
                self.ended = bool(events) or block is None
                if self.ended:
                    collector.cancel()
                    publisher.cancel()
                    past_end = [
                        task
                        for position, task in self.checkpoints.items()
                        if position > self.detector.samples and not task.done()
                    ]
                    for task in past_end:
                        task.cancel()
                    await asyncio.gather(publisher, *past_end, return_exceptions=True)
                count = self.detector.samples * 2
                if count > sent:
                    accepted = bytes(self.audio[sent:count])
                    await self.session.push(accepted)
                    if self.speaker_audio is not None:
                        self.speaker_audio.extend(accepted)
                    sent = count
                if not self.onset and self.detector.speech_started_at_ms is not None:
                    self.onset = True
                    self.session.speech_started()
                    await self.note(
                        "speech_start", onset_ms=int(self.detector.speech_started_at_ms)
                    )
                async with self.progress:
                    self.processed_samples = self.detector.samples
                    self.progress.notify_all()
                silence_ms = self.detector.silent_samples / 16
                if silence_ms < self.detector.profile.min_silence_ms:
                    candidate = False
                elif not candidate:
                    candidate = True
                    await self.note("end_candidate")
                if events or block is None:
                    if wall_deadline is not None:
                        wall_deadline.reschedule(None)
                    if self.detector.samples:
                        arrival = next(
                            stamp
                            for end, stamp in self.arrivals
                            if end >= self.detector.samples
                        )
                        self.observe(loop.time() - arrival)
                    await self.decide(events[-1]["reason"] if events else "input_end")
                    return (
                        await self.text_at(self.detector.samples) if self.onset else ""
                    )

        collector = asyncio.create_task(collect())
        publisher = asyncio.create_task(publish())
        processor = asyncio.create_task(detect())
        self.tasks.extend([collector, publisher])
        collector.add_done_callback(completed)
        publisher.add_done_callback(completed)
        try:
            try:
                async with asyncio.timeout(timeout) as wall_deadline:
                    watched: set[asyncio.Future[Any]] = {processor, failed}
                    await asyncio.wait(watched, return_when=asyncio.FIRST_COMPLETED)
                    if failed.done():
                        await failed
                    return await processor
            except TimeoutError:
                self.fail("wall_limit")
                raise
        finally:
            for task in self.tasks:
                task.cancel()
            await asyncio.gather(*self.tasks, return_exceptions=True)
            processor.cancel()
            try:
                async with asyncio.timeout(grace):
                    await asyncio.gather(processor, return_exceptions=True)
            except TimeoutError:
                pass
            if failed.done() and not failed.cancelled():
                failed.exception()
            else:
                failed.cancel()
            self.audio.clear()

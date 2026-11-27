import asyncio
import logging
from collections.abc import Callable
from dataclasses import replace
from typing import Literal

from assist_live_client.models import TimingEvent

from .clock import ClockWindow
from .metrics import Metrics
from .protocol import DeviceEvent, TimingIngress, TurnStore
from .store import SQLiteStore, safe_data

LOGGER = logging.getLogger(__name__)


class EventGateway:
    def __init__(self, store: TurnStore) -> None:
        self.store = store
        self.clocks: dict[str, ClockWindow] = {}
        self.sequences: dict[str, tuple[int, int, int]] = {}
        self.metrics = Metrics()
        self.acceptance_failed = False

    async def accept(self, device: DeviceEvent) -> None:
        event, commit = self.convert(device)
        if isinstance(self.store, SQLiteStore):
            await self.store.append_device(event, device.arrival_ns)
        else:
            await self.store.append(event)
        commit()

    def convert(self, device: DeviceEvent) -> tuple[TimingEvent, Callable[[], None]]:
        raw = device.event
        current = self.clocks.get(device.satellite)
        clock = current.copy() if current is not None else ClockWindow()
        stamp, uncertainty = clock.observe(
            raw["boot"], raw["device_ms"], device.arrival_ns
        )
        previous = self.sequences.get(device.satellite, (raw["boot"], -1, 0))
        boot, high, received = previous
        if boot != raw["boot"]:
            high, received = -1, 0
        sequence = (raw["boot"], max(high, raw["seq"]), received + 1)
        quality: Literal["measured", "proxy", "estimate"] = "proxy"
        if raw["data"].get("quality") == "measured":
            quality = "measured"
        elif raw["data"].get("quality") == "estimate":
            quality = "estimate"
        event = TimingEvent(
            None,
            device.satellite,
            "device",
            raw["stage"],
            stamp,
            quality,
            uncertainty,
            raw["device_ms"],
            raw["seq"],
        )
        event = replace(event, data=safe_data(replace(event, data=raw["data"])))

        def commit() -> None:
            self.clocks[device.satellite] = clock
            self.sequences[device.satellite] = sequence
            self.metrics.sequence_gaps = sum(
                high + 1 - received for _, high, received in self.sequences.values()
            )

        return event, commit

    async def run(self, ingress: TimingIngress) -> None:
        backoff = 0.1
        while True:
            event = await ingress.queue.get()
            accepted = False
            failed = False
            try:
                if isinstance(self.store, SQLiteStore):
                    accepted = await self.store.accept_device(
                        ingress.registry, event, self.convert
                    )
                else:
                    accepted = await asyncio.to_thread(
                        ingress.registry.accept_sequence,
                        event.event["device"],
                        event.event["boot"],
                        event.event["seq"],
                        credential_epoch=event.credential_epoch,
                    )
                    if accepted:
                        await self.accept(event)
                if accepted:
                    self.acceptance_failed = False
                    ingress.accepted += 1
                else:
                    ingress.refused += 1
                backoff = 0.1
            except asyncio.CancelledError:
                accepted = False
                ingress.dropped += 1
                raise
            except Exception:
                accepted = False
                self.acceptance_failed = True
                # Exception text can contain credentials or event data.
                LOGGER.error("timing acceptance failed")
                ingress.worker_errors += 1
                ingress.refused += 1
                failed = True
            finally:
                if event.result is not None and not event.result.done():
                    event.result.set_result(accepted)
                ingress.queue.task_done()
            if failed:
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 5.0)

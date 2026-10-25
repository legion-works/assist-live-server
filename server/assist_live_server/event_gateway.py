import asyncio
from dataclasses import replace
from typing import Literal

from assist_live_client.models import TimingEvent

from .clock import ClockWindow
from .metrics import Metrics
from .protocol import DeviceEvent, TimingIngress, TurnStore
from .store import SQLiteStore, safe_data


class EventGateway:
    def __init__(self, store: TurnStore) -> None:
        self.store = store
        self.clocks: dict[str, ClockWindow] = {}
        self.sequences: dict[str, tuple[int, int, int]] = {}
        self.metrics = Metrics()

    async def accept(self, device: DeviceEvent) -> None:
        event = self.convert(device)
        if isinstance(self.store, SQLiteStore):
            await self.store.append_device(event, device.arrival_ns)
        else:
            await self.store.append(event)

    def convert(self, device: DeviceEvent) -> TimingEvent:
        raw = device.event
        clock = self.clocks.setdefault(device.satellite, ClockWindow())
        stamp, uncertainty = clock.observe(
            raw["boot"], raw["device_ms"], device.arrival_ns
        )
        previous = self.sequences.get(device.satellite, (raw["boot"], -1, 0))
        boot, high, received = previous
        if boot != raw["boot"]:
            high, received = -1, 0
        self.sequences[device.satellite] = (
            raw["boot"],
            max(high, raw["seq"]),
            received + 1,
        )
        self.metrics.sequence_gaps = sum(
            high + 1 - received for _, high, received in self.sequences.values()
        )
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
        return event

    async def run(self, ingress: TimingIngress) -> None:
        while True:
            event = await ingress.queue.get()
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
                    ingress.accepted += 1
                else:
                    ingress.refused += 1
                if event.result is not None and not event.result.done():
                    event.result.set_result(accepted)
            finally:
                ingress.queue.task_done()

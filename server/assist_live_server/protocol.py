import asyncio
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Protocol

from aiohttp import WSMsgType, web
from assist_live_client.models import TimingEvent, TurnOpen
from assist_live_client.protocol import parse_json

from .auth import Registry, verify_event

MAX_DEVICE = 4096


class TurnStore(Protocol):
    async def open_turn(self, turn: TurnOpen) -> None: ...
    async def append(self, event: TimingEvent) -> None: ...
    async def get_turn(self, turn_id: str) -> dict[str, Any] | None: ...
    def export(
        self, satellite: str, since_utc_ns: int
    ) -> AsyncIterator[dict[str, Any]]: ...
    def events(
        self, satellite: str, after_event_id: int
    ) -> AsyncIterator[dict[str, Any]]: ...


class AudioBackend(Protocol):
    def stt(
        self, turn_id: str, audio: AsyncIterator[bytes]
    ) -> AsyncIterator[dict[str, Any]]: ...
    def tts(
        self, turn_id: str, text: AsyncIterator[str], voice: str
    ) -> AsyncIterator[bytes | dict[str, Any]]: ...


@dataclass(frozen=True)
class DeviceEvent:
    satellite: str
    event: dict[str, Any]
    new_boot: bool
    arrival_ns: int


class TimingIngress:
    def __init__(self, registry: Registry, queue_size: int = 128) -> None:
        if not 1 <= queue_size <= 4096:
            raise ValueError("invalid queue size")
        self.registry = registry
        self.queue: asyncio.Queue[DeviceEvent] = asyncio.Queue(maxsize=queue_size)

    def accept(self, packet: bytes) -> bool:
        arrival_ns = time.time_ns()
        if len(packet) > MAX_DEVICE or self.queue.full():
            return False
        try:
            event = parse_json(packet, MAX_DEVICE)
            tag = event.pop("tag")
            credentials = self.registry.device(event["device"])
            if credentials is None or not verify_event(event, tag, credentials[1]):
                return False
            satellite = credentials[0]
            if event["data"].get("satellite", satellite) != satellite:
                return False
            new_boot = self.registry.replay[event["device"]].boot != event["boot"]
            if not self.registry.accept_sequence(
                event["device"], event["boot"], event["seq"]
            ):
                return False
            self.queue.put_nowait(DeviceEvent(satellite, event, new_boot, arrival_ns))
            return True
        except ValueError, TypeError, KeyError, RecursionError:
            return False

    def missing(self, device: str) -> int:
        return self.registry.missing(device)


class TimingTransport(Protocol):
    def accept(self, packet: bytes) -> bool: ...


class UDPTransport(asyncio.DatagramProtocol):
    def __init__(self, ingress: TimingIngress) -> None:
        self.ingress = ingress

    def accept(self, packet: bytes) -> bool:
        return self.ingress.accept(packet)

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self.accept(data)


class WebSocketTransport:
    def __init__(self, ingress: TimingIngress) -> None:
        self.ingress = ingress

    def accept(self, packet: bytes) -> bool:
        return self.ingress.accept(packet)

    async def handle(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(max_msg_size=MAX_DEVICE, compress=False)
        await ws.prepare(request)
        async for message in ws:
            if message.type not in {WSMsgType.TEXT, WSMsgType.BINARY}:
                break
            packet = (
                message.data.encode() if isinstance(message.data, str) else message.data
            )
            if not self.accept(packet):
                await ws.close(code=1008, message=b"event refused")
                break
        return ws

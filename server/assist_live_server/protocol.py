import asyncio
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Protocol

from aiohttp import WSMsgType, web
from assist_live_client.models import TimingEvent, TurnOpen, TurnRecord
from assist_live_client.protocol import parse_json

from .auth import Registry, verify_event

MAX_DEVICE = 4096
DUMMY_DEVICE_SECRET = bytes(32)
DEVICE_AUTH_TIMEOUT = 2.0
DEVICE_CLOSE_TIMEOUT = 0.1
MAX_DEVICE_CONNECTIONS = 64


class TurnStore(Protocol):
    async def open_turn(self, turn: TurnOpen) -> None: ...
    async def append(self, event: TimingEvent) -> None: ...
    async def get_turn(self, turn_id: str) -> TurnRecord | None: ...
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
    result: asyncio.Future[bool] | None = None
    credential_epoch: int = 0


class TimingIngress:
    def __init__(self, registry: Registry, queue_size: int = 128) -> None:
        if not 1 <= queue_size <= 4096:
            raise ValueError("invalid queue size")
        self.registry = registry
        self.queue: asyncio.Queue[DeviceEvent] = asyncio.Queue(maxsize=queue_size)
        self.accepted = 0
        self.refused = 0
        self.dropped = 0

    def accept(
        self, packet: bytes, *, result: asyncio.Future[bool] | None = None
    ) -> bool:
        arrival_ns = time.time_ns()
        if len(packet) > MAX_DEVICE or self.queue.full():
            self.dropped += 1
            return False
        try:
            event = parse_json(packet, MAX_DEVICE)
            tag = event.pop("tag")
            credentials = self.registry.device_snapshot(event["device"])
            secret = credentials[1] if credentials is not None else DUMMY_DEVICE_SECRET
            verified = verify_event(event, tag, secret)
            if not verified or credentials is None:
                return False
            satellite = credentials[0]
            if event["data"].get("satellite", satellite) != satellite:
                return False
            self.queue.put_nowait(
                DeviceEvent(satellite, event, False, arrival_ns, result, credentials[2])
            )
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
        self.active = 0

    def accept(self, packet: bytes) -> bool:
        return self.ingress.accept(packet)

    async def handle(self, request: web.Request) -> web.WebSocketResponse:
        if self.active >= MAX_DEVICE_CONNECTIONS:
            refused = web.HTTPServiceUnavailable(text="device connection limit")
            refused.force_close()
            raise refused
        self.active += 1
        ws = web.WebSocketResponse(
            max_msg_size=MAX_DEVICE, compress=False, timeout=DEVICE_CLOSE_TIMEOUT
        )
        try:
            await ws.prepare(request)
            authenticated = False
            while True:
                async with asyncio.timeout(
                    None if authenticated else DEVICE_AUTH_TIMEOUT
                ):
                    message = await ws.receive()
                if message.type not in {WSMsgType.TEXT, WSMsgType.BINARY}:
                    break
                packet = (
                    message.data.encode()
                    if isinstance(message.data, str)
                    else message.data
                )
                result = asyncio.get_running_loop().create_future()
                if not self.ingress.accept(packet, result=result):
                    await ws.close(code=1008, message=b"event refused")
                    break
                authenticated = True
                if not await result:
                    await ws.close(code=1008, message=b"event refused")
                    break
        except TimeoutError:
            await ws.close(code=1008, message=b"authentication timeout")
        finally:
            try:
                if ws.prepared:
                    await ws.close()
            finally:
                self.active -= 1
        return ws

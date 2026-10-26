import asyncio
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from aiohttp import WSMsgType, web
from assist_live_client.models import TimingEvent, TTSFormat, TurnOpen, TurnRecord
from assist_live_client.protocol import parse_json

from .auth import Registry, verify_event

MAX_DEVICE = 4096
DUMMY_DEVICE_SECRET = bytes(32)
DEVICE_AUTH_TIMEOUT = 2.0
DEVICE_RESULT_TIMEOUT = 2.0
WS_CLOSE_TIMEOUT = 0.1
MAX_DEVICE_CONNECTIONS = 64
MAX_DEVICE_PER_DEVICE = 4


class TurnExistsError(ValueError):
    """The turn identifier already exists in the store."""


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
    """Declare the PCM format and end each audio sentence with sentence_end.

    TTS supplies raw PCM and sentence_end controls with indices from 0.
    The gateway sends audio_format first. It refuses a missing sentence_end.
    """

    audio_format: TTSFormat

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
        self.worker_errors = 0
        self.closed = False

    def accept(
        self,
        packet: bytes,
        *,
        result: asyncio.Future[bool] | None = None,
        admit: Callable[[str], bool] | None = None,
    ) -> bool:
        arrival_ns = time.time_ns()
        if self.closed or len(packet) > MAX_DEVICE or self.queue.full():
            self.dropped += 1
            return False
        try:
            event = parse_json(packet, MAX_DEVICE)
            tag = event.pop("tag")
            credentials = self.registry.device_snapshot(event["device"])
            secret = credentials[1] if credentials is not None else DUMMY_DEVICE_SECRET
            verified = verify_event(event, tag, secret)
            if not verified or credentials is None:
                self.refused += 1
                return False
            satellite = credentials[0]
            if event["data"].get("satellite", satellite) != satellite:
                self.refused += 1
                return False
            if admit is not None and not admit(event["device"]):
                self.refused += 1
                return False
            self.queue.put_nowait(
                DeviceEvent(satellite, event, False, arrival_ns, result, credentials[2])
            )
            return True
        except ValueError, TypeError, KeyError, RecursionError:
            self.refused += 1
            return False

    def close(self) -> None:
        self.closed = True
        while not self.queue.empty():
            event = self.queue.get_nowait()
            self.dropped += 1
            if event.result is not None and not event.result.done():
                event.result.set_result(False)
            self.queue.task_done()

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
        self.devices: dict[str, int] = {}

    def accept(self, packet: bytes) -> bool:
        return self.ingress.accept(packet)

    async def handle(self, request: web.Request) -> web.WebSocketResponse:
        if self.active >= MAX_DEVICE_CONNECTIONS:
            refused = web.HTTPServiceUnavailable(text="device connection limit")
            refused.force_close()
            raise refused
        self.active += 1
        identity: str | None = None

        def admit(device: str) -> bool:
            nonlocal identity
            if identity is not None:
                return identity == device
            if self.devices.get(device, 0) >= MAX_DEVICE_PER_DEVICE:
                return False
            self.devices[device] = self.devices.get(device, 0) + 1
            identity = device
            return True

        ws = web.WebSocketResponse(
            max_msg_size=MAX_DEVICE, compress=False, timeout=WS_CLOSE_TIMEOUT
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
                if not self.ingress.accept(packet, result=result, admit=admit):
                    await ws.close(code=1008, message=b"event refused")
                    break
                authenticated = True
                async with asyncio.timeout(DEVICE_RESULT_TIMEOUT):
                    accepted = await result
                if not accepted:
                    await ws.close(code=1008, message=b"event refused")
                    break
        except TimeoutError:
            await ws.close(code=1008, message=b"event timeout")
        finally:
            try:
                if ws.prepared:
                    await ws.close()
            finally:
                self.active -= 1
                if identity is not None:
                    self.devices[identity] -= 1
                    if not self.devices[identity]:
                        del self.devices[identity]
        return ws

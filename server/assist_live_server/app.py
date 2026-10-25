import asyncio
import contextlib
import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import replace
from typing import Any

from aiohttp import WSMsgType, web
from assist_live_client.models import TimingEvent, TurnOpen, identifier, unsigned
from assist_live_client.protocol import (
    HEARTBEAT_INTERVAL,
    MAX_AUDIO,
    MAX_CONTROL,
    MESSAGE_DEADLINE,
    decode_audio,
    decode_control,
    encode_audio,
    encode_control,
    parse_json,
)

from .auth import Registry
from .event_gateway import EventGateway
from .protocol import (
    AudioBackend,
    TimingIngress,
    TurnStore,
    UDPTransport,
    WebSocketTransport,
)
from .store import SQLiteStore, safe_data

UDP_ADDRESS = web.AppKey("udp_address", tuple[str, int])
TEXT_IDLE_TIMEOUT = 30.0


def create_app(
    registry: Registry,
    store: TurnStore | None = None,
    backend: AudioBackend | None = None,
    *,
    record_transcripts: bool = False,
    ingress: TimingIngress | None = None,
    udp_address: tuple[str, int] | None = None,
) -> web.Application:
    @web.middleware
    async def errors(
        request: web.Request,
        handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
    ) -> web.StreamResponse:
        try:
            return await handler(request)
        except ValueError, TypeError, KeyError, TimeoutError:
            raise web.HTTPBadRequest(text="invalid request") from None

    app = web.Application(client_max_size=MAX_CONTROL, middlewares=[errors])
    timing = ingress or TimingIngress(registry)
    if timing.registry is not registry:
        raise ValueError("ingress registry mismatch")
    gateway = EventGateway(store) if store is not None else None
    device_transport = WebSocketTransport(timing)
    if udp_address is not None and (
        store is None
        or (not isinstance(store, SQLiteStore) and registry.replay_store is None)
    ):
        raise ValueError("timing and replay stores required")

    async def lifecycle(application: web.Application) -> AsyncIterator[None]:
        transport = None
        consumer = None
        maintenance = None
        if isinstance(store, SQLiteStore):
            await store.configure_registry(registry)

        async def maintain() -> None:
            while True:
                if isinstance(store, SQLiteStore):
                    await store.maintain()
                await asyncio.sleep(0.1)

        try:
            if gateway is not None:
                consumer = asyncio.create_task(
                    gateway.run(timing), name="assist-live-timing"
                )
                maintenance = asyncio.create_task(
                    maintain(), name="assist-live-retention"
                )
            if udp_address is not None:
                (
                    transport,
                    _,
                ) = await asyncio.get_running_loop().create_datagram_endpoint(
                    lambda: UDPTransport(timing), local_addr=udp_address
                )
                address = transport.get_extra_info("sockname")
                application[UDP_ADDRESS] = (address[0], address[1])
            yield
        finally:
            if transport is not None:
                transport.close()
            tasks = [task for task in (consumer, maintenance) if task is not None]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    app.cleanup_ctx.append(lifecycle)

    def allowed(request: web.Request, satellite: str | None = None) -> frozenset[str]:
        header = request.headers.get("Authorization", "")
        satellites = (
            registry.authorize(header[7:]) if header.startswith("Bearer ") else None
        )
        if satellites is None:
            raise web.HTTPUnauthorized(text="unauthorized")
        selected = satellite or request.query.get("satellite")
        if selected is not None and selected not in satellites:
            raise web.HTTPForbidden(text="satellite refused")
        return satellites

    def storage() -> TurnStore:
        if store is None:
            raise web.HTTPServiceUnavailable(text="store unavailable")
        return store

    async def body(request: web.Request) -> dict[str, Any]:
        if request.content_length is not None and request.content_length > MAX_CONTROL:
            raise web.HTTPRequestEntityTooLarge(
                max_size=MAX_CONTROL, actual_size=request.content_length
            )
        raw = bytearray()
        async with asyncio.timeout(MESSAGE_DEADLINE):
            async for chunk in request.content.iter_chunked(1024):
                if len(raw) + len(chunk) > MAX_CONTROL:
                    raise ValueError("message too large")
                raw.extend(chunk)
        return parse_json(bytes(raw), MAX_CONTROL)

    async def health(request: web.Request) -> web.Response:
        allowed(request)
        return web.json_response(
            {
                "version": 1,
                "healthy": store is not None
                and backend is not None
                and registry.replay_store is not None,
            }
        )

    async def bindings(request: web.Request) -> web.Response:
        satellites = allowed(request)
        return web.json_response([{"satellite": s} for s in sorted(satellites)])

    async def metrics(request: web.Request) -> web.Response:
        satellites = allowed(request)
        if not isinstance(store, SQLiteStore) or gateway is None:
            raise web.HTTPServiceUnavailable(text="metrics unavailable")
        await store.maintain()
        gateway.metrics.sequence_gaps = sum(
            registry.missing(device)
            for device in registry.devices
            if registry.devices[device]["satellite"] in satellites
        )
        return web.Response(
            text=gateway.metrics.render(
                {
                    **await store.diagnostics(satellites),
                    "device_accepted_total": timing.accepted,
                    "device_refused_total": timing.refused,
                    "device_dropped_total": timing.dropped,
                    "device_connections": device_transport.active,
                }
            ),
            headers={"Content-Type": "text/plain; version=0.0.4; charset=utf-8"},
        )

    async def device(request: web.Request) -> web.WebSocketResponse:
        storage()
        if registry.replay_store is None:
            raise web.HTTPServiceUnavailable(text="replay store unavailable")
        return await device_transport.handle(request)

    async def open_turn(request: web.Request) -> web.Response:
        allowed(request)
        turn = TurnOpen(**await body(request))
        allowed(request, turn.satellite)
        existing = await storage().get_turn(turn.turn_id)
        if existing is not None:
            raise web.HTTPConflict(text="turn already exists")
        await storage().open_turn(turn)
        return web.json_response({"version": 1})

    async def turn(request: web.Request) -> web.Response:
        satellites = allowed(request)
        value = await storage().get_turn(request.match_info["turn_id"])
        if value is None or value["satellite"] not in satellites:
            raise web.HTTPNotFound(text="turn not found")
        return web.json_response(value)

    async def append(request: web.Request) -> web.Response:
        allowed(request)
        event = TimingEvent(**await body(request))
        allowed(request, event.satellite)
        if event.source not in {"integration", "ha_state"}:
            raise web.HTTPForbidden(text="event source refused")
        turn_value = await storage().get_turn(event.turn_id) if event.turn_id else None
        if event.turn_id is not None and (
            turn_value is None or turn_value["satellite"] != event.satellite
        ):
            raise web.HTTPForbidden(text="turn refused")
        transcript = bool(
            record_transcripts
            and turn_value
            and turn_value.get("record_transcripts", False)
        )
        data = safe_data(event, transcript)
        await storage().append(replace(event, data=data))
        return web.json_response({"version": 1})

    async def export(request: web.Request) -> web.StreamResponse:
        satellite = identifier(request.query["satellite"])
        allowed(request, satellite)
        since = unsigned(int(request.query.get("since_utc_ns", "0")))
        records = storage().export(satellite, since)
        response = web.StreamResponse(headers={"Content-Type": "application/x-ndjson"})
        await response.prepare(request)
        async for row in records:
            if row["event"]["satellite"] != satellite:
                raise ValueError("store isolation violation")
            raw = json.dumps(row, allow_nan=False, separators=(",", ":")).encode()
            if len(raw) > MAX_CONTROL:
                raise ValueError("stored record too large")
            await response.write(raw + b"\n")
        await response.write_eof()
        return response

    async def events(request: web.Request) -> web.WebSocketResponse:
        satellites = allowed(request)
        target = storage()
        ws = web.WebSocketResponse(max_msg_size=MAX_CONTROL, compress=False)
        await ws.prepare(request)
        try:
            first = await ws.receive(timeout=MESSAGE_DEADLINE)
            if first.type != WSMsgType.TEXT:
                raise ValueError("subscription required")
            value = parse_json(first.data, MAX_CONTROL)
            if value.keys() != {"type", "version", "satellite", "after_event_id"}:
                raise ValueError("invalid subscription")
            if type(value["version"]) is not int or value["version"] != 1:
                raise ValueError("unsupported version")
            if value["type"] != "subscribe" or value["satellite"] not in satellites:
                raise ValueError("subscription refused")
            cursor = unsigned(value["after_event_id"])
            closed = asyncio.create_task(
                ws.receive(), name="assist-live-subscription-close"
            )
            iterator = target.events(value["satellite"], cursor)

            async def next_record() -> dict[str, Any]:
                return await anext(iterator)

            try:
                while not ws.closed:
                    next_event = asyncio.create_task(
                        next_record(), name="assist-live-subscription-next"
                    )
                    try:
                        done, _ = await asyncio.wait(
                            {closed, next_event}, return_when=asyncio.FIRST_COMPLETED
                        )
                        if closed in done:
                            break
                        row = next_event.result()
                        if (
                            row["event"]["satellite"] != value["satellite"]
                            or row["event_id"] <= cursor
                        ):
                            raise ValueError("invalid store envelope")
                        raw = json.dumps(row, allow_nan=False)
                        if len(raw.encode()) > MAX_CONTROL:
                            raise ValueError("stored event too large")
                        cursor = row["event_id"]
                        await ws.send_str(raw)
                    finally:
                        next_event.cancel()
                        with contextlib.suppress(
                            asyncio.CancelledError, StopAsyncIteration
                        ):
                            await next_event
            finally:
                closed.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await closed
                if hasattr(iterator, "aclose"):
                    await iterator.aclose()
        except ValueError, KeyError, TypeError, TimeoutError, StopAsyncIteration:
            await ws.close(code=1008, message=b"subscription refused")
        return ws

    async def audio(request: web.Request) -> web.WebSocketResponse:
        satellites = allowed(request)
        target = storage()
        if backend is None:
            raise web.HTTPServiceUnavailable(text="audio unavailable")
        ws = web.WebSocketResponse(max_msg_size=MAX_AUDIO, compress=False)
        await ws.prepare(request)
        tasks: list[asyncio.Task[None]] = []
        try:
            first = await ws.receive(timeout=MESSAGE_DEADLINE)
            if first.type != WSMsgType.TEXT:
                raise ValueError("negotiation required")
            negotiation = decode_control(first.data)
            if negotiation["type"] != "negotiate":
                raise ValueError("negotiation required")
            turn_id = negotiation["turn_id"]
            turn_value = await target.get_turn(turn_id)
            if turn_value is None or turn_value["satellite"] not in satellites:
                raise ValueError("turn refused")
            await ws.send_json(
                encode_control(
                    "negotiated", turn_id, capabilities=negotiation["capabilities"]
                )
            )
            queue: asyncio.Queue[bytes | str | None] = asyncio.Queue(maxsize=16)
            lock = asyncio.Lock()

            async def send(value: dict[str, Any]) -> None:
                async with lock:
                    await ws.send_json(decode_control(json.dumps(value), turn_id))

            async def heartbeat() -> None:
                while True:
                    await asyncio.sleep(HEARTBEAT_INTERVAL)
                    await send(encode_control("heartbeat", turn_id))

            async def receive() -> None:
                expected_seq = 0
                ended = False
                while True:
                    message = await ws.receive(
                        timeout=None
                        if ended
                        else (
                            TEXT_IDLE_TIMEOUT
                            if negotiation["mode"] == "tts"
                            else MESSAGE_DEADLINE
                        )
                    )
                    if ended:
                        raise ValueError("input already ended")
                    if (
                        message.type == WSMsgType.BINARY
                        and negotiation["mode"] == "stt"
                    ):
                        seq, pcm = decode_audio(message.data, turn_id)
                        if seq != expected_seq:
                            raise ValueError("audio sequence mismatch")
                        expected_seq += 1
                        await queue.put(pcm)
                    elif message.type == WSMsgType.TEXT:
                        control = decode_control(message.data, turn_id)
                        if control["type"] == "end":
                            if ended:
                                raise ValueError("duplicate end")
                            ended = True
                            await queue.put(None)
                            continue
                        if control["type"] != "text" or negotiation["mode"] != "tts":
                            raise ValueError("unexpected control")
                        await queue.put(control["text"])
                    else:
                        raise ValueError("audio stream closed")

            async def pcm_input() -> AsyncIterator[bytes]:
                while (value := await queue.get()) is not None:
                    if not isinstance(value, bytes):
                        raise ValueError("PCM required")
                    yield value

            async def text_input() -> AsyncIterator[str]:
                while (value := await queue.get()) is not None:
                    if not isinstance(value, str):
                        raise ValueError("text required")
                    yield value

            async def produce() -> None:
                if negotiation["mode"] == "stt":
                    async for reply in backend.stt(turn_id, pcm_input()):
                        reply = decode_control(json.dumps(reply), turn_id)
                        if reply.get("type") == "final":
                            data: dict[str, object] = {}
                            if record_transcripts and turn_value["record_transcripts"]:
                                data["transcript"] = reply["text"]
                            await target.append(
                                TimingEvent(
                                    turn_id,
                                    turn_value["satellite"],
                                    "server",
                                    "stt_final",
                                    time.time_ns(),
                                    "measured",
                                    None,
                                    data=data,
                                )
                            )
                        await send(reply)
                else:
                    await send(
                        encode_control(
                            "format",
                            turn_id,
                            codec="pcm_s16le",
                            rate=16000,
                            channels=1,
                            width=2,
                        )
                    )
                    seq = 0
                    async for tts_reply in backend.tts(
                        turn_id, text_input(), negotiation.get("voice", "")
                    ):
                        if isinstance(tts_reply, bytes):
                            async with lock:
                                await ws.send_bytes(
                                    encode_audio(turn_id, seq, tts_reply)
                                )
                            seq += 1
                        else:
                            await send(tts_reply)
                await send(encode_control("end", turn_id))

            reader = asyncio.create_task(receive(), name="assist-live-audio-receive")
            producer = asyncio.create_task(produce(), name="assist-live-audio-produce")
            pulse = asyncio.create_task(heartbeat(), name="assist-live-heartbeat")
            tasks.extend([reader, producer, pulse])
            done, _ = await asyncio.wait(
                {reader, producer, pulse}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                task.result()
            if producer not in done:
                await producer
        except ValueError, KeyError, TypeError, TimeoutError, ConnectionError:
            await ws.close(code=1008, message=b"audio refused")
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await ws.close()
        return ws

    app.router.add_get("/health", health)
    app.router.add_get("/metrics", metrics)
    app.router.add_get("/v1/bindings", bindings)
    app.router.add_post("/v1/turns", open_turn)
    app.router.add_get("/v1/turns/{turn_id}", turn)
    app.router.add_post("/v1/event", append)
    app.router.add_get("/v1/export", export)
    app.router.add_get("/v1/events", events)
    app.router.add_get("/v1/audio", audio)
    app.router.add_get("/v1/device", device)
    return app

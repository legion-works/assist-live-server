import asyncio
import contextlib
import json
import socket
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from dataclasses import asdict
from ipaddress import ip_address
from typing import Any

import aiohttp

from .models import (
    HealthCapabilities,
    TimingEvent,
    TTSAudio,
    TTSFormat,
    TTSItem,
    TTSSentenceEnd,
    TTSVoice,
    TurnOpen,
    TurnRecord,
    identifier,
    unsigned,
)
from .protocol import (
    MAX_AUDIO,
    MAX_CONTROL,
    MESSAGE_DEADLINE,
    ProtocolError,
    decode_audio,
    decode_control,
    encode_audio,
    encode_control,
    encode_text,
    parse_json,
)


class AssistLiveClient:
    def __init__(
        self, url: str, token: str, *, session: aiohttp.ClientSession | None = None
    ) -> None:
        parsed = aiohttp.client_reqrep.URL(url)
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.user
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("invalid server URL")
        self.url = url.rstrip("/")
        self._token = token
        self._session = session
        self._owns_session = session is None

    async def __aenter__(self) -> AssistLiveClient:
        self._connection()
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()

    def _connection(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=20, sock_read=MESSAGE_DEADLINE),
                read_bufsize=MAX_CONTROL,
                auto_decompress=False,
            )
        return self._session

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": "Bearer " + self._token}

    async def close(self) -> None:
        if self._owns_session and self._session is not None:
            await self._session.close()

    @contextlib.asynccontextmanager
    async def _connect[Response](
        self,
        connect: Callable[..., contextlib.AbstractAsyncContextManager[Response]],
        path: str,
        *,
        timeout: aiohttp.ClientTimeout | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[Response]:
        session = self._connection()
        url = aiohttp.client_reqrep.URL(self.url + path)
        headers = self._headers
        total = (timeout or session.timeout).total
        if timeout is not None:
            kwargs["timeout"] = timeout
        async with contextlib.AsyncExitStack() as stack:
            async with asyncio.timeout(
                total if total and total > 0 else None
            ) as deadline:
                try:
                    response = await stack.enter_async_context(
                        connect(url=url, headers=headers, **kwargs)
                    )
                except aiohttp.ClientConnectorDNSError as error:
                    host = url.raw_host
                    connector = session.connector
                    if host is None or not isinstance(connector, aiohttp.TCPConnector):
                        raise
                    try:
                        ip_address(host)
                    except ValueError:
                        pass
                    else:
                        raise error
                    # The supplied resolver can keep an old NXDOMAIN answer.
                    try:
                        addresses = await asyncio.get_running_loop().getaddrinfo(
                            host,
                            url.port or 0,
                            family=connector.family,
                            type=socket.SOCK_STREAM,
                        )
                    except OSError:
                        raise error from None
                    if not addresses:
                        raise error
                    headers["Host"] = url.host_port_subcomponent or host
                    when = deadline.when()
                    if timeout is not None and when is not None:
                        remaining = when - asyncio.get_running_loop().time()
                        if remaining <= 0:
                            raise TimeoutError from None
                        kwargs["timeout"] = aiohttp.ClientTimeout(
                            total=remaining,
                            connect=timeout.connect,
                            sock_read=timeout.sock_read,
                            sock_connect=timeout.sock_connect,
                            ceil_threshold=float("inf"),
                        )
                    response = await stack.enter_async_context(
                        connect(
                            url=url.with_host(str(addresses[0][4][0])),
                            headers=headers,
                            server_hostname=host,
                            **kwargs,
                        )
                    )
            # HTTP keeps its body deadline. WebSockets keep their stream deadlines.
            yield response

    async def _request(
        self, method: str, path: str, *, data: object = None, timeout: float = 20
    ) -> Any:
        raw = json.dumps(data, allow_nan=False).encode() if data is not None else None
        if raw is not None and len(raw) > MAX_CONTROL:
            raise ProtocolError("request too large", kind="malformed")
        try:
            async with self._connect(
                self._connection().request,
                path,
                method=method,
                data=raw,
                timeout=aiohttp.ClientTimeout(total=timeout),
                allow_redirects=False,
            ) as response:
                if response.status != 200:
                    raise ProtocolError("request refused", kind="refused")
                value = bytearray()
                async for chunk in response.content.iter_chunked(1024):
                    if len(value) + len(chunk) > MAX_CONTROL:
                        raise ProtocolError("response too large", kind="malformed")
                    value.extend(chunk)
                if path.startswith("/v1/bindings"):
                    bindings = json.loads(value)
                    if not isinstance(bindings, list) or any(
                        not isinstance(row, dict) or row.keys() != {"satellite"}
                        for row in bindings
                    ):
                        raise ProtocolError("invalid bindings", kind="malformed")
                    for row in bindings:
                        identifier(row["satellite"])
                    return bindings
                return parse_json(bytes(value), MAX_CONTROL)
        except TimeoutError:
            raise ProtocolError("request failed", kind="timeout") from None
        except aiohttp.ClientError:
            raise ProtocolError("request failed", kind="closed") from None
        except ValueError:
            raise ProtocolError("request failed", kind="malformed") from None

    async def health(self, timeout: float = 0.2) -> bool:
        try:
            value = await self._request("GET", "/health", timeout=timeout)
            return value.get("version") == 1 and value.get("healthy") is True
        except ProtocolError:
            return False

    async def health_capabilities(self, timeout: float = 0.2) -> HealthCapabilities:
        value = await self._request("GET", "/health", timeout=timeout)
        if value.get("version") != 1 or type(value.get("healthy")) is not bool:
            raise ProtocolError("invalid health response", kind="malformed")
        capabilities = value.get("capabilities", {})
        if not isinstance(capabilities, dict):
            raise ProtocolError("invalid health capabilities", kind="malformed")
        fields = {}
        for key in ("stt", "tts", "text_positions"):
            item = capabilities.get(key, False)
            if type(item) is not bool:
                raise ProtocolError("invalid health capability", kind="malformed")
            fields[key] = item
        local_only = value.get("local_only")
        if local_only is not None and type(local_only) is not bool:
            raise ProtocolError("invalid health local only", kind="malformed")
        destinations = value.get("destinations")
        if isinstance(destinations, dict):
            destinations = {
                key: item
                for key, item in destinations.items()
                if key in {"stt", "tts", "speaker"}
            }
        if destinations is not None and (
            not isinstance(destinations, dict)
            or any(
                not isinstance(item, str) or item not in {"local", "cloud", "unknown"}
                for item in destinations.values()
            )
        ):
            raise ProtocolError("invalid health destinations", kind="malformed")
        voices = []
        default_voice = capabilities.get("tts_default_voice")
        try:
            entries = capabilities.get("tts_voices", [])
            if not isinstance(entries, list):
                raise ValueError("invalid voice list")
            for entry in entries:
                if not isinstance(entry, dict):
                    raise ValueError("invalid voice entry")
                voices.append(TTSVoice(entry["id"], entry["name"], entry["language"]))
            ids = [voice.id for voice in voices]
            if len(set(ids)) != len(ids) or (
                default_voice is not None
                and (not isinstance(default_voice, str) or default_voice not in ids)
            ):
                raise ValueError("invalid default voice")
        except ValueError, TypeError, KeyError:
            raise ProtocolError("invalid health TTS voices", kind="malformed") from None
        return HealthCapabilities(
            value["healthy"],
            **fields,
            local_only=local_only,
            destinations=destinations,
            tts_voices=tuple(voices),
            tts_default_voice=default_voice,
        )

    async def open_turn(self, turn: TurnOpen) -> None:
        await self._request("POST", "/v1/turns", data=asdict(turn))

    async def event(self, event: TimingEvent) -> None:
        await self._request("POST", "/v1/event", data=asdict(event))

    async def turn(self, turn_id: str) -> TurnRecord:
        from .models import turn_uuid

        turn_uuid(turn_id)
        return await self._request("GET", "/v1/turns/" + turn_id)

    async def bindings(self) -> list[dict[str, str]]:
        return await self._request("GET", "/v1/bindings")

    def _envelope(
        self, value: dict[str, Any], satellite: str, cursor: int
    ) -> dict[str, Any]:
        if value.keys() != {"version", "event_id", "event"} or value["version"] != 1:
            raise ProtocolError("invalid event envelope", kind="malformed")
        unsigned(value["event_id"])
        event = TimingEvent(**value["event"])
        if event.satellite != satellite or value["event_id"] <= cursor:
            raise ProtocolError("event isolation or cursor mismatch", kind="malformed")
        return value

    async def events(
        self, satellite: str, after_event_id: int = 0
    ) -> AsyncIterator[dict[str, Any]]:
        identifier(satellite)
        unsigned(after_event_id)
        try:
            async with self._connect(
                self._connection().ws_connect,
                "/v1/events",
                max_msg_size=MAX_CONTROL,
                compress=0,
            ) as ws:
                await ws.send_json(
                    {
                        "type": "subscribe",
                        "version": 1,
                        "satellite": satellite,
                        "after_event_id": after_event_id,
                    }
                )
                async for message in ws:
                    if message.type != aiohttp.WSMsgType.TEXT:
                        raise ProtocolError("event stream stopped", kind="closed")
                    value = self._envelope(
                        parse_json(message.data, MAX_CONTROL), satellite, after_event_id
                    )
                    after_event_id = value["event_id"]
                    yield value
        except TimeoutError:
            raise ProtocolError("event stream failed", kind="timeout") from None
        except aiohttp.ClientError:
            raise ProtocolError("event stream failed", kind="closed") from None
        except ValueError, TypeError, KeyError:
            raise ProtocolError("event stream failed", kind="malformed") from None

    async def export(
        self, satellite: str, since_utc_ns: int
    ) -> AsyncIterator[dict[str, Any]]:
        identifier(satellite)
        unsigned(since_utc_ns)
        try:
            async with self._connect(
                self._connection().get,
                "/v1/export",
                params={"satellite": satellite, "since_utc_ns": str(since_utc_ns)},
                timeout=self._connection().timeout,
                allow_redirects=False,
            ) as response:
                if response.status != 200:
                    raise ProtocolError("export refused", kind="refused")
                line = bytearray()
                cursor = 0
                async for chunk in response.content.iter_chunked(1024):
                    for byte in chunk:
                        if byte == 10:
                            value = self._envelope(
                                parse_json(bytes(line), MAX_CONTROL), satellite, cursor
                            )
                            if value["event"]["t_utc_ns"] < since_utc_ns:
                                raise ProtocolError(
                                    "export time mismatch", kind="malformed"
                                )
                            cursor = value["event_id"]
                            yield value
                            line.clear()
                        else:
                            if len(line) >= MAX_CONTROL:
                                raise ProtocolError(
                                    "export line too large", kind="malformed"
                                )
                            line.append(byte)
                if line:
                    raise ProtocolError("incomplete export line", kind="malformed")
        except TimeoutError:
            raise ProtocolError("export failed", kind="timeout") from None
        except aiohttp.ClientError:
            raise ProtocolError("export failed", kind="closed") from None
        except ValueError, TypeError, KeyError:
            raise ProtocolError("export failed", kind="malformed") from None

    async def _audio(
        self,
        turn_id: str,
        source: AsyncIterator[bytes] | AsyncIterator[str],
        mode: str,
        voice: str | None = None,
        text_positions: bool = False,
    ) -> AsyncGenerator[dict[str, Any] | bytes]:
        sender: asyncio.Task[None] | None = None
        received_terminal = False
        sent_characters = 0
        previous_text_end = 0
        try:
            async with self._connect(
                self._connection().ws_connect,
                "/v1/audio",
                # aiohttp refuses equality. The protocol maximum is inclusive.
                max_msg_size=MAX_AUDIO + 1,
                compress=0,
            ) as ws:
                fields: dict[str, object] = {
                    "mode": mode,
                    "capabilities": [mode, "speaker_result"],
                }
                if mode == "tts":
                    if voice is not None:
                        fields["voice"] = voice
                    if text_positions:
                        fields["text_positions"] = True
                await ws.send_json(encode_control("negotiate", turn_id, **fields))
                reply = await ws.receive(timeout=MESSAGE_DEADLINE)
                if reply.type != aiohttp.WSMsgType.TEXT:
                    raise ProtocolError("negotiation refused", kind="closed")
                negotiated = decode_control(reply.data, turn_id)
                if (
                    negotiated["type"] != "negotiated"
                    or mode not in negotiated["capabilities"]
                ):
                    raise ProtocolError("negotiation refused", kind="refused")

                async def send() -> None:
                    nonlocal sent_characters
                    seq = 0
                    async for chunk in source:
                        if mode == "stt" and isinstance(chunk, bytes):
                            await ws.send_bytes(encode_audio(turn_id, seq, chunk))
                            seq += 1
                        elif mode == "tts" and isinstance(chunk, str):
                            for message in encode_text(turn_id, chunk):
                                sent_characters += len(decode_control(message)["text"])
                                await ws.send_str(message)
                        else:
                            raise ProtocolError("invalid audio input", kind="malformed")
                    await ws.send_json(encode_control("end", turn_id))

                sender = asyncio.create_task(send(), name="assist-live-client-send")
                expected_seq = 0
                got_format = False
                while True:
                    if sender.done() and not received_terminal:
                        sender.result()
                    reply = await ws.receive(timeout=MESSAGE_DEADLINE)
                    if (
                        reply.type == aiohttp.WSMsgType.BINARY
                        and mode == "tts"
                        and got_format
                    ):
                        seq, pcm = decode_audio(reply.data, turn_id)
                        if seq != expected_seq:
                            raise ProtocolError(
                                "audio sequence mismatch", kind="malformed"
                            )
                        expected_seq += 1
                        yield pcm
                    elif reply.type == aiohttp.WSMsgType.TEXT:
                        value = decode_control(
                            reply.data,
                            turn_id,
                            text_positions=mode != "tts" or text_positions,
                        )
                        if (
                            mode == "tts"
                            and value["type"] == "sentence_end"
                            and "text_end" in value
                        ):
                            text_end = value["text_end"]
                            if not previous_text_end <= text_end <= sent_characters:
                                raise ProtocolError(
                                    "TTS text position out of range", kind="malformed"
                                )
                            previous_text_end = text_end
                        if value["type"] in {"end", "error"} or (
                            mode == "stt" and value["type"] == "final"
                        ):
                            received_terminal = True
                            sender.cancel()
                        if value["type"] == "end":
                            return
                        if value["type"] == "error":
                            if value["code"] == "unknown_tts_voice":
                                raise ProtocolError("unknown_tts_voice", kind="refused")
                            raise ProtocolError("audio refused", kind="refused")
                        if value["type"] == "format":
                            got_format = True
                        yield value
                    else:
                        raise ProtocolError("audio stream stopped", kind="closed")
        except TimeoutError:
            raise ProtocolError("message deadline exceeded", kind="timeout") from None
        except aiohttp.ClientError:
            raise ProtocolError("audio stream failed", kind="closed") from None
        except ValueError, TypeError, KeyError:
            raise ProtocolError("audio stream failed", kind="malformed") from None
        finally:
            if sender is not None:
                sender.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    try:
                        await sender
                    except aiohttp.ClientConnectionResetError:
                        if not received_terminal:
                            raise

    async def stt(
        self, turn_id: str, audio: AsyncIterator[bytes]
    ) -> AsyncIterator[dict[str, Any]]:
        stream = self._audio(turn_id, audio, "stt")
        try:
            async for value in stream:
                if not isinstance(value, dict):
                    raise ProtocolError("unexpected PCM", kind="malformed")
                yield value
        finally:
            await stream.aclose()

    async def tts(
        self,
        turn_id: str,
        text: AsyncIterator[str],
        voice: str | None = None,
        *,
        text_positions: bool = False,
    ) -> AsyncIterator[bytes]:
        stream = self._audio(turn_id, text, "tts", voice, text_positions)
        try:
            async for value in stream:
                if (
                    isinstance(value, dict)
                    and value["type"] == "format"
                    and (
                        value["codec"],
                        value["rate"],
                        value["channels"],
                        value["width"],
                    )
                    != ("pcm_s16le", 16000, 1, 2)
                ):
                    raise ProtocolError(
                        "audio format needs the typed TTS stream", kind="malformed"
                    )
                if isinstance(value, bytes):
                    yield value
        finally:
            await stream.aclose()

    async def tts_stream(
        self,
        turn_id: str,
        text: AsyncIterator[str],
        voice: str | None = None,
        *,
        text_positions: bool = False,
    ) -> AsyncIterator[TTSItem]:
        """Yield the declared format, audio, and sentence-completion items."""
        stream = self._audio(turn_id, text, "tts", voice, text_positions)
        got_format = False
        pending_audio = False
        sentence = 0
        try:
            async for value in stream:
                if isinstance(value, bytes):
                    if not got_format:
                        raise ProtocolError(
                            "TTS format required before audio", kind="malformed"
                        )
                    pending_audio = True
                    yield TTSAudio(value)
                elif value["type"] == "format":
                    if got_format:
                        raise ProtocolError("duplicate TTS format", kind="malformed")
                    got_format = True
                    yield TTSFormat(
                        value["codec"], value["rate"], value["channels"], value["width"]
                    )
                elif value["type"] == "sentence_end":
                    if not got_format or value["sentence"] != sentence:
                        raise ProtocolError(
                            "TTS sentence sequence mismatch", kind="malformed"
                        )
                    sentence += 1
                    pending_audio = False
                    yield TTSSentenceEnd(value["sentence"], value.get("text_end"))
            if not got_format or pending_audio:
                raise ProtocolError("incomplete TTS sentence stream", kind="malformed")
        finally:
            await stream.aclose()

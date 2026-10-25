import asyncio
import contextlib
import json
from collections.abc import AsyncGenerator, AsyncIterator
from dataclasses import asdict
from typing import Any

import aiohttp

from .models import (
    TimingEvent,
    TTSAudio,
    TTSFormat,
    TTSItem,
    TTSSentenceEnd,
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

    async def _request(
        self, method: str, path: str, *, data: object = None, timeout: float = 20
    ) -> Any:
        raw = json.dumps(data, allow_nan=False).encode() if data is not None else None
        if raw is not None and len(raw) > MAX_CONTROL:
            raise ProtocolError("request too large")
        try:
            async with self._connection().request(
                method,
                self.url + path,
                data=raw,
                headers=self._headers,
                timeout=aiohttp.ClientTimeout(total=timeout),
                allow_redirects=False,
            ) as response:
                if response.status != 200:
                    raise ProtocolError("request refused")
                value = bytearray()
                async for chunk in response.content.iter_chunked(1024):
                    if len(value) + len(chunk) > MAX_CONTROL:
                        raise ProtocolError("response too large")
                    value.extend(chunk)
                if path.startswith("/v1/bindings"):
                    bindings = json.loads(value)
                    if not isinstance(bindings, list) or any(
                        not isinstance(row, dict) or row.keys() != {"satellite"}
                        for row in bindings
                    ):
                        raise ProtocolError("invalid bindings")
                    for row in bindings:
                        identifier(row["satellite"])
                    return bindings
                return parse_json(bytes(value), MAX_CONTROL)
        except aiohttp.ClientError, TimeoutError, ValueError:
            raise ProtocolError("request failed") from None

    async def health(self, timeout: float = 0.2) -> bool:
        try:
            value = await self._request("GET", "/health", timeout=timeout)
            return value.get("version") == 1 and value.get("healthy") is True
        except ProtocolError:
            return False

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
            raise ProtocolError("invalid event envelope")
        unsigned(value["event_id"])
        event = TimingEvent(**value["event"])
        if event.satellite != satellite or value["event_id"] <= cursor:
            raise ProtocolError("event isolation or cursor mismatch")
        return value

    async def events(
        self, satellite: str, after_event_id: int = 0
    ) -> AsyncIterator[dict[str, Any]]:
        identifier(satellite)
        unsigned(after_event_id)
        try:
            async with self._connection().ws_connect(
                self.url + "/v1/events",
                headers=self._headers,
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
                        raise ProtocolError("event stream stopped")
                    value = self._envelope(
                        parse_json(message.data, MAX_CONTROL), satellite, after_event_id
                    )
                    after_event_id = value["event_id"]
                    yield value
        except aiohttp.ClientError, TimeoutError, ValueError, TypeError, KeyError:
            raise ProtocolError("event stream failed") from None

    async def export(
        self, satellite: str, since_utc_ns: int
    ) -> AsyncIterator[dict[str, Any]]:
        identifier(satellite)
        unsigned(since_utc_ns)
        try:
            async with self._connection().get(
                self.url + "/v1/export",
                params={"satellite": satellite, "since_utc_ns": str(since_utc_ns)},
                headers=self._headers,
                allow_redirects=False,
            ) as response:
                if response.status != 200:
                    raise ProtocolError("export refused")
                line = bytearray()
                cursor = 0
                async for chunk in response.content.iter_chunked(1024):
                    for byte in chunk:
                        if byte == 10:
                            value = self._envelope(
                                parse_json(bytes(line), MAX_CONTROL), satellite, cursor
                            )
                            if value["event"]["t_utc_ns"] < since_utc_ns:
                                raise ProtocolError("export time mismatch")
                            cursor = value["event_id"]
                            yield value
                            line.clear()
                        else:
                            if len(line) >= MAX_CONTROL:
                                raise ProtocolError("export line too large")
                            line.append(byte)
                if line:
                    raise ProtocolError("incomplete export line")
        except aiohttp.ClientError, TimeoutError, ValueError, TypeError, KeyError:
            raise ProtocolError("export failed") from None

    async def _audio(
        self,
        turn_id: str,
        source: AsyncIterator[bytes] | AsyncIterator[str],
        mode: str,
        voice: str = "",
    ) -> AsyncGenerator[dict[str, Any] | bytes]:
        sender: asyncio.Task[None] | None = None
        received_terminal = False
        try:
            async with self._connection().ws_connect(
                self.url + "/v1/audio",
                headers=self._headers,
                # aiohttp refuses equality. The protocol maximum is inclusive.
                max_msg_size=MAX_AUDIO + 1,
                compress=0,
            ) as ws:
                fields: dict[str, object] = {
                    "mode": mode,
                    "capabilities": [mode, "speaker_result"],
                }
                if mode == "tts":
                    fields["voice"] = voice
                await ws.send_json(encode_control("negotiate", turn_id, **fields))
                reply = await ws.receive(timeout=MESSAGE_DEADLINE)
                if reply.type != aiohttp.WSMsgType.TEXT:
                    raise ProtocolError("negotiation refused")
                negotiated = decode_control(reply.data, turn_id)
                if (
                    negotiated["type"] != "negotiated"
                    or mode not in negotiated["capabilities"]
                ):
                    raise ProtocolError("negotiation refused")

                async def send() -> None:
                    seq = 0
                    async for chunk in source:
                        if mode == "stt" and isinstance(chunk, bytes):
                            await ws.send_bytes(encode_audio(turn_id, seq, chunk))
                            seq += 1
                        elif mode == "tts" and isinstance(chunk, str):
                            await ws.send_json(
                                encode_control("text", turn_id, text=chunk)
                            )
                        else:
                            raise ProtocolError("invalid audio input")
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
                            raise ProtocolError("audio sequence mismatch")
                        expected_seq += 1
                        yield pcm
                    elif reply.type == aiohttp.WSMsgType.TEXT:
                        value = decode_control(reply.data, turn_id)
                        if value["type"] == "end" or (
                            mode == "stt" and value["type"] == "final"
                        ):
                            received_terminal = True
                            sender.cancel()
                        if value["type"] == "end":
                            return
                        if value["type"] == "error":
                            raise ProtocolError("audio refused")
                        if value["type"] == "format":
                            got_format = True
                        yield value
                    else:
                        raise ProtocolError("audio stream stopped")
        except TimeoutError:
            raise ProtocolError("message deadline exceeded") from None
        except aiohttp.ClientError, ValueError, TypeError, KeyError:
            raise ProtocolError("audio stream failed") from None
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
                    raise ProtocolError("unexpected PCM")
                yield value
        finally:
            await stream.aclose()

    async def tts(
        self, turn_id: str, text: AsyncIterator[str], voice: str
    ) -> AsyncIterator[bytes]:
        stream = self._audio(turn_id, text, "tts", voice)
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
                    raise ProtocolError("audio format needs the typed TTS stream")
                if isinstance(value, bytes):
                    yield value
        finally:
            await stream.aclose()

    async def tts_stream(
        self, turn_id: str, text: AsyncIterator[str], voice: str
    ) -> AsyncIterator[TTSItem]:
        """Yield the declared format, audio, and sentence-completion items."""
        stream = self._audio(turn_id, text, "tts", voice)
        got_format = False
        pending_audio = False
        sentence = 0
        try:
            async for value in stream:
                if isinstance(value, bytes):
                    if not got_format:
                        raise ProtocolError("TTS format required before audio")
                    pending_audio = True
                    yield TTSAudio(value)
                elif value["type"] == "format":
                    if got_format:
                        raise ProtocolError("duplicate TTS format")
                    got_format = True
                    yield TTSFormat(
                        value["codec"], value["rate"], value["channels"], value["width"]
                    )
                elif value["type"] == "sentence_end":
                    if not got_format or value["sentence"] != sentence:
                        raise ProtocolError("TTS sentence sequence mismatch")
                    sentence += 1
                    pending_audio = False
                    yield TTSSentenceEnd(value["sentence"])
            if not got_format or pending_audio:
                raise ProtocolError("incomplete TTS sentence stream")
        finally:
            await stream.aclose()

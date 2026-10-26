"""Explicit cloud STT with header authentication and per-connection opt-out."""

import asyncio
import json
from contextlib import suppress

import aiohttp
from yarl import URL

from .stt import AudioLimitError, STTClosedError


class DeepgramError(RuntimeError):
    """The cloud operation failed without provider response details."""


class DeepgramProvider:
    destination = "cloud"

    def __init__(
        self,
        api_key: str,
        *,
        endpoint: str = "wss://api.deepgram.com/v1/listen",
        timeout: float = 15.0,
    ) -> None:
        if not api_key or any(c in api_key for c in "\r\n"):
            raise ValueError("invalid Deepgram credential")
        url = None
        try:
            url = URL(endpoint)
            _ = url.host
        except ValueError:
            url = None
        if (
            url is None
            or url.scheme not in {"http", "https", "ws", "wss"}
            or (url.scheme in {"http", "ws"} and url.host not in {"127.0.0.1", "::1"})
            or not url.host
            or url.user is not None
            or url.query_string
            or url.fragment
            or api_key in endpoint
            or timeout <= 0
        ):
            raise ValueError("invalid Deepgram endpoint or timeout")
        self._api_key = api_key
        self._endpoint = url
        self._timeout = timeout

    def session(self) -> DeepgramSession:
        return DeepgramSession(self)


class DeepgramSession:
    def __init__(self, provider: DeepgramProvider) -> None:
        self._provider = provider
        self._client: aiohttp.ClientSession | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._reader: asyncio.Task[None] | None = None
        self._closing: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._changed = asyncio.Event()
        self._finals: list[str] = []
        self._interim = ""
        self._onset = False
        self._cancelled = False
        self._finishing = False
        self._finished = False
        self._failed = False
        self._complete = False
        self._bytes = 0

    def _check(self) -> None:
        if self._cancelled:
            raise STTClosedError("STT session cancelled")
        if self._failed:
            raise DeepgramError("Deepgram stream failed")

    async def connect(self) -> None:
        self._check()
        if self._finished or self._finishing:
            raise STTClosedError("STT session finished or finishing")
        if self._reader is not None and not self._reader.done():
            return
        failed = False
        try:
            if self._client is None:
                trace = aiohttp.TraceConfig()

                async def refuse_redirect(
                    session: aiohttp.ClientSession,
                    context: object,
                    params: aiohttp.TraceRequestRedirectParams,
                ) -> None:
                    raise DeepgramError("Deepgram redirect refused")

                trace.on_request_redirect.append(refuse_redirect)
                self._client = aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=self._provider._timeout),
                    trust_env=False,
                    trace_configs=[trace],
                )
            self._ws = await self._client.ws_connect(
                self._provider._endpoint,
                params={
                    "model": "nova-3",
                    "mip_opt_out": "true",
                    "encoding": "linear16",
                    "sample_rate": "16000",
                    "channels": "1",
                    "interim_results": "true",
                },
                headers={"Authorization": "Token " + self._provider._api_key},
                timeout=aiohttp.ClientWSTimeout(
                    ws_receive=self._provider._timeout, ws_close=1.0
                ),
                max_msg_size=262144,
            )
            self._reader = asyncio.create_task(self._receive())
        except asyncio.CancelledError:
            self.cancel()
            await self.aclose()
            raise
        except Exception:
            failed = True
        # Do not retain the original exception as a context: headers and remote
        # reason text can contain credentials even when the URL does not.
        if failed:
            self._failed = True
            await self.aclose()
            raise DeepgramError("Deepgram connection failed")

    async def _receive(self) -> None:
        assert self._ws is not None
        try:
            async for message in self._ws:
                if message.type != aiohttp.WSMsgType.TEXT:
                    self._failed = True
                    break
                value = json.loads(message.data)
                if value.get("type") == "Results":
                    text = value["channel"]["alternatives"][0]["transcript"]
                    if not isinstance(text, str) or not isinstance(
                        value["is_final"], bool
                    ):
                        raise ValueError("invalid transcript result")
                    if value["is_final"]:
                        if text:
                            self._finals.append(text)
                        self._interim = ""
                    else:
                        self._interim = text
                    self._changed.set()
                elif value.get("type") == "Metadata" and self._finishing:
                    self._complete = True
                elif value.get("type") == "Error":
                    self._failed = True
                    break
            if self._ws.exception() is not None:
                self._failed = True
            if self._bytes and not self._finishing:
                # Replaying accepted audio can duplicate words; reconnecting
                # without it can lose words. Fail this turn instead of either.
                self._failed = True
        except asyncio.CancelledError:
            raise
        except Exception:
            self._failed = True
        finally:
            self._changed.set()
            with suppress(Exception):
                await self._ws.close()

    def speech_started(self) -> None:
        self._check()
        self._onset = True

    async def push(self, pcm: bytes) -> None:
        self._check()
        if self._finished or self._finishing:
            raise STTClosedError("STT session finished or finishing")
        if len(pcm) % 2:
            raise ValueError("mono int16 PCM must contain complete samples")
        if self._bytes + len(pcm) > 480000:
            raise AudioLimitError("STT audio exceeds 15 s; finish accepted audio")
        if not pcm:
            return
        failed = False
        try:
            await self.connect()
            assert self._ws is not None
            await self._ws.send_bytes(pcm)
            self._bytes += len(pcm)
        except asyncio.CancelledError:
            self.cancel()
            await self.aclose()
            raise
        except Exception:
            failed = True
        if failed:
            self._failed = True
            await self.aclose()
            raise DeepgramError("Deepgram audio send failed")

    async def partial(self) -> str:
        self._check()
        if not self._onset:
            return ""
        if self._bytes and not self._changed.is_set():
            failed = False
            try:
                async with asyncio.timeout(self._provider._timeout):
                    await self._changed.wait()
            except asyncio.CancelledError:
                self.cancel()
                await self.aclose()
                raise
            except Exception:
                failed = True
            if failed:
                self._failed = True
        self._check()
        return " ".join([*self._finals, self._interim]).strip()

    async def finish(self) -> str:
        async with self._lock:
            self._check()
            if self._finished:
                return " ".join(self._finals)
            if not self._bytes:
                self._finished = True
                await self.aclose()
                return ""
            self._finishing = True
            failed = False
            try:
                assert self._ws is not None and self._reader is not None
                await self._ws.send_json({"type": "CloseStream"})
                async with asyncio.timeout(self._provider._timeout):
                    await asyncio.shield(self._reader)
                self._check()
                if not self._complete:
                    failed = True
            except asyncio.CancelledError:
                self.cancel()
                await self.aclose()
                raise
            except Exception:
                failed = True
            finally:
                await self.aclose()
            if failed:
                self._failed = True
                raise DeepgramError("Deepgram finalization failed")
            self._finished = True
            return " ".join(self._finals)

    def cancel(self) -> None:
        self._cancelled = True
        self._finals.clear()
        self._interim = ""
        if self._reader is not None:
            self._reader.cancel()
        if self._client is not None and self._closing is None:
            self._closing = asyncio.create_task(self.aclose())

    async def aclose(self) -> None:
        if self._closing is not None and self._closing is not asyncio.current_task():
            await asyncio.shield(self._closing)
            return
        if self._reader is not None and not self._reader.done():
            self._reader.cancel()
        if self._reader is not None:
            await asyncio.gather(self._reader, return_exceptions=True)
        if self._ws is not None:
            with suppress(Exception):
                await self._ws.close()
        if self._client is not None:
            with suppress(Exception):
                await self._client.close()

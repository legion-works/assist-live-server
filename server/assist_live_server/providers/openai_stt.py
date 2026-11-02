"""Cloud transcription contract: assist-live docs/design.md, section 5.4."""

import asyncio
import base64
import json
from contextlib import suppress

import aiohttp
import numpy as np
from assist_live_client.protocol import MAX_CONTROL
from yarl import URL

from .deepgram import MAX_FINAL_SEGMENTS, MAX_SEGMENT_BYTES, MAX_TRANSCRIPT_BYTES
from .parakeet import MAX_AUDIO_BYTES
from .stt import AudioLimitError, STTClosedError


class OpenAIError(RuntimeError):
    """The cloud operation failed without provider response details."""


class OpenAIProvider:
    destination = "cloud"

    def __init__(
        self,
        api_key: str,
        *,
        endpoint: str = "wss://api.openai.com/v1/realtime",
        timeout: float = 15.0,
        delay: str = "low",
    ) -> None:
        if not api_key or any(c in api_key for c in "\r\n"):
            raise ValueError("invalid OpenAI credential")
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
            raise ValueError("invalid OpenAI endpoint or timeout")
        if delay not in {"minimal", "low", "medium", "high", "xhigh"}:
            raise ValueError("invalid OpenAI transcription delay")
        self._api_key = api_key
        self._endpoint = url
        self._timeout = timeout
        self._delay = delay

    def session(self) -> OpenAISession:
        return OpenAISession(self)


class OpenAISession:
    final_redecodes = False

    def __init__(self, provider: OpenAIProvider) -> None:
        self._provider = provider
        self._client: aiohttp.ClientSession | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._reader: asyncio.Task[None] | None = None
        self._closing: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._changed = asyncio.Event()
        self._items: dict[str, str] = {}
        self._completed: set[str] = set()
        self._item_failures: set[str] = set()
        self._committed: str | None = None
        self._partial_item: str | None = None
        self._final = ""
        self._messages = 0
        self._onset = False
        self._cancelled = False
        self._finishing = False
        self._finished = False
        self._failed = False
        self._complete = False
        self._bytes = 0
        self._last_sample = 0

    def _check(self) -> None:
        if self._cancelled:
            raise STTClosedError("STT session cancelled")
        if self._failed:
            raise OpenAIError("OpenAI stream failed")

    async def connect(self) -> None:
        self._check()
        if self._finished or self._finishing:
            raise STTClosedError("STT session finished or finishing")
        if self._reader is not None:
            if self._reader.done():
                self._failed = True
                self._check()
            return
        failed = False
        try:
            trace = aiohttp.TraceConfig()

            async def refuse_redirect(
                session: aiohttp.ClientSession,
                context: object,
                params: aiohttp.TraceRequestRedirectParams,
            ) -> None:
                raise OpenAIError("OpenAI redirect refused")

            trace.on_request_redirect.append(refuse_redirect)
            self._client = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self._provider._timeout),
                trust_env=False,
                trace_configs=[trace],
            )
            self._ws = await self._client.ws_connect(
                self._provider._endpoint,
                params={"intent": "transcription"},
                headers={"Authorization": "Bearer " + self._provider._api_key},
                timeout=aiohttp.ClientWSTimeout(ws_receive=None, ws_close=1.0),
                max_msg_size=MAX_CONTROL * 16,
            )
            await self._ws.send_json(
                {
                    "type": "session.update",
                    "session": {
                        "type": "transcription",
                        "audio": {
                            "input": {
                                "format": {
                                    "type": "audio/pcm",
                                    "rate": 24000,
                                },
                                "transcription": {
                                    "model": "gpt-live-transcribe",
                                    "delay": self._provider._delay,
                                },
                                "turn_detection": None,
                            }
                        },
                    },
                }
            )
            self._reader = asyncio.create_task(self._receive())
        except asyncio.CancelledError:
            self.cancel()
            await self.aclose()
            raise
        except Exception:
            failed = True
        # Transport exceptions can contain authentication headers or remote text.
        # Raise outside the handler so the exception retains no secret context.
        if failed:
            self._failed = True
            await self.aclose()
            raise OpenAIError("OpenAI connection failed")

    def _result(self, value: dict[str, object]) -> None:
        kind = value.get("type")
        if kind == "error":
            raise ValueError("remote error")
        if kind == "input_audio_buffer.committed":
            item = value.get("item_id")
            if not self._finishing or self._committed is not None:
                raise ValueError("unexpected commit")
            self._committed = self._item_id(item)
        elif kind in {
            "conversation.item.input_audio_transcription.delta",
            "conversation.item.input_audio_transcription.completed",
            "conversation.item.input_audio_transcription.failed",
        }:
            item = self._item_id(value.get("item_id"))
            if kind == "conversation.item.input_audio_transcription.failed":
                self._item_failures.add(item)
            else:
                delta = kind == "conversation.item.input_audio_transcription.delta"
                text = value.get("delta" if delta else "transcript")
                if (
                    not isinstance(text, str)
                    or len(text.encode("utf-8")) > MAX_SEGMENT_BYTES
                    or self._provider._api_key in text
                ):
                    raise ValueError("invalid transcript segment")
                text = self._items.get(item, "") + text if delta else text
                others = [v for k, v in self._items.items() if k != item]
                budget = sum(len(json.dumps(v).encode()) - 2 for v in [*others, text])
                if budget + len(others) > MAX_TRANSCRIPT_BYTES:
                    raise ValueError("transcript limit exceeded")
                self._items[item] = text
                if self._partial_item is None:
                    self._partial_item = item
                if not delta:
                    self._completed.add(item)
                self._changed.set()
        # The public design requires item-ID matching, not event arrival order.
        if self._committed in self._item_failures:
            raise ValueError("transcription failed")
        if self._committed in self._completed:
            assert self._committed is not None
            self._final = self._items[self._committed]
            self._complete = True

    @staticmethod
    def _item_id(value: object) -> str:
        if not isinstance(value, str) or not value or len(value) > MAX_SEGMENT_BYTES:
            raise ValueError("invalid item ID")
        return value

    async def _receive(self) -> None:
        assert self._ws is not None
        try:
            async for message in self._ws:
                self._messages += 1
                if (
                    message.type != aiohttp.WSMsgType.TEXT
                    or self._messages > MAX_FINAL_SEGMENTS
                ):
                    raise ValueError("message limit exceeded")
                self._result(json.loads(message.data))
                if self._complete:
                    break
            if not self._complete:
                self._failed = True
        except asyncio.CancelledError:
            raise
        except Exception:
            self._failed = True
        finally:
            self._changed.set()
            try:
                await self._ws.close()
            except Exception:
                self._failed = True

    def speech_started(self) -> None:
        self._check()
        self._onset = True

    def _resample(self, pcm: bytes) -> bytes:
        samples = np.frombuffer(pcm, dtype="<i2")
        before = self._bytes // 2
        after = before + len(samples)
        # Causal interpolation needs only the last input sample across pushes.
        # The 2:3 ratio keeps sample counts and phase independent of chunk size.
        positions = (
            (np.arange(before * 3 // 2, after * 3 // 2) + 1) * 2 - before * 3
        ) / 3
        values = np.concatenate(
            ([self._last_sample if before else int(samples[0])], samples)
        )
        result = np.rint(np.interp(positions, np.arange(len(values)), values))
        self._last_sample = int(samples[-1])
        return result.astype("<i2").tobytes()

    async def push(self, pcm: bytes) -> None:
        self._check()
        if self._finished or self._finishing:
            raise STTClosedError("STT session finished or finishing")
        if len(pcm) % 2:
            raise ValueError("mono int16 PCM must contain complete samples")
        if self._bytes + len(pcm) > MAX_AUDIO_BYTES:
            raise AudioLimitError("STT audio exceeds 15 s; finish accepted audio")
        if not pcm:
            return
        failed = False
        try:
            await self.connect()
            assert self._ws is not None
            await self._ws.send_json(
                {
                    "type": "input_audio_buffer.append",
                    "audio": base64.b64encode(self._resample(pcm)).decode("ascii"),
                }
            )
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
            raise OpenAIError("OpenAI audio send failed")

    async def partial(self) -> str:
        self._check()
        if not self._onset:
            return ""
        item = self._committed or self._partial_item
        return self._items.get(item, "") if item is not None else ""

    async def finish(self) -> str:
        async with self._lock:
            self._check()
            if self._finished:
                return self._final
            if not self._bytes:
                self._finished = True
                await self.aclose()
                return ""
            self._finishing = True
            failed = False
            try:
                assert self._ws is not None and self._reader is not None
                await self._ws.send_json({"type": "input_audio_buffer.commit"})
                async with asyncio.timeout(self._provider._timeout):
                    await asyncio.shield(self._reader)
                failed = self._failed or not self._complete
            except asyncio.CancelledError:
                self.cancel()
                await self.aclose()
                raise
            except Exception:
                failed = True
            finally:
                await self.aclose()
            if failed or self._failed:
                self._failed = True
                raise OpenAIError("OpenAI finalization failed")
            self._finished = True
            return self._final

    def cancel(self) -> None:
        self._cancelled = True
        self._items.clear()
        self._completed.clear()
        self._item_failures.clear()
        self._final = ""
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

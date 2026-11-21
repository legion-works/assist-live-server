import asyncio

import pytest
from aiohttp import WSMsgType, web
from assist_live_server.providers.deepgram import DeepgramProvider
from assist_live_server.providers.openai_stt import OpenAIProvider


@pytest.mark.parametrize("provider", [DeepgramProvider, OpenAIProvider])
@pytest.mark.parametrize("outcome", ["success", "failed"])
def test_cancel_after_finish_does_not_start_second_close(
    cloud_ws_server, monkeypatch, provider, outcome
):
    async def run():
        async def handler(request):
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            async for message in ws:
                if message.type != WSMsgType.TEXT:
                    continue
                kind = message.json()["type"]
                if outcome == "failed" and kind in {
                    "CloseStream",
                    "input_audio_buffer.commit",
                }:
                    await ws.close()
                    continue
                if kind == "CloseStream":
                    await ws.send_json({"type": "Metadata"})
                    await ws.close()
                elif kind == "input_audio_buffer.commit":
                    await ws.send_json(
                        {"type": "input_audio_buffer.committed", "item_id": "selected"}
                    )
                    await ws.send_json(
                        {
                            "type": "conversation.item.input_audio_transcription."
                            "completed",
                            "item_id": "selected",
                            "content_index": 0,
                            "transcript": "final words",
                        }
                    )
            return ws

        async with cloud_ws_server(handler) as fake:
            session = provider("synthetic-cloud-key", endpoint=fake.url).session()
            calls = []
            close = session.aclose

            async def tracked_close():
                calls.append(True)
                await close()

            monkeypatch.setattr(session, "aclose", tracked_close)
            try:
                await session.push(bytes(1024))
                if outcome == "failed":
                    with pytest.raises(RuntimeError, match="finalization failed"):
                        await session.finish()
                else:
                    await session.finish()
                assert session._client.closed
                assert session._ws.closed
                assert session._reader.done()
                session.cancel()
                assert session._closing is None, "CLOSED_SESSION_STARTED_CLOSE_TASK"
                assert calls == [True]
            finally:
                if session._closing is not None:
                    await session._closing

    asyncio.run(asyncio.wait_for(run(), 4))

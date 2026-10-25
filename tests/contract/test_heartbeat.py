import asyncio

import pytest


def test_heartbeat_and_cleanup(service, turn_id):
    async def run():
        from assist_live_client import TurnOpen

        async with service(block_backend=True) as (client, state):
            await client.open_turn(
                TurnOpen(turn_id, "satellite-a", "voice", None, None)
            )

            async def audio():
                yield b"\x00\x00"

            before = set(asyncio.all_tasks())
            replies = client.stt(turn_id, audio())
            first = await asyncio.wait_for(anext(replies), 0.5)
            assert first["type"] == "heartbeat"
            second = await asyncio.wait_for(anext(replies), 0.2)
            assert second["type"] == "heartbeat"
            await replies.aclose()
            await asyncio.wait_for(state.backend_closed.wait(), 1)
            assert not [
                task
                for task in asyncio.all_tasks() - before
                if task.get_name().startswith("assist-live-")
            ]

    asyncio.run(run())


def test_client_stops_after_message_deadline(silent_service, turn_id):
    async def run():
        from assist_live_client.protocol import ProtocolError

        async with silent_service() as client:

            async def audio():
                yield b"\x00\x00"

            try:
                await asyncio.wait_for(anext(client.stt(turn_id, audio())), 0.9)
            except ProtocolError as error:
                assert "deadline" in str(error)
            else:
                raise AssertionError("silent server did not stop")

    asyncio.run(run())


@pytest.mark.parametrize("gap", ["first", "between"])
def test_tts_text_gaps_do_not_use_server_message_deadline(service, turn_id, gap):
    from assist_live_client import TurnOpen
    from assist_live_client.protocol import MESSAGE_DEADLINE

    async def run():
        async with service(tts_parts=("first", "second")) as (client, _):
            await client.open_turn(
                TurnOpen(turn_id, "satellite-a", "tts_only", None, None)
            )

            async def text():
                for index, part in enumerate(["first", "second"]):
                    if (gap == "first" and index == 0) or (
                        gap == "between" and index == 1
                    ):
                        loop = asyncio.get_running_loop()
                        elapsed = loop.create_future()
                        loop.call_later(
                            MESSAGE_DEADLINE + 0.2, elapsed.set_result, None
                        )
                        await elapsed
                    yield part

            async with asyncio.timeout(3):
                assert [
                    chunk async for chunk in client.tts(turn_id, text(), "test-voice")
                ] == [b"\x01\x00"]

    asyncio.run(run())


def test_tts_text_idle_has_its_own_bound(service, turn_id, monkeypatch):
    from assist_live_client import TurnOpen
    from assist_live_client.protocol import ProtocolError
    from assist_live_server import app

    monkeypatch.setattr(app, "TEXT_IDLE_TIMEOUT", 0.05)

    async def run():
        async with service() as (client, _):
            await client.open_turn(
                TurnOpen(turn_id, "satellite-a", "tts_only", None, None)
            )

            async def silent_text():
                await asyncio.Event().wait()
                yield "test text"

            with pytest.raises(ProtocolError):
                async with asyncio.timeout(0.4):
                    _ = [
                        chunk
                        async for chunk in client.tts(
                            turn_id, silent_text(), "test-voice"
                        )
                    ]

    asyncio.run(run())

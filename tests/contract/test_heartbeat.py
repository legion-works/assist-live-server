import asyncio


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

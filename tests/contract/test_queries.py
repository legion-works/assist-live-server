import asyncio
import logging


def test_events_cursor_queries_export_and_isolation(service, turn_id, caplog):
    async def run():
        from assist_live_client import TimingEvent, TurnOpen
        from assist_live_client.protocol import ProtocolError

        caplog.set_level(logging.DEBUG)
        async with service() as (client, state):
            assert await client.health()
            assert await client.bindings() == [{"satellite": "satellite-a"}]
            await client.open_turn(
                TurnOpen(turn_id, "satellite-a", "voice", None, None)
            )
            assert (await client.turn(turn_id))["satellite"] == "satellite-a"
            for time in [10, 20]:
                await client.event(
                    TimingEvent(
                        turn_id,
                        "satellite-a",
                        "integration",
                        "mic_open",
                        time,
                        "measured",
                        None,
                    )
                )
            subscription = client.events("satellite-a", 1)
            received = await anext(subscription)
            assert received["event_id"] == 2
            assert received["event"]["quality"] == "measured"
            await subscription.aclose()
            await client.event(
                TimingEvent(
                    turn_id,
                    "satellite-a",
                    "integration",
                    "fallback_used",
                    30,
                    "measured",
                    None,
                    data={
                        "part": "stt",
                        "provider": "test-provider",
                        "destination": "local",
                    },
                )
            )
            reconnected = client.events("satellite-a", received["event_id"])
            resumed = await anext(reconnected)
            assert resumed["event_id"] == 3
            assert resumed["event"]["data"] == {
                "part": "stt",
                "provider": "test-provider",
                "destination": "local",
            }
            await reconnected.aclose()
            assert [r["event_id"] async for r in client.export("satellite-a", 15)] == [
                2,
                3,
            ]
            for path in [
                "/health?satellite=satellite-b",
                "/v1/export?satellite=satellite-b",
                "/v1/bindings?satellite=satellite-b",
            ]:
                async with state.session.get(
                    state.url + path, headers=state.headers
                ) as response:
                    assert response.status == 403
            state.store.turns[turn_id]["satellite"] = "satellite-b"
            try:
                await client.turn(turn_id)
            except ProtocolError:
                pass
            else:
                raise AssertionError("cross-satellite query accepted")
            for token in ["test-device-secret", "invalid-integration-token"]:
                async with state.session.get(
                    state.url + "/health", headers={"Authorization": "Bearer " + token}
                ) as response:
                    assert response.status == 401
                async with state.session.get(
                    state.url + "/v1/audio",
                    headers={"Authorization": "Bearer " + token},
                ) as response:
                    assert response.status == 401
            assert "integration-test-token" not in caplog.text
            assert "test-device-secret" not in caplog.text

    asyncio.run(run())


def test_transcripts_require_both_opt_ins(service, turn_id):
    async def run():
        from assist_live_client import TimingEvent, TurnOpen

        for policy in [False, True]:
            for opt_in in [False, True]:
                async with service(record_transcripts=policy) as (client, state):
                    await client.open_turn(
                        TurnOpen(
                            turn_id,
                            "satellite-a",
                            "voice",
                            None,
                            None,
                            record_transcripts=opt_in,
                        )
                    )
                    await client.event(
                        TimingEvent(
                            turn_id,
                            "satellite-a",
                            "integration",
                            "stt_final",
                            1,
                            "measured",
                            None,
                            data={"transcript": "test text"},
                        )
                    )
                    data = state.store.rows[0]["event"]["data"]
                    assert ("transcript" in data) == (policy and opt_in)

    asyncio.run(run())


def test_write_and_subscription_isolation(service, turn_id):
    async def run():
        import json

        from aiohttp import WSMsgType
        from assist_live_client import TimingEvent, TurnOpen
        from assist_live_client.protocol import ProtocolError

        async with service() as (client, state):
            await client.open_turn(
                TurnOpen(turn_id, "satellite-a", "voice", None, None)
            )
            for event in [
                TimingEvent(
                    turn_id,
                    "satellite-b",
                    "integration",
                    "mic_open",
                    1,
                    "measured",
                    None,
                ),
                TimingEvent(
                    turn_id, "satellite-a", "device", "mic_open", 1, "measured", None
                ),
            ]:
                try:
                    await client.event(event)
                except ProtocolError:
                    pass
                else:
                    raise AssertionError("unauthorized write accepted")
            assert not state.store.rows
            async with state.session.ws_connect(
                state.url + "/v1/events", headers=state.headers
            ) as ws:
                await ws.send_str(
                    json.dumps(
                        {
                            "type": "subscribe",
                            "version": 1,
                            "satellite": "satellite-b",
                            "after_event_id": 0,
                        }
                    )
                )
                reply = await ws.receive(timeout=1)
                assert reply.type == WSMsgType.CLOSE and reply.data == 1008

            async def oversized_body():
                yield b"x" * 16385

            async with state.session.post(
                state.url + "/v1/event", headers=state.headers, data=oversized_body()
            ) as response:
                assert response.status == 400
            assert not state.store.rows

    asyncio.run(run())

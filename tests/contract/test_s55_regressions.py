import asyncio
import hashlib
import sqlite3
from contextlib import suppress
from uuid import uuid7

import pytest
from aiohttp import ClientPayloadError, WSMsgType, web
from assist_live_client import TimingEvent, TurnOpen
from assist_live_client.protocol import ProtocolError
from assist_live_server import __main__ as startup
from assist_live_server import app
from assist_live_server import store as storage

from tests.contract.test_event_store import packet
from tests.contract.test_event_store import service as sqlite_service


def test_hint_depth_refused_as_invalid_hints(service, monkeypatch):
    validate = app.validate_hints
    calls = []

    def observe(value):
        calls.append(value)
        return validate(value)

    monkeypatch.setattr(app, "validate_hints", observe)

    async def run():
        async with service() as (_, state):
            for depth in (32, 33, 2048):
                async with state.session.put(
                    state.url + "/v1/satellites/satellite-a/hints",
                    headers=state.headers,
                    data=b"[" * depth + b"0" + b"]" * depth,
                ) as response:
                    assert response.status == 400
                    assert await response.json() == {"error": "invalid_hints"}
            assert len(calls) == 1

    asyncio.run(run())


def mic_event(turn_id=None, stamp=1):
    return TimingEvent(
        turn_id, "satellite-a", "integration", "mic_open", stamp, "measured", None
    )


@pytest.mark.parametrize("route", ["export", "events"])
def test_stalled_event_output_terminates(service, monkeypatch, turn_id, route):
    monkeypatch.setattr(app, "MESSAGE_DEADLINE", 0.05)
    monkeypatch.setattr(app, "MAX_AUTH_PER_TOKEN", 1)

    async def run():
        started = asyncio.Event()
        release = asyncio.Event()
        cancelled = asyncio.Event()

        async def blocked(self, data):
            started.set()
            try:
                await release.wait()
            finally:
                cancelled.set()

        if route == "export":
            monkeypatch.setattr(web.StreamResponse, "write", blocked)
        else:
            monkeypatch.setattr(web.WebSocketResponse, "send_str", blocked)

        async with service() as (client, state):
            await client.open_turn(
                TurnOpen(turn_id, "satellite-a", "voice", None, None)
            )
            await client.event(mic_event(turn_id))
            try:
                if route == "export":
                    async with state.session.get(
                        state.url + "/v1/export?satellite=satellite-a",
                        headers=state.headers,
                    ) as response:
                        assert response.status == 200
                        await asyncio.wait_for(started.wait(), 2)
                        with pytest.raises(ClientPayloadError):
                            await asyncio.wait_for(response.read(), 1)
                else:
                    async with state.session.ws_connect(
                        state.url + "/v1/events", headers=state.headers
                    ) as ws:
                        await ws.send_json(
                            {
                                "version": 1,
                                "type": "subscribe",
                                "satellite": "satellite-a",
                                "after_event_id": 0,
                            }
                        )
                        await asyncio.wait_for(started.wait(), 2)
                        assert (await ws.receive(timeout=1)).type in {
                            WSMsgType.CLOSE,
                            WSMsgType.CLOSED,
                        }
                    async with state.session.ws_connect(
                        state.url + "/v1/events", headers=state.headers
                    ):
                        pass
                assert cancelled.is_set()
            finally:
                release.set()

    asyncio.run(run())


@pytest.mark.parametrize("operation", ["maintenance", "acceptance", "append", "open"])
def test_health_reports_timing_store_failure_and_recovery(
    tmp_path, monkeypatch, operation
):
    async def run():
        fail = False
        attempted = asyncio.Event()
        recovered = asyncio.Event()
        async with sqlite_service(tmp_path) as (client, session, store, ingress, _):
            resume_maintenance = asyncio.Event()

            async def health():
                async with session.get(
                    client.url + "/health", headers=client._headers
                ) as response:
                    assert response.status == 200
                    return await response.json()

            assert (await health())["healthy"] is True
            maintain = store.maintain

            async def maintain_observed():
                if fail:
                    attempted.set()
                    raise sqlite3.OperationalError("synthetic store failure")
                await maintain()
                recovered.set()

            async def maintain_paused():
                await resume_maintenance.wait()
                await maintain()
                recovered.set()

            append_device_rows = store._append_device_rows

            def append_device_rows_observed(*args):
                if fail:
                    raise sqlite3.OperationalError("synthetic store failure")
                append_device_rows(*args)

            async def send(seq):
                result = asyncio.get_running_loop().create_future()
                assert ingress.accept(packet(seq), result=result)
                return await asyncio.wait_for(result, 5)

            fail = True
            if operation == "maintenance":
                monkeypatch.setattr(store, "maintain", maintain_observed)
                await asyncio.wait_for(attempted.wait(), 5)
            else:
                monkeypatch.setattr(store, "maintain", maintain_paused)
                monkeypatch.setattr(
                    store, "_append_device_rows", append_device_rows_observed
                )
                assert not await send(0)
            body = await health()
            assert body["healthy"] is False
            assert body["unavailable"]["timing_store"] == "timing_store_failed"

            fail = False
            if operation == "acceptance":
                resume_maintenance.set()
                await asyncio.wait_for(recovered.wait(), 5)
            elif operation == "append":
                await store.append(
                    TimingEvent(
                        None, "satellite-a", "server", "unmatched", 1, "proxy", None
                    )
                )
            elif operation == "open":
                await store.open_turn(
                    TurnOpen(str(uuid7()), "satellite-a", "voice", None, None)
                )
            else:
                await asyncio.wait_for(recovered.wait(), 5)
            body = await health()
            assert body["healthy"] is True
            assert "timing_store" not in body.get("unavailable", {})

    asyncio.run(run())


def test_health_expires_when_maintenance_stalls(tmp_path, monkeypatch):
    async def run():
        entered = asyncio.Event()
        release = asyncio.Event()
        original = storage.SQLiteStore.maintain

        async def stalled(store):
            entered.set()
            await release.wait()
            await original(store)

        monkeypatch.setattr(storage.SQLiteStore, "maintain", stalled)
        monkeypatch.setattr(app, "STORE_READY_SECONDS", 0.05)
        async with sqlite_service(tmp_path) as (client, session, _, _, _):
            await asyncio.wait_for(entered.wait(), 5)
            await asyncio.sleep(0.06)
            async with session.get(
                client.url + "/health", headers=client._headers
            ) as response:
                assert response.status == 200
                body = await response.json()
                assert body["healthy"] is False
                assert body["unavailable"]["timing_store"] == "timing_store_failed"

    asyncio.run(run())


@pytest.mark.parametrize("budget", ["bytes", "time"])
def test_startup_download_budget(tmp_path, monkeypatch, budget):
    async def run():
        chunks = [b"synthetic asset"] * (10 if budget == "time" else 1)
        data = b"".join(chunks)
        sent_all = asyncio.Event()

        async def asset(request):
            if budget == "time":
                reply = web.StreamResponse()
                reply.content_length = len(data)
                await reply.prepare(request)
                with suppress(ConnectionError):
                    for chunk in chunks:
                        await reply.write(chunk)
                        await asyncio.sleep(0.02)
                    sent_all.set()
                return reply
            return web.Response(body=data)

        application = web.Application()
        application.router.add_get("/asset", asset)
        runner = web.AppRunner(application, access_log=None)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        url = f"http://127.0.0.1:{runner.addresses[0][1]}/asset"
        pin = "a" * 40
        record = {
            "id": "asset",
            "kind": "model",
            "status": "included",
            "download": "startup",
            "path": "models/asset.onnx",
            "paths": ["models/asset.onnx"],
            "revision": pin,
            "url": f"https://example.com/{pin}/asset",
            "sha256": hashlib.sha256(data).hexdigest(),
            "components": [],
            "bytes": len(data) - 1 if budget == "bytes" else len(data),
        }
        document = {
            "assets": [record],
            "download_plan": [
                {key: record[key] for key in ("id", "path", "sha256", "url", "bytes")}
            ],
        }
        records = startup.download_records
        monkeypatch.setattr(
            startup,
            "download_records",
            lambda doc: [{**entry, "url": url} for entry in records(doc)],
        )
        monkeypatch.setattr(startup, "DOWNLOAD_DEADLINE", 0.05, raising=False)
        try:
            with pytest.raises(
                ValueError, match=f"model_download_{budget}_limit: asset"
            ):
                await asyncio.to_thread(
                    startup.fetch_plan, tmp_path, document, download=True
                )
            assert not sent_all.is_set()
            assert list((tmp_path / "models").iterdir()) == []
        finally:
            await runner.cleanup()

    asyncio.run(run())


def test_event_subscribers_cannot_lose_notification(tmp_path, monkeypatch):
    async def run():
        store = storage.SQLiteStore(tmp_path / "turns.db")
        empty = asyncio.Event()
        release = asyncio.Event()
        call = store._call

        async def delayed(function, *args):
            result = await call(function, *args)
            if function == store._next_event and not empty.is_set():
                assert result is None
                empty.set()
                await release.wait()
            return result

        monkeypatch.setattr(store, "_call", delayed)
        first = store.events("satellite-a", 0)
        second = store.events("satellite-a", 0)
        waiting = asyncio.create_task(anext(first))
        try:
            await asyncio.wait_for(empty.wait(), 5)
            await store.append(
                TimingEvent(
                    None, "satellite-a", "server", "unmatched", 1, "proxy", None
                )
            )
            other = await asyncio.wait_for(anext(second), 5)
            release.set()
            assert await asyncio.wait_for(waiting, 1) == other
        finally:
            waiting.cancel()
            await asyncio.gather(waiting, return_exceptions=True)
            await first.aclose()
            await second.aclose()
            store.close()

    asyncio.run(run())


@pytest.mark.parametrize("limit", ["count", "size"])
def test_pending_overflow_does_not_rollback_open(tmp_path, monkeypatch, limit):
    async def run():
        store = storage.SQLiteStore(tmp_path / "turns.db", now_ns=lambda: 3)
        turn = TurnOpen(str(uuid7()), "satellite-a", "voice", None, None)
        try:
            for stamp in (1, 2, 3):
                pending = TimingEvent(
                    None, "satellite-a", "device", "wake_detected", stamp, "proxy", 1
                )
                await store.append_device(pending, stamp)
            if limit == "count":
                monkeypatch.setattr(storage, "MAX_TURN_EVENTS", 2)
            else:
                from dataclasses import replace

                size = len(
                    store._encode(replace(pending, turn_id=turn.turn_id)).encode()
                )
                monkeypatch.setattr(storage, "MAX_TURN_EVENT_BYTES", 2 * size)
            await store.open_turn(turn)
            record = await store.get_turn(turn.turn_id)
            assert record["status"] == "open"
            assert [event["t_utc_ns"] for event in record["events"]] == [1, 2]
            rows = [row["event"] async for row in store.export(turn.satellite, 0)]
            assert [(row["turn_id"], row["stage"]) for row in rows] == [
                (turn.turn_id, "wake_detected"),
                (turn.turn_id, "wake_detected"),
                (None, "unmatched"),
            ]
        finally:
            store.close()

    asyncio.run(run())


@pytest.mark.parametrize("payload", ["null", "42"])
def test_non_object_turn_payload_is_corrupt(tmp_path, payload):
    async def run():
        store = storage.SQLiteStore(tmp_path / "turns.db")
        turn = TurnOpen(str(uuid7()), "satellite-a", "voice", None, None)

        def corrupt():
            with store.connection:
                store.connection.execute(
                    "UPDATE turns SET payload=?, status='closing' WHERE turn_id=?",
                    (payload, turn.turn_id),
                )

        try:
            await store.open_turn(turn)
            await store._call(corrupt)
            await store.maintain()
            assert await store.get_turn(turn.turn_id) is None
            assert store.invalid_rows[turn.satellite, "turn"] > 0
        finally:
            store.close()

    asyncio.run(run())


def test_event_client_reports_a_websocket_close(service, turn_id, monkeypatch):
    async def run():
        async with service() as (client, state):
            await client.open_turn(
                TurnOpen(turn_id, "satellite-a", "voice", None, None)
            )
            await client.event(mic_event(turn_id))

            async def finite(*args):
                yield state.store.rows[0]

            monkeypatch.setattr(state.store, "events", finite)
            stream = client.events("satellite-a")
            try:
                assert (await asyncio.wait_for(anext(stream), 2))["event_id"] == 1
                with pytest.raises(ProtocolError) as error:
                    await asyncio.wait_for(anext(stream), 2)
                assert error.value.kind == "closed"
            finally:
                await stream.aclose()

    asyncio.run(run())


def test_late_open_uses_one_covering_range_seek(tmp_path):
    async def run():
        store = storage.SQLiteStore(tmp_path / "turns.db")

        def query_plan():
            return [
                row[3]
                for row in store.connection.execute(
                    "EXPLAIN QUERY PLAN SELECT 1 FROM turns "
                    "WHERE satellite=? AND opened_ns>? LIMIT 1",
                    ("satellite-a", 0),
                )
            ]

        try:
            assert await store._call(query_plan) == [
                "SEARCH turns USING COVERING INDEX turns_satellite_opened "
                "(satellite=? AND opened_ns>?)"
            ]
        finally:
            store.close()

    asyncio.run(run())

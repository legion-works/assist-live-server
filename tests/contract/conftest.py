import asyncio
from contextlib import asynccontextmanager
from dataclasses import asdict
from types import SimpleNamespace
from uuid import uuid7

import pytest


@pytest.fixture
def turn_id():
    return str(uuid7())


@pytest.fixture
def replay_store():
    from copy import deepcopy

    class ReplayMemory:
        def __init__(self):
            self.rows = {}

        def load_replay(self, device):
            return deepcopy(self.rows.get(device))

        def save_replay(self, device, state):
            self.rows[device] = deepcopy(state)

        def clear_replay(self, device):
            self.rows.pop(device, None)

    return ReplayMemory()


class MemoryStore:
    def __init__(self):
        self.turns = {}
        self.rows = []
        self.changed = asyncio.Event()

    async def open_turn(self, turn):
        self.turns[turn.turn_id] = asdict(turn)

    async def append(self, event):
        self.rows.append(
            {"version": 1, "event_id": len(self.rows) + 1, "event": asdict(event)}
        )
        self.changed.set()

    async def get_turn(self, turn_id):
        return self.turns.get(turn_id)

    async def export(self, satellite, since_utc_ns):
        for row in self.rows:
            if (
                row["event"]["satellite"] == satellite
                and row["event"]["t_utc_ns"] >= since_utc_ns
            ):
                yield row

    async def events(self, satellite, after_event_id):
        while True:
            self.changed.clear()
            for row in self.rows:
                if row["event_id"] > after_event_id:
                    after_event_id = row["event_id"]
                    if row["event"]["satellite"] == satellite:
                        yield row
            await self.changed.wait()


@pytest.fixture
def store():
    return MemoryStore()


@pytest.fixture
def service(tmp_path):
    @asynccontextmanager
    async def start(*, record_transcripts=False, block_backend=False):
        from aiohttp import ClientSession, UnixConnector, web
        from assist_live_client import AssistLiveClient
        from assist_live_client.protocol import encode_control
        from assist_live_server.app import create_app
        from assist_live_server.auth import Registry
        from assist_live_server.protocol import TimingIngress

        state = SimpleNamespace(
            audio=b"", store=MemoryStore(), backend_closed=asyncio.Event()
        )

        class Backend:
            async def stt(self, turn_id, audio):
                try:
                    async for chunk in audio:
                        state.audio += chunk
                    if block_backend:
                        await asyncio.Event().wait()
                    yield encode_control("final", turn_id, text="test transcript")
                    yield encode_control(
                        "speaker_result",
                        turn_id,
                        name="test-speaker",
                        distance=0.25,
                        destination="local",
                    )
                finally:
                    state.backend_closed.set()

            async def tts(self, turn_id, text, voice):
                assert voice == "test-voice"
                assert [chunk async for chunk in text] == ["test text"]
                yield b"\x01\x00"
                yield encode_control("sentence_end", turn_id, sentence=0)

        registry = Registry()
        registry.bind("satellite-a", "satellite-a", b"test-device-secret")
        registry.add_token("integration-test-token", ["satellite-a"])
        state.registry = registry
        state.ingress = TimingIngress(registry, queue_size=2)
        runner = web.AppRunner(
            create_app(
                registry,
                state.store,
                Backend(),
                record_transcripts=record_transcripts,
                ingress=state.ingress,
            ),
            access_log=None,
        )
        await runner.setup()
        path = tmp_path / "service.sock"
        await web.UnixSite(runner, path).start()
        state.url = "http://service.invalid"
        state.headers = {"Authorization": "Bearer integration-test-token"}
        try:
            async with ClientSession(connector=UnixConnector(path=path)) as session:
                state.session = session
                async with AssistLiveClient(
                    state.url, "integration-test-token", session=session
                ) as client:
                    yield client, state
        finally:
            await runner.cleanup()

    return start


@pytest.fixture
def silent_service(tmp_path):
    @asynccontextmanager
    async def start():
        from aiohttp import ClientSession, UnixConnector, web
        from assist_live_client import AssistLiveClient
        from assist_live_client.protocol import decode_control, encode_control

        async def silent(request):
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            negotiation = decode_control((await ws.receive()).data)
            await ws.send_json(
                encode_control(
                    "negotiated", negotiation["turn_id"], capabilities=["stt"]
                )
            )
            async for _ in ws:
                pass
            return ws

        app = web.Application()
        app.router.add_get("/v1/audio", silent)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        path = tmp_path / "silent.sock"
        await web.UnixSite(runner, path).start()
        try:
            async with ClientSession(connector=UnixConnector(path=path)) as session:
                async with AssistLiveClient(
                    "http://service.invalid", "test-token", session=session
                ) as client:
                    yield client
        finally:
            await runner.cleanup()

    return start

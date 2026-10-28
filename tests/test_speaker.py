import asyncio
import socket
from contextlib import asynccontextmanager

import pytest
from aiohttp import web


@pytest.fixture(autouse=True)
def loopback_only(monkeypatch):
    connect = socket.socket.connect

    def guarded(sock, address):
        if sock.family in {socket.AF_INET, socket.AF_INET6}:
            assert address[0] in {"127.0.0.1", "::1"}, "external connection forbidden"
        return connect(sock, address)

    monkeypatch.setattr(socket.socket, "connect", guarded)


@asynccontextmanager
async def fake_speaker(handler):
    app = web.Application()
    app.router.add_post("/api/speakers/verify", handler)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    try:
        yield port
    finally:
        await runner.cleanup()


def test_dns_change_between_config_and_connection_sends_no_audio():
    from assist_live_server.config import ServerConfig
    from assist_live_server.speaker import MurdockSpeaker

    calls = []

    async def resolve(host, port):
        calls.append(host)
        return ("127.0.0.1",) if len(calls) == 1 else ("192.0.2.1",)

    async def forbidden(request):
        raise AssertionError("audio sent after DNS changed")

    async def run():
        async with fake_speaker(forbidden) as port:
            config = await ServerConfig.load(
                speaker_url=f"http://speaker.invalid:{port}/api/speakers/verify",
                local_only=True,
                resolver=resolve,
            )
            speaker = MurdockSpeaker(
                config.speaker_url, local_only=True, resolver=resolve
            )
            with pytest.raises(ValueError, match="local only"):
                await speaker.identify(b"\x01\x00")
            assert calls == ["speaker.invalid", "speaker.invalid"]

    asyncio.run(run())


def test_concurrent_speaker_destinations_stay_with_each_result():
    from uuid import uuid7

    from assist_live_server.audio_gateway import AudioGateway
    from assist_live_server.providers.parakeet import ParakeetSession
    from assist_live_server.speaker import SpeakerResult
    from assist_live_server.turn.detector import TurnDetector

    async def run():
        first_ready = asyncio.Event()
        second_ready = asyncio.Event()

        class SharedSpeaker:
            destination = "local"

            async def identify(self, audio):
                if audio[0] == 1:
                    first_ready.set()
                    await second_ready.wait()
                    return SpeakerResult("speaker-a", 0.2, "local")
                await first_ready.wait()
                self.destination = "cloud"
                second_ready.set()
                return SpeakerResult("speaker-b", 0.2, "cloud")

        gateway = AudioGateway(
            lambda _: ParakeetSession(lambda _: "unchanged final"),
            speaker=SharedSpeaker(),
            local_only=False,
            detector_factory=lambda: TurnDetector(
                vad_probability=lambda _: 1, turn_probability=lambda _: 0
            ),
        )

        async def turn(value):
            async def frames():
                yield bytes([value, 0]) * 512

            return [
                item
                async for item in gateway.stt(str(uuid7()), frames())
                if item["type"] == "speaker_result"
            ]

        local, cloud = await asyncio.gather(turn(1), turn(2))
        assert len(local) == len(cloud) == 1
        assert local[0]["destination"] == "local"
        assert cloud[0]["destination"] == "cloud"

    asyncio.run(asyncio.wait_for(run(), 5))


def test_checked_address_is_pinned_with_original_host_and_no_audio_file(
    tmp_path, monkeypatch
):
    import io
    import wave

    import aiohttp
    from assist_live_server.speaker import MurdockSpeaker, SpeakerResult

    monkeypatch.chdir(tmp_path)
    request = aiohttp.ClientSession._request

    async def checked_request(self, method, url, **kwargs):
        assert kwargs.get("server_hostname") == "speaker.invalid"
        return await request(self, method, url, **kwargs)

    monkeypatch.setattr(aiohttp.ClientSession, "_request", checked_request)

    received = []
    resolutions = []

    async def resolve(host, port):
        resolutions.append(host)
        return ("127.0.0.1",) if len(resolutions) == 1 else ("192.0.2.1",)

    async def identify(request):
        field = await (await request.multipart()).next()
        assert field.name == "audio"
        assert field.headers["Content-Type"] == "audio/wav"
        raw = await field.read()
        with wave.open(io.BytesIO(raw)) as wav:
            assert (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) == (
                16000,
                1,
                2,
            )
            received.append((request.host, wav.readframes(wav.getnframes())))
        return web.json_response(
            {
                "is_match": True,
                "matched_speaker": "speaker-a",
                "distance": 0.2,
                "threshold": 0.3,
                "all_distances": {"speaker-a": 0.2},
            }
        )

    async def run():
        async with fake_speaker(identify) as port:
            speaker = MurdockSpeaker(
                f"http://speaker.invalid:{port}/api/speakers/verify",
                local_only=True,
                resolver=resolve,
            )
            assert speaker.checked_destination is None
            assert await speaker.identify(b"\x01\x00") == SpeakerResult(
                "speaker-a", 0.2
            )
            assert speaker.checked_destination is None
            assert received == [(f"speaker.invalid:{port}", b"\x01\x00")]
            assert resolutions == ["speaker.invalid"]
            assert list(tmp_path.iterdir()) == []
            assert not any(
                isinstance(v, (bytes, bytearray)) for v in vars(speaker).values()
            )

    asyncio.run(run())


@pytest.mark.parametrize(
    "name,reason",
    [
        ("", "empty"),
        ("speaker\nname", "control"),
        ("speaker\tname", "control"),
        ("speaker\x85name", "control"),
        ("x" * 65, "bound"),
        (" speaker-a", "whitespace"),
        ("speaker-a ", "whitespace"),
    ],
)
def test_invalid_speaker_name_is_dropped_and_counted(name, reason):
    from assist_live_client import TimingEvent
    from assist_live_server.store import SPEAKER_NAME_DROPS, safe_data

    before = SPEAKER_NAME_DROPS[reason]
    event = TimingEvent(
        None,
        "satellite-a",
        "server",
        "speaker_result",
        1,
        "measured",
        None,
        data={"name": name},
    )
    assert safe_data(event) == {}
    assert SPEAKER_NAME_DROPS[reason] == before + 1


def test_speaker_display_name_normalized_only_in_its_stage_and_key():
    from dataclasses import replace

    from assist_live_client import TimingEvent
    from assist_live_server.store import safe_data

    event = TimingEvent(
        None,
        "satellite-a",
        "server",
        "speaker_result",
        1,
        "measured",
        None,
        data={"name": "Spe\u0301aker A"},
    )
    assert safe_data(event) == {"name": "Spéaker A"}
    assert safe_data(replace(event, stage="stt_final")) == {}
    assert (
        safe_data(replace(event, stage="stt_final", data={"name": "speaker-a"})) == {}
    )
    assert safe_data(replace(event, data={"provider": "Spéaker A"})) == {}
    assert safe_data(replace(event, data={"name": "x" * 64})) == {"name": "x" * 64}
    assert safe_data(replace(event, data={"name": "\U00010400" * 64})) == {
        "name": "\U00010400" * 64
    }


@pytest.mark.parametrize(
    "server_opt,turn_opt",
    [(None, True), (False, False), (False, True), (True, False), (True, True)],
)
@pytest.mark.parametrize("is_match,distance", [(True, 0.2), (False, 2.0), (False, 1.4)])
def test_real_speaker_reaches_client_and_record_without_transcript_change(
    tmp_path, server_opt, turn_opt, caplog, is_match, distance
):
    import logging
    from uuid import uuid7

    from aiohttp import ClientSession, UnixConnector
    from assist_live_client import AssistLiveClient, TurnOpen
    from assist_live_server.app import create_app
    from assist_live_server.audio_gateway import AudioGateway
    from assist_live_server.auth import Registry
    from assist_live_server.providers.parakeet import ParakeetSession
    from assist_live_server.speaker import MurdockSpeaker
    from assist_live_server.store import SQLiteStore
    from assist_live_server.turn.detector import TurnDetector

    audio = b"\x01\x00" * 512
    caplog.set_level(logging.DEBUG)

    async def identify(request):
        assert (await (await request.multipart()).next()).name == "audio"
        return web.json_response(
            {
                "is_match": is_match,
                "matched_speaker": "Spéaker A" if is_match else None,
                "distance": distance,
                "threshold": 0.3,
                "all_distances": {"Spéaker A": 0.2},
            }
        )

    async def run():
        store = SQLiteStore(tmp_path / "turns.db")
        registry = Registry()
        registry.bind("satellite-a", "satellite-a", b"synthetic-key")
        registry.add_token("synthetic-token", ["satellite-a"])
        async with fake_speaker(identify) as port:
            speaker = MurdockSpeaker(f"http://127.0.0.1:{port}/api/speakers/verify")
            gateway = AudioGateway(
                lambda _: ParakeetSession(lambda _: "unchanged final"),
                speaker=speaker,
                destination="local",
                detector_factory=lambda: TurnDetector(
                    vad_probability=lambda _: 1, turn_probability=lambda _: 0
                ),
            )
            options = {} if server_opt is None else {"record_transcripts": server_opt}
            runner = web.AppRunner(
                create_app(registry, store, audio_gateway=gateway, **options),
                access_log=None,
            )
            await runner.setup()
            path = tmp_path / "audio.sock"
            await web.UnixSite(runner, path).start()
            try:
                async with ClientSession(connector=UnixConnector(path=path)) as session:
                    client = AssistLiveClient(
                        "http://service.invalid", "synthetic-token", session=session
                    )
                    turn = TurnOpen(
                        str(uuid7()), "satellite-a", "voice", None, None, turn_opt
                    )
                    await client.open_turn(turn)

                    async def frames():
                        yield audio

                    replies = [
                        reply async for reply in client.stt(turn.turn_id, frames())
                    ]
                    assert (
                        next(r["text"] for r in replies if r["type"] == "final")
                        == "unchanged final"
                    )
                    result = next(
                        (r for r in replies if r["type"] == "speaker_result"), None
                    )
                    assert result is not None
                    assert (
                        result["name"],
                        result["distance"],
                        result["destination"],
                    ) == ("Spéaker A" if is_match else "", distance, "local")
                    record = await store.get_turn(turn.turn_id)
                    stored = next(
                        e["data"]
                        for e in record["events"]
                        if e["stage"] == "speaker_result"
                    )
                    assert stored.get("name", "") == ("Spéaker A" if is_match else "")
                    assert stored.get("distance") == distance
                    final = next(
                        e["data"] for e in record["events"] if e["stage"] == "stt_final"
                    )
                    assert final["destination"] == "local"
                    assert final.get("transcript") == (
                        "unchanged final" if server_opt and turn_opt else None
                    )
                    assert all(
                        not ({"audio", "pcm"} & e["data"].keys())
                        for e in record["events"]
                    )
                    async with session.get(
                        "http://service.invalid/metrics",
                        headers={"Authorization": "Bearer synthetic-token"},
                    ) as response:
                        assert response.status == 200
                        metrics = await response.text()
                        assert "Spéaker A" not in metrics
                        assert "speaker_name_dropped_empty_total" in metrics
            finally:
                await runner.cleanup()
                store.close()
        assert audio not in (tmp_path / "turns.db").read_bytes()

    asyncio.run(asyncio.wait_for(run(), 10))
    assert "Spéaker A" not in caplog.text


def test_redirect_is_not_followed():
    from assist_live_server.speaker import MurdockSpeaker

    calls = []

    async def resolve(host, port):
        calls.append(host)
        if host != "speaker.invalid":
            raise AssertionError("redirect resolved")
        return ("127.0.0.1",)

    async def redirect(request):
        return web.Response(
            status=307, headers={"Location": "http://192.0.2.1/identify"}
        )

    async def run():
        async with fake_speaker(redirect) as port:
            speaker = MurdockSpeaker(
                f"http://speaker.invalid:{port}/api/speakers/verify",
                local_only=True,
                resolver=resolve,
            )
            with pytest.raises(ValueError, match="speaker request refused"):
                await speaker.identify(b"\x00\x00")
            assert calls == ["speaker.invalid"]

    asyncio.run(run())


def test_mixed_dns_answers_report_the_connected_address_class():
    from assist_live_server.speaker import MurdockSpeaker

    async def resolve(host, port):
        return ("127.0.0.1", "192.0.2.1")

    async def identify(request):
        await request.read()
        return web.json_response(
            {"is_match": True, "matched_speaker": "speaker-a", "distance": 0.2}
        )

    async def run():
        async with fake_speaker(identify) as port:
            speaker = MurdockSpeaker(
                f"http://speaker.invalid:{port}/api/speakers/verify",
                local_only=False,
                mode="cloud",
                resolver=resolve,
            )
            result = await speaker.identify(b"\x01\x00")
            assert result.name == "speaker-a"
            assert getattr(result, "destination", None) == "local"
            assert speaker.destination == "cloud"
            assert speaker.checked_destination is None

    asyncio.run(run())


@pytest.mark.parametrize("distance", [2.0001, -0.01])
def test_invalid_cosine_distance_fails_by_name(distance):
    from uuid import uuid7

    from assist_live_client.models import TimingEvent
    from assist_live_server.speaker import MurdockSpeaker
    from assist_live_server.store import safe_data

    async def identify(request):
        await request.read()
        return web.json_response(
            {"is_match": False, "matched_speaker": None, "distance": distance}
        )

    async def run():
        async with fake_speaker(identify) as port:
            with pytest.raises(ValueError, match="^invalid speaker result$"):
                await MurdockSpeaker(
                    f"http://127.0.0.1:{port}/api/speakers/verify"
                ).identify(b"\x01\x00")

    asyncio.run(run())
    event = TimingEvent(
        str(uuid7()),
        "satellite-a",
        "server",
        "speaker_result",
        1,
        "measured",
        None,
        data={"distance": distance},
    )
    assert "distance" not in safe_data(event)


@pytest.mark.parametrize("missing", ["matched_speaker", "distance"])
def test_malformed_speaker_response_raises_value_error(missing):
    from assist_live_server.speaker import MurdockSpeaker

    async def identify(request):
        await request.read()
        value = {"is_match": True, "matched_speaker": "speaker-a", "distance": 0.2}
        del value[missing]
        return web.json_response(value)

    async def run():
        async with fake_speaker(identify) as port:
            with pytest.raises(ValueError, match="^invalid speaker result$"):
                await MurdockSpeaker(
                    f"http://127.0.0.1:{port}/api/speakers/verify"
                ).identify(b"\x01\x00")

    asyncio.run(run())


def test_transcript_defaults_are_false(tmp_path):
    from assist_live_server.config import ServerConfig
    from assist_live_server.store import SQLiteStore

    assert ServerConfig().record_transcripts is False
    store = SQLiteStore(tmp_path / "defaults.db")
    try:
        assert store.record_transcripts is False
    finally:
        store.close()


@pytest.mark.parametrize(
    "server_opt,turn_opt", [(False, False), (False, True), (True, False), (True, True)]
)
def test_configured_transcript_switch_controls_store(tmp_path, server_opt, turn_opt):
    from uuid import uuid7

    from aiohttp import ClientSession, UnixConnector
    from assist_live_client import AssistLiveClient, TimingEvent, TurnOpen
    from assist_live_server.app import create_app
    from assist_live_server.auth import Registry
    from assist_live_server.config import ProviderFactory, ServerConfig
    from assist_live_server.store import SQLiteStore

    async def run():
        store = SQLiteStore(tmp_path / "switch.db", record_transcripts=not server_opt)
        registry = Registry()
        registry.bind("satellite-a", "satellite-a", b"synthetic-key")
        registry.add_token("test-token", ["satellite-a"])
        config = ServerConfig(
            stt_provider=None, tts_provider=None, record_transcripts=server_opt
        )
        providers = await ProviderFactory.load(config)
        runner = web.AppRunner(
            create_app(registry, store, providers=providers), access_log=None
        )
        await runner.setup()
        path = tmp_path / "transcripts.sock"
        await web.UnixSite(runner, path).start()
        try:
            async with ClientSession(connector=UnixConnector(path=path)) as session:
                client = AssistLiveClient(
                    "http://service.invalid", "test-token", session=session
                )
                turn = TurnOpen(
                    str(uuid7()), "satellite-a", "voice", None, None, turn_opt
                )
                await client.open_turn(turn)
                await client.event(
                    TimingEvent(
                        turn.turn_id,
                        turn.satellite,
                        "integration",
                        "stt_final",
                        1,
                        "measured",
                        None,
                        data={"transcript": "synthetic final"},
                    )
                )
                record = await store.get_turn(turn.turn_id)
                assert record["events"][0]["data"].get("transcript") == (
                    "synthetic final" if server_opt and turn_opt else None
                )
                assert store.record_transcripts is server_opt
        finally:
            await runner.cleanup()
            store.close()

    asyncio.run(run())

import asyncio
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid7

import pytest
import yaml
from aiohttp.test_utils import TestClient, TestServer
from assist_live_client import AssistLiveClient, TurnOpen
from assist_live_server.__main__ import main
from assist_live_server.app import create_app
from assist_live_server.auth import Registry
from assist_live_server.config import ProviderFactory, ServerConfig
from assist_live_server.providers import stt
from assist_live_server.providers.parakeet import ParakeetSession
from assist_live_server.store import SQLiteStore
from assist_live_server.turn import detector
from assist_live_server.turn.profiles import PROFILES, Profile


@pytest.mark.parametrize("end_rule", ["partial", "checkpoint"])
@pytest.mark.parametrize("option", [None, "fast"], ids=["default", "fast"])
def test_turn_profile_reaches_each_turn_and_authenticated_health(
    tmp_path, monkeypatch, end_rule, option
):
    name = option or "safe"
    monkeypatch.setitem(PROFILES, "fast", Profile(200, 0.6, 1000))
    expected = PROFILES[name]
    seen = []
    init = detector.TurnDetector.__init__

    def track_profile(self, *args, **kwargs):
        init(
            self,
            *args,
            vad_probability=lambda _: 0,
            turn_probability=lambda _: 0,
            **kwargs,
        )
        seen.append(self)

    async def load_provider(_):
        return SimpleNamespace(session=lambda: ParakeetSession(lambda _: ""))

    monkeypatch.setattr(detector.TurnDetector, "__init__", track_profile)
    monkeypatch.setattr(detector, "turn_models_unavailable", lambda _: None)
    monkeypatch.setattr(stt, "provider_factory", lambda _: load_provider)

    async def run():
        settings = {"turn_profile": option} if option is not None else {}
        config = await ServerConfig.load(
            tts_provider=None, end_rule=end_rule, **settings
        )
        providers = await ProviderFactory.load(config, cache=tmp_path)
        registry = Registry()
        registry.bind("satellite-a", "satellite-a", b"synthetic-device-secret")
        registry.add_token("test-token", ["satellite-a"])
        store = SQLiteStore(tmp_path / "turns.sqlite3")
        try:
            app = create_app(registry, store, providers=providers)
            async with TestClient(TestServer(app)) as http:
                url = str(http.make_url("/")).rstrip("/")
                client = AssistLiveClient(url, "test-token", session=http.session)

                async def audio():
                    yield bytes(1024)

                for _ in range(2):
                    turn = TurnOpen(str(uuid7()), "satellite-a", "voice", None, None)
                    await client.open_turn(turn)
                    replies = [
                        reply async for reply in client.stt(turn.turn_id, audio())
                    ]
                    assert any(reply["type"] == "final" for reply in replies)

                assert len(seen) == 2 and seen[0] is not seen[1]
                for instance in seen:
                    assert instance.profile.min_silence_ms == expected.min_silence_ms
                    assert (
                        instance.profile.smart_turn_limit == expected.smart_turn_limit
                    )
                    assert instance.profile.max_silence_ms == expected.max_silence_ms

                async with http.get("/health") as response:
                    assert response.status == 401
                async with http.get(
                    "/health", headers={"Authorization": "Bearer test-token"}
                ) as response:
                    assert response.status == 200
                    assert (await response.json())["turn_profile"] == name
        finally:
            store.close()

    asyncio.run(run())


def test_invalid_turn_profile_stops_startup(tmp_path, monkeypatch, capsys):
    data = tmp_path / "data"
    monkeypatch.setenv("ASSIST_LIVE_DATA", str(data))
    monkeypatch.setenv("ASSIST_LIVE_STT_PROVIDER", "none")
    monkeypatch.setenv("ASSIST_LIVE_TTS_PROVIDER", "none")
    monkeypatch.setenv("ASSIST_LIVE_END_RULE", "partial")
    monkeypatch.setenv("ASSIST_LIVE_PORT", "not-a-port")
    monkeypatch.setenv("ASSIST_LIVE_TURN_PROFILE", "unknown")
    assert main([]) == 1
    assert "invalid ASSIST_LIVE_TURN_PROFILE" in capsys.readouterr().out
    assert not data.exists()


def test_app_exposes_and_maps_turn_profile(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    definition = yaml.safe_load((root / "assist-live-server/config.yaml").read_text())
    assert definition["options"]["turn_profile"] == "safe"
    assert definition["schema"]["turn_profile"] == "list(safe|fast)"
    spec = importlib.util.spec_from_file_location(
        "app_turn_profile", root / "assist-live-server/run.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    options = tmp_path / "options.json"
    options.write_text(json.dumps({"turn_profile": "fast"}))
    calls = []
    monkeypatch.setattr(module.os, "execvpe", lambda *args: calls.append(args))
    module.main(options)
    assert calls[0][2]["ASSIST_LIVE_TURN_PROFILE"] == "fast"


def test_gateway_refuses_unknown_turn_profile_at_construction():
    from assist_live_server.audio_gateway import AudioGateway

    with pytest.raises(ValueError, match="invalid ASSIST_LIVE_TURN_PROFILE"):
        AudioGateway(lambda _: None, turn_profile="slow")

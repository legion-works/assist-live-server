import asyncio
import importlib.util
import json
from pathlib import Path

import pytest
import yaml
from assist_live_server.__main__ import main
from assist_live_server.config import ServerConfig


@pytest.mark.parametrize("value", ["partial", "checkpoint"])
def test_end_rule_config(value):
    config = asyncio.run(ServerConfig.load(end_rule=value))
    assert config.end_rule == value


def test_unknown_end_rule_names_variable_at_startup(monkeypatch, capsys):
    monkeypatch.setenv("ASSIST_LIVE_END_RULE", "unknown")
    assert main([]) == 1
    assert "ASSIST_LIVE_END_RULE" in capsys.readouterr().out


@pytest.mark.parametrize("provider", ["deepgram", "openai"])
def test_checkpoint_refuses_non_prefix_provider_at_startup(
    monkeypatch, capsys, provider
):
    monkeypatch.setenv("ASSIST_LIVE_END_RULE", "checkpoint")
    monkeypatch.setenv("ASSIST_LIVE_STT_PROVIDER", provider)
    assert main([]) == 1
    output = capsys.readouterr().out
    assert "ASSIST_LIVE_END_RULE" in output
    assert provider in output


def test_checkpoint_allows_no_stt_provider():
    assert (
        ServerConfig(stt_provider=None, end_rule="checkpoint").end_rule == "checkpoint"
    )


def test_default_end_rule_keeps_partial():
    assert ServerConfig().end_rule == "partial"


def test_app_exposes_and_maps_end_rule(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    definition = yaml.safe_load((root / "assist-live-server/config.yaml").read_text())
    assert definition["options"]["end_rule"] == "partial"
    assert definition["schema"]["end_rule"] == "list(partial|checkpoint)"
    spec = importlib.util.spec_from_file_location(
        "app_end_rule", root / "assist-live-server/run.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    options = tmp_path / "options.json"
    options.write_text(json.dumps({"end_rule": "checkpoint"}))
    calls = []
    monkeypatch.setattr(module.os, "execvpe", lambda *args: calls.append(args))
    module.main(options)
    assert calls[0][2]["ASSIST_LIVE_END_RULE"] == "checkpoint"

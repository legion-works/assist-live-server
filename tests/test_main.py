import hashlib
import importlib.util
import io
import json
import os
import select
import subprocess
import sys
from pathlib import Path
from urllib.request import Request, urlopen

import pytest
from assist_live_server.__main__ import fetch_plan
from assist_live_server.auth import Registry

ROOT = Path(__file__).resolve().parents[1]


def environment(tmp_path):
    return {
        **os.environ,
        "ASSIST_LIVE_DATA": str(tmp_path),
        "ASSIST_LIVE_STT_PROVIDER": "none",
        "ASSIST_LIVE_TTS_PROVIDER": "none",
        "ASSIST_LIVE_DOWNLOAD_MODELS": "false",
        "ASSIST_LIVE_HOST": "127.0.0.1",
        "ASSIST_LIVE_PORT": "0",
        "DEEPGRAM_API_KEY": "synthetic-password-value",
    }


def test_valid_startup_serves_health(tmp_path):
    registry = Registry(tmp_path / "registry.json")
    registry.bind("test-satellite", "test-satellite", b"x" * 32)
    registry.add_token("synthetic-health-token", ["test-satellite"])
    process = subprocess.Popen(
        [sys.executable, "-m", "assist_live_server"],
        env=environment(tmp_path),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert select.select([process.stdout], [], [], 20)[0]
        line = process.stdout.readline()
        assert line.startswith("PASS server: listening "), line
        port = int(line.split()[-1])
        request = Request(
            f"http://127.0.0.1:{port}/health",
            headers={"Authorization": "Bearer synthetic-health-token"},
        )
        with urlopen(request, timeout=5) as response:
            assert response.status == 200
        assert "synthetic-password-value" not in line
    finally:
        process.terminate()
        stdout, stderr = process.communicate(timeout=10)
    assert process.returncode == 0
    assert "synthetic-password-value" not in stdout + stderr
    assert (tmp_path / "turns.sqlite3").exists()
    assert (tmp_path / "registry.json").stat().st_mode & 0o777 == 0o600


def test_bad_configuration_exits_without_secret(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "assist_live_server"],
        env={
            **environment(tmp_path),
            "ASSIST_LIVE_STT_PROVIDER": "synthetic-password-value",
        },
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 1
    assert result.stdout == "FAIL server: startup configuration or model check failed\n"
    assert result.stderr == ""


def test_bad_cached_hash_exits_without_traceback(tmp_path):
    cache = tmp_path / "cache"
    (cache / "models").mkdir(parents=True)
    (cache / "models/voice.onnx").write_bytes(b"wrong")
    manifest = tmp_path / "manifest.json"
    record = {
        "id": "voice",
        "kind": "voice",
        "download": "startup",
        "status": "included",
        "revision": "a" * 40,
        "url": "https://example.com/" + "a" * 40 + "/voice.onnx",
        "sha256": "0" * 64,
        "paths": ["models/voice.onnx"],
        "components": [],
    }
    manifest.write_text(
        json.dumps(
            {
                "assets": [record],
                "download_plan": [
                    {
                        "id": "voice",
                        "url": record["url"],
                        "sha256": record["sha256"],
                        "path": "models/voice.onnx",
                    }
                ],
            }
        )
    )
    result = subprocess.run(
        [sys.executable, "-m", "assist_live_server", "--fetch-models"],
        env={
            **environment(tmp_path),
            "ASSIST_LIVE_ASSET_MANIFEST": str(manifest),
            "ASSIST_LIVE_MODEL_CACHE": str(cache),
        },
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 1
    assert result.stdout == "FAIL server: startup configuration or model check failed\n"
    assert result.stderr == ""


def test_app_option_mapping_keeps_password_out_of_output(tmp_path, monkeypatch, capsys):
    spec = importlib.util.spec_from_file_location("app_run", ROOT / "ha-app/run.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    options = tmp_path / "options.json"
    options.write_text(
        json.dumps(
            {
                "stt_provider": "deepgram",
                "local_only": False,
                "deepgram_api_key": "synthetic-password-value",
            }
        )
    )
    calls = []
    monkeypatch.setattr(module.os, "execvpe", lambda *args: calls.append(args))
    module.main(options)
    command, args, env = calls[0]
    assert command == "assist-live-server" and args == [command]
    assert env["DEEPGRAM_API_KEY"] == "synthetic-password-value"
    assert env["ASSIST_LIVE_STT_PROVIDER"] == "deepgram"
    assert env["ASSIST_LIVE_LOCAL_ONLY"] == "false"
    assert "synthetic-password-value" not in capsys.readouterr().out


@pytest.mark.parametrize(
    "kind,path", [("runtime", "models/file.whl"), ("voice", "../escape.onnx")]
)
def test_startup_refuses_runtime_wheels_and_path_escape(tmp_path, kind, path):
    pin = "a" * 40
    record = {
        "id": "asset",
        "kind": kind,
        "download": "startup",
        "status": "included",
        "url": f"https://example.com/{pin}/file",
        "revision": pin,
        "sha256": "0" * 64,
        "paths": [path],
        "components": [],
    }
    document = {
        "assets": [record],
        "download_plan": [
            {
                "id": "asset",
                "url": record["url"],
                "sha256": record["sha256"],
                "path": path,
            }
        ],
    }
    with pytest.raises(ValueError, match="invalid selected download plan"):
        fetch_plan(tmp_path, document, download=False)


def test_startup_download_checks_bytes_and_supplies_provider_basename(
    tmp_path, monkeypatch
):
    import assist_live_server.__main__ as startup

    data = b"synthetic model bytes"
    pin = "a" * 40
    record = {
        "id": "asset",
        "kind": "voice",
        "download": "startup",
        "status": "included",
        "url": f"https://example.com/{pin}/file.onnx",
        "revision": pin,
        "sha256": hashlib.sha256(data).hexdigest(),
        "paths": ["models/file.onnx"],
        "components": [],
    }
    document = {
        "assets": [record],
        "download_plan": [
            {
                "id": "asset",
                "url": record["url"],
                "sha256": record["sha256"],
                "path": "models/file.onnx",
            }
        ],
    }
    monkeypatch.setattr(startup, "urlopen", lambda *args, **kwargs: io.BytesIO(data))
    fetch_plan(tmp_path, document, download=True)
    assert (tmp_path / "models/file.onnx").read_bytes() == data
    assert (tmp_path / "file.onnx").samefile(tmp_path / "models/file.onnx")
    monkeypatch.setattr(
        startup, "urlopen", lambda *args, **kwargs: io.BytesIO(b"wrong")
    )
    fresh = tmp_path / "fresh"
    with pytest.raises(ValueError, match="download SHA-256 mismatch"):
        fetch_plan(fresh, document, download=True)
    assert not (fresh / "models/file.onnx").exists()
    assert list((fresh / "models").iterdir()) == []
    (tmp_path / "file.onnx").write_bytes(b"wrong hash")
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        fetch_plan(tmp_path, document, download=True)

import asyncio
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


def entrypoint_command(tmp_path, name, *args):
    report = tmp_path / f"{name}-imports.json"
    code = f"""
import atexit
import json
import runpy
import sys
from pathlib import Path
def report():
    Path({str(report)!r}).write_text(json.dumps(sorted(
        name for name in sys.modules if name.startswith("onnxruntime")
    )))
atexit.register(report)
runpy.run_module("assist_live_server", run_name="__main__")
"""
    return [sys.executable, "-c", code, *args]


# A hang guard for subprocess startup only. It is not a startup time
# requirement: under host load the interpreter and imports can take
# more than 20 s before the first line.
STARTUP_GUARD = 60


def environment(tmp_path):
    return {
        **os.environ,
        "ASSIST_LIVE_DATA": str(tmp_path),
        "ASSIST_LIVE_MODEL_CACHE": str(tmp_path / "cache"),
        "ASSIST_LIVE_STT_PROVIDER": "none",
        "ASSIST_LIVE_TTS_PROVIDER": "none",
        "ASSIST_LIVE_DOWNLOAD_MODELS": "false",
        "ASSIST_LIVE_HOST": "127.0.0.1",
        "ASSIST_LIVE_PORT": "0",
        "DEEPGRAM_API_KEY": "synthetic-password-value",
    }


@pytest.mark.parametrize("provider", ["Deepgram", "OpenAI"])
def test_startup_refuses_short_cloud_stt_credential(
    tmp_path, monkeypatch, capsys, provider
):
    from assist_live_server import store
    from assist_live_server.__main__ import main

    for name, value in environment(tmp_path).items():
        if name.startswith("ASSIST_LIVE_"):
            monkeypatch.setenv(name, value)
    monkeypatch.setenv("ASSIST_LIVE_LOCAL_ONLY", "false")
    monkeypatch.setenv("ASSIST_LIVE_STT_PROVIDER", provider.lower())
    monkeypatch.setenv(f"{provider.upper()}_API_KEY", "x" * 15)

    def unexpected_store(*args, **kwargs):
        pytest.fail("server reached store construction with a short credential")

    monkeypatch.setattr(store, "SQLiteStore", unexpected_store)
    assert main([]) == 1
    captured = capsys.readouterr()
    assert captured.out == (
        "FAIL server: startup configuration or model check failed: "
        f"{provider} STT credential must have at least 16 characters\n"
    )
    assert captured.err == ""


@pytest.mark.parametrize("provisioned", [False, True])
@pytest.mark.parametrize("turn_profile", [None, "fast"], ids=["default", "fast"])
def test_valid_startup_serves_health(tmp_path, provisioned, turn_profile):
    if provisioned:
        registry = Registry(tmp_path / "registry.json")
        registry.bind("test-satellite", "test-satellite", b"x" * 32)
        registry.add_token("synthetic-health-token", ["test-satellite"])
    env = environment(tmp_path)
    if turn_profile is None:
        env.pop("ASSIST_LIVE_TURN_PROFILE", None)
    else:
        env["ASSIST_LIVE_TURN_PROFILE"] = turn_profile
    process = subprocess.Popen(
        entrypoint_command(tmp_path, "server"),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert select.select([process.stdout], [], [], STARTUP_GUARD)[0]
        line = process.stdout.readline()
        assert line.startswith("PASS server: listening "), line
        port = int(line.split()[-1])
        health = subprocess.run(
            entrypoint_command(tmp_path, "health", "--healthcheck"),
            env={**environment(tmp_path), "ASSIST_LIVE_PORT": str(port)},
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert health.returncode == 0, health.stdout + health.stderr
        assert health.stdout == "PASS server: health checked\n"
        assert health.stderr == ""
        assert json.loads((tmp_path / "health-imports.json").read_text()) == []
        if provisioned:
            request = Request(
                f"http://127.0.0.1:{port}/health",
                headers={"Authorization": "Bearer synthetic-health-token"},
            )
            with urlopen(request, timeout=5) as response:
                assert response.status == 200
                assert json.load(response)["turn_profile"] == (turn_profile or "safe")
        assert "synthetic-password-value" not in line
    finally:
        process.terminate()
        stdout, stderr = process.communicate(timeout=10)
    assert process.returncode == 0
    assert json.loads((tmp_path / "server-imports.json").read_text()) == []
    assert "synthetic-password-value" not in stdout + stderr
    assert (tmp_path / "turns.sqlite3").exists()
    assert (tmp_path / "registry.json").stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("initial", [None, "0"])
@pytest.mark.parametrize("failure", [None, "missing", "broken"])
def test_entrypoint_disables_telemetry_before_onnxruntime_import(
    tmp_path, initial, failure
):
    provider = tmp_path / "fake_piper.py"
    provider.write_text("""
import onnxruntime
class PiperProvider:
    @classmethod
    async def load(cls, cache):
        return cls()
""")
    code = f"""
import importlib.abc
import importlib.util
import hashlib
import os
import sys
from pathlib import Path
seen = []
cache = Path(os.environ["ASSIST_LIVE_DATA"]) / "cache"
class Check(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "assist_live_server.providers.piper":
            return importlib.util.spec_from_file_location(fullname, {str(provider)!r})
        if fullname == "onnxruntime":
            assert "onnxruntime" not in sys.modules
            assert os.environ.get("ORT_DISABLE_TELEMETRY") == "1"
            assert (cache / "voice.onnx").read_bytes() == b"synthetic model bytes"
            seen.append(fullname)
            if {failure!r} == "missing":
                raise ModuleNotFoundError("synthetic missing runtime")
            if {failure!r} == "broken":
                raise OSError("synthetic broken runtime")
        return None
sys.meta_path.insert(0, Check())
import assist_live_server.__main__ as startup
assert seen == []
path = cache / "models/voice.onnx"
path.parent.mkdir(parents=True)
path.write_bytes(b"synthetic model bytes")
record = {{
    "id": "voice", "kind": "voice", "download": "startup", "status": "included",
    "revision": "a" * 40, "url": "https://example.com/" + "a" * 40 + "/voice.onnx",
    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    "paths": ["models/voice.onnx"], "components": [],
}}
startup.load_asset_manifest = lambda: {{
    "assets": [record],
    "download_plan": [{{"id": "voice", "path": "models/voice.onnx",
                        "url": record["url"], "sha256": record["sha256"],
                        "bytes": path.stat().st_size}}],
}}
status = startup.main([])
assert seen == ["onnxruntime"]
assert os.environ["ORT_DISABLE_TELEMETRY"] == "1"
assert ("onnxruntime" in sys.modules) == {failure is None!r}
print("PASS telemetry disabled before ONNX Runtime import")
raise SystemExit(status)
"""
    env = {**environment(tmp_path), "ASSIST_LIVE_TTS_PROVIDER": "piper"}
    env.pop("ORT_DISABLE_TELEMETRY", None)
    if initial is not None:
        env["ORT_DISABLE_TELEMETRY"] = initial
    process = subprocess.Popen(
        [sys.executable, "-c", code],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        line = ""
        if failure is None:
            assert select.select([process.stdout], [], [], STARTUP_GUARD)[0]
            line = process.stdout.readline()
            assert line.startswith("PASS server: listening "), line
            health = subprocess.run(
                [sys.executable, "-m", "assist_live_server", "--healthcheck"],
                env={**env, "ASSIST_LIVE_PORT": line.split()[-1]},
                capture_output=True,
                text=True,
                timeout=10,
            )
            assert health.returncode == 0, health.stdout + health.stderr
            process.terminate()
        stdout, stderr = process.communicate(timeout=20)
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=10)
    assert process.returncode == (0 if failure is None else 1), line + stdout + stderr
    expected = "PASS telemetry disabled before ONNX Runtime import\n"
    if failure is not None:
        expected = (
            "FAIL server: startup configuration or model check failed\n" + expected
        )
    assert stdout == expected
    assert "synthetic-password-value" not in line + stdout + stderr
    assert "Traceback" not in stderr and "telemetry_1ds" not in stderr
    # Hyper-V CI hosts can emit an ONNX Runtime PCI device-discovery warning.
    assert all("onnxruntime" in row for row in stderr.splitlines())


@pytest.mark.parametrize("failure", ["missing", "broken"])
def test_detector_import_defers_runtime_failure_until_construction(tmp_path, failure):
    code = f"""
import importlib.abc
import hashlib
import sys
from pathlib import Path
class Check(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "onnxruntime":
            if {failure!r} == "missing":
                raise ModuleNotFoundError("synthetic missing runtime")
            raise OSError("synthetic broken runtime")
        return None
sys.meta_path.insert(0, Check())
from assist_live_server.turn import detector
from assist_live_server.assets import TURN_MODEL_IDS, model_path, verified_model
assert detector.model_path is model_path
assert detector.verified_model is verified_model
assert "onnxruntime" not in sys.modules
cache = Path({str(tmp_path)!r})
path = cache / "model.onnx"
path.write_bytes(b"synthetic model bytes")
record = {{"id": "model", "path": path.name,
           "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}}
detector.model_records = lambda manifest: dict.fromkeys(TURN_MODEL_IDS, record)
try:
    detector.ModelRuntime(cache)
except (ModuleNotFoundError, OSError) as error:
    assert str(error) == "synthetic " + {failure!r} + " runtime"
else:
    raise AssertionError("runtime failure was hidden")
print("PASS runtime failure at construction")
"""
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=20
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == "PASS runtime failure at construction\n"
    assert result.stderr == ""


def test_health_failure_has_its_own_sanitized_line(tmp_path):
    Registry(tmp_path / "registry.json").save()
    result = subprocess.run(
        entrypoint_command(tmp_path, "health-failure", "--healthcheck"),
        env=environment(tmp_path),
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 1
    assert (
        result.stdout == "FAIL health: server unavailable or health response invalid\n"
    )
    assert result.stderr == ""
    assert json.loads((tmp_path / "health-failure-imports.json").read_text()) == []


def test_default_cache_reaches_turn_detector_environment(tmp_path, monkeypatch):
    import assist_live_server.__main__ as startup
    from assist_live_server.config import ProviderFactory

    monkeypatch.setenv("ASSIST_LIVE_DATA", str(tmp_path))
    monkeypatch.setenv("ASSIST_LIVE_STT_PROVIDER", "none")
    monkeypatch.setenv("ASSIST_LIVE_TTS_PROVIDER", "none")
    monkeypatch.setenv("ASSIST_LIVE_MODEL_CACHE", "")
    monkeypatch.delenv("ASSIST_LIVE_MODEL_CACHE")

    async def load(config, *, cache):
        resolved_cache = os.environ.get("ASSIST_LIVE_MODEL_CACHE")
        assert resolved_cache == str(cache) == str(tmp_path / "cache")
        raise RuntimeError("stop before app setup")

    monkeypatch.setattr(ProviderFactory, "load", load)
    with pytest.raises(RuntimeError, match="stop before app setup"):
        asyncio.run(startup.serve())


@pytest.mark.parametrize(
    "stt,tts,only",
    [
        ("deepgram", "none", ("silero-vad", "smart-turn-v3.2-cpu")),
        ("openai", "elevenlabs", ("silero-vad", "smart-turn-v3.2-cpu")),
        ("parakeet", "none", None),
        ("deepgram", "piper", None),
        ("none", "none", "skipped"),
    ],
)
def test_startup_prefetches_turn_models_for_each_stt_provider(
    tmp_path, monkeypatch, stt, tts, only
):
    import assist_live_server.__main__ as startup
    from assist_live_server.config import ProviderFactory

    monkeypatch.setenv("ASSIST_LIVE_DATA", str(tmp_path))
    monkeypatch.setenv("ASSIST_LIVE_MODEL_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("ASSIST_LIVE_STT_PROVIDER", stt)
    monkeypatch.setenv("ASSIST_LIVE_TTS_PROVIDER", tts)
    monkeypatch.setenv("ASSIST_LIVE_LOCAL_ONLY", "false")
    monkeypatch.setenv("ASSIST_LIVE_DOWNLOAD_MODELS", "false")
    calls = []

    def fetch(cache, document, *, download, only=None, basename_aliases=True):
        calls.append((download, only, basename_aliases))

    async def load(config, *, cache):
        raise RuntimeError("stop before app setup")

    monkeypatch.setattr(startup, "fetch_plan", fetch)
    monkeypatch.setattr(ProviderFactory, "load", load)
    with pytest.raises(RuntimeError, match="stop before app setup"):
        asyncio.run(startup.serve())
    # The scoped fetch makes no basename aliases; the full plan keeps them.
    assert calls == ([] if only == "skipped" else [(False, only, only is None)])


def turn_entries():
    from assist_live_server.assets import TURN_MODEL_IDS, load_asset_manifest

    selected = [
        entry
        for entry in load_asset_manifest()["download_plan"]
        if entry["id"] in TURN_MODEL_IDS
    ]
    assert sorted(entry["id"] for entry in selected) == sorted(TURN_MODEL_IDS)
    return selected


def test_changed_turn_models_do_not_stop_cloud_startup(tmp_path, monkeypatch):
    import assist_live_server.__main__ as startup
    from assist_live_server.config import ProviderFactory

    cache = tmp_path / "cache"
    for entry in turn_entries():
        path = cache / entry["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"changed model")
    monkeypatch.setenv("ASSIST_LIVE_DATA", str(tmp_path))
    monkeypatch.setenv("ASSIST_LIVE_MODEL_CACHE", str(cache))
    monkeypatch.setenv("ASSIST_LIVE_STT_PROVIDER", "deepgram")
    monkeypatch.setenv("ASSIST_LIVE_TTS_PROVIDER", "none")
    monkeypatch.setenv("ASSIST_LIVE_LOCAL_ONLY", "false")
    monkeypatch.setenv("ASSIST_LIVE_DOWNLOAD_MODELS", "false")
    reached = []

    async def load(config, *, cache):
        reached.append(cache)
        raise RuntimeError("stop before app setup")

    monkeypatch.setattr(ProviderFactory, "load", load)
    # The changed turn models must reach the readiness check, not stop startup.
    with pytest.raises(RuntimeError, match="stop before app setup"):
        asyncio.run(startup.serve())
    assert reached == [cache]


def synthetic_turn_manifest(tmp_path, cache):
    """Write valid synthetic turn models and a manifest that pins them."""
    records, plan = [], []
    for name in ("silero-vad", "smart-turn-v3.2-cpu"):
        data = f"synthetic {name} model".encode()
        relative = f"models/{name}/{name}.onnx"
        (cache / relative).parent.mkdir(parents=True, exist_ok=True)
        (cache / relative).write_bytes(data)
        revision = "a" * 40
        url = f"https://example.com/{revision}/{name}.onnx"
        digest = hashlib.sha256(data).hexdigest()
        records.append(
            {
                "id": name,
                "kind": "model",
                "download": "startup",
                "status": "included",
                "revision": revision,
                "url": url,
                "sha256": digest,
                "paths": [relative],
                "components": [],
            }
        )
        plan.append(
            {
                "id": name,
                "url": url,
                "sha256": digest,
                "path": relative,
                "bytes": len(data),
            }
        )
    manifest = tmp_path / "synthetic-manifest.json"
    manifest.write_text(json.dumps({"assets": records, "download_plan": plan}))
    return manifest


@pytest.mark.parametrize(
    "case",
    [
        "changed",
        "download_failed",
        "read_failed",
        "missing",
        "outside",
        "alias",
        "timeout",
    ],
)
def test_cloud_startup_serves_health_without_turn_models(tmp_path, case):
    cache = tmp_path / "cache"
    manifest = ""
    if case in {"changed", "outside"}:
        for entry in turn_entries():
            path = cache / entry["path"]
            path.parent.mkdir(parents=True, exist_ok=True)
            if case == "changed":
                path.write_bytes(b"changed model")
            else:
                # A canonical path that resolves outside the cache.
                outside = tmp_path / f"outside-{entry['id']}.onnx"
                outside.write_bytes(b"outside model")
                path.symlink_to(outside)
    elif case == "alias":
        manifest = str(synthetic_turn_manifest(tmp_path, cache))
    registry = Registry(tmp_path / "registry.json")
    registry.bind("test-satellite", "test-satellite", b"x" * 32)
    registry.add_token("synthetic-health-token", ["test-satellite"])
    code = """
import json
import os
from pathlib import Path
from http.client import IncompleteRead
from urllib.error import URLError
import assist_live_server.__main__ as startup
from assist_live_server.turn import detector
def refused(*args, **kwargs):
    if os.environ["SYNTHETIC_CASE"] == "timeout":
        raise URLError(TimeoutError("synthetic download failure"))
    raise URLError("synthetic download failure")
class Interrupted:
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def read1(self, size):
        raise IncompleteRead(b"synthetic download failure")
def interrupted(*args, **kwargs):
    return Interrupted()
read_failed = os.environ["SYNTHETIC_CASE"] == "read_failed"
startup.urlopen = interrupted if read_failed else refused
if os.environ["SYNTHETIC_MANIFEST"]:
    document = json.loads(Path(os.environ["SYNTHETIC_MANIFEST"]).read_text())
    startup.load_asset_manifest = lambda path=None: document
    detector.load_asset_manifest = lambda path=None: document
raise SystemExit(startup.main([]))
"""
    env = {
        **environment(tmp_path),
        "ASSIST_LIVE_STT_PROVIDER": "deepgram",
        "ASSIST_LIVE_TTS_PROVIDER": "elevenlabs",
        "ASSIST_LIVE_LOCAL_ONLY": "false",
        "ASSIST_LIVE_DOWNLOAD_MODELS": (
            "true" if case in {"download_failed", "read_failed", "timeout"} else "false"
        ),
        "SYNTHETIC_CASE": case,
        "ELEVENLABS_API_KEY": "synthetic-key",
        "ELEVENLABS_VOICE_ID": "test-voice",
        "ELEVENLABS_VOICE_NAME": "Test voice",
        "ELEVENLABS_VOICE_LANGUAGE": "en-US",
        "SYNTHETIC_MANIFEST": manifest,
    }
    process = subprocess.Popen(
        [sys.executable, "-c", code],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert select.select([process.stdout], [], [], STARTUP_GUARD)[0]
        line = process.stdout.readline()
        assert line.startswith("PASS server: listening "), line
        request = Request(
            f"http://127.0.0.1:{int(line.split()[-1])}/health",
            headers={"Authorization": "Bearer synthetic-health-token"},
        )
        with urlopen(request, timeout=5) as response:
            body = json.load(response)
    finally:
        process.terminate()
        stdout, stderr = process.communicate(timeout=10)
    assert process.returncode == 0, stdout + stderr
    assert body["healthy"] is True
    assert body["capabilities"]["tts"] is True
    assert "synthetic download failure" not in stdout + stderr
    assert ("model_download_time_limit" in stderr) == (case == "timeout")
    files = sorted(p.relative_to(cache).as_posix() for p in cache.rglob("*"))
    if case == "alias":
        # The turn detector reads canonical paths. Startup makes no alias.
        assert body["capabilities"]["stt"] is True
        assert "unavailable" not in body
        assert [f for f in files if "/" not in f and f.endswith(".onnx")] == []
        return
    assert body["capabilities"]["stt"] is False
    assert body["unavailable"] == {
        "stt": "turn_models_invalid"
        if case in {"changed", "outside"}
        else "turn_models_missing"
    }
    leftovers = [f for f in files if (cache / f).is_file() or (cache / f).is_symlink()]
    assert len(leftovers) == (
        0 if case in {"download_failed", "read_failed", "missing", "timeout"} else 2
    )


@pytest.mark.parametrize(
    "provisioned,status,body",
    [
        (False, 401, b"different server"),
        (False, 403, b"unauthorized"),
        (True, 401, b"unauthorized"),
    ],
)
def test_health_does_not_accept_other_refusals(
    tmp_path, monkeypatch, capsys, provisioned, status, body
):
    from urllib.error import HTTPError

    import assist_live_server.__main__ as startup

    registry = Registry(tmp_path / "registry.json")
    if provisioned:
        registry.bind("test-satellite", "test-satellite", b"x" * 32)
        registry.add_token("synthetic-health-token", ["test-satellite"])
    else:
        registry.save()
    monkeypatch.setenv("ASSIST_LIVE_DATA", str(tmp_path))

    def refuse(*args, **kwargs):
        raise HTTPError(
            "http://127.0.0.1/health", status, "refused", None, io.BytesIO(body)
        )

    monkeypatch.setattr(startup, "urlopen", refuse)
    assert startup.main(["--healthcheck"]) == 1
    assert (
        capsys.readouterr().out
        == "FAIL health: server unavailable or health response invalid\n"
    )


def test_bad_configuration_exits_without_secret(tmp_path):
    result = subprocess.run(
        entrypoint_command(tmp_path, "configuration"),
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
    assert json.loads((tmp_path / "configuration-imports.json").read_text()) == []


@pytest.mark.parametrize("voice", ["", "test-voice"])
def test_invalid_elevenlabs_logging_option_is_named_without_its_value(tmp_path, voice):
    result = subprocess.run(
        entrypoint_command(tmp_path, "logging"),
        env={
            **environment(tmp_path),
            "ASSIST_LIVE_TTS_PROVIDER": "elevenlabs",
            "ASSIST_LIVE_LOCAL_ONLY": "false",
            "ELEVENLABS_API_KEY": "synthetic-key",
            "ELEVENLABS_VOICE_ID": voice,
            "ELEVENLABS_VOICE_LANGUAGE": "en-US",
            "ELEVENLABS_ENABLE_LOGGING": "synthetic-password-value",
        },
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 1
    assert result.stdout == (
        "FAIL server: startup configuration or model check failed: "
        "invalid ELEVENLABS_ENABLE_LOGGING option\n"
    )
    assert "synthetic-password-value" not in result.stdout + result.stderr
    assert "synthetic-key" not in result.stdout + result.stderr
    assert result.stderr == ""


@pytest.mark.parametrize("fetch_only", [False, True])
def test_bad_cached_hash_exits_without_traceback(tmp_path, fetch_only):
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
                        "bytes": 5,
                    }
                ],
            }
        )
    )
    if not fetch_only:
        from assist_live_server.assets import load_asset_manifest

        entry = load_asset_manifest()["download_plan"][0]
        path = cache / entry["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"wrong")
    result = subprocess.run(
        entrypoint_command(
            tmp_path, "hash", *(["--fetch-models"] if fetch_only else [])
        ),
        env={
            **environment(tmp_path),
            "ASSIST_LIVE_TTS_PROVIDER": "piper",
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
    assert json.loads((tmp_path / "hash-imports.json").read_text()) == []


def test_app_option_mapping_keeps_password_out_of_output(tmp_path, monkeypatch, capsys):
    spec = importlib.util.spec_from_file_location(
        "app_run", ROOT / "assist-live-server/run.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    options = tmp_path / "options.json"
    options.write_text(
        json.dumps(
            {
                "stt_provider": "deepgram",
                "local_only": False,
                "deepgram_api_key": "synthetic-password-value",
                "elevenlabs_voice_id": "test-voice",
                "elevenlabs_voice_name": "Test voice",
                "elevenlabs_voice_language": "en-US",
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
    assert env["ELEVENLABS_VOICE_ID"] == "test-voice"
    assert env["ELEVENLABS_VOICE_NAME"] == "Test voice"
    assert env["ELEVENLABS_VOICE_LANGUAGE"] == "en-US"
    assert "synthetic-password-value" not in capsys.readouterr().out


@pytest.mark.parametrize("setting", [None, False, True])
def test_app_maps_optional_elevenlabs_logging(tmp_path, monkeypatch, setting):
    spec = importlib.util.spec_from_file_location(
        "app_run", ROOT / "assist-live-server/run.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    options = tmp_path / "options.json"
    options.write_text(
        json.dumps({} if setting is None else {"elevenlabs_enable_logging": setting})
    )
    calls = []
    monkeypatch.delenv("ELEVENLABS_ENABLE_LOGGING", raising=False)
    monkeypatch.setattr(module.os, "execvpe", lambda *args: calls.append(args))
    module.main(options)
    _, _, env = calls[0]
    if setting is None:
        assert "ELEVENLABS_ENABLE_LOGGING" not in env
    else:
        assert env["ELEVENLABS_ENABLE_LOGGING"] == str(setting).lower()


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
                "bytes": 1,
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
                "bytes": len(data),
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


@pytest.mark.parametrize("past_limit", [False, True])
def test_largest_download_budget_allows_slow_link(tmp_path, monkeypatch, past_limit):
    import assist_live_server.__main__ as startup

    asset_bytes = 652_184_014
    elapsed = 120 + asset_bytes / (256 * 1024) + (1 if past_limit else -1)
    document = startup.load_asset_manifest()
    entry = next(
        row for row in document["download_plan"] if row["id"] == "stt-parakeet"
    )
    record = next(row for row in document["assets"] if row["id"] == entry["id"])
    data = b"synthetic encoder"
    digest = hashlib.sha256(data).hexdigest()
    entry.update(bytes=asset_bytes, sha256=digest)
    record.update(bytes=asset_bytes, sha256=digest)
    clock = [0.0]

    class Transfer(io.BytesIO):
        def read1(self, size):
            clock[0] = elapsed
            return super().read1(size)

    monkeypatch.setattr(startup, "monotonic", lambda: clock[0])
    monkeypatch.setattr(startup, "urlopen", lambda *args, **kwargs: Transfer(data))
    target = tmp_path / entry["path"]
    if past_limit:
        with pytest.raises(ValueError, match="model_download_time_limit"):
            fetch_plan(tmp_path, document, download=True, only=(entry["id"],))
        assert list(target.parent.iterdir()) == []
    else:
        fetch_plan(tmp_path, document, download=True, only=(entry["id"],))
        assert target.read_bytes() == data


def test_local_startup_prints_wrapped_download_timeout(tmp_path, monkeypatch, capsys):
    from urllib.error import URLError

    import assist_live_server.__main__ as startup

    for name, value in {
        **environment(tmp_path),
        "ASSIST_LIVE_STT_PROVIDER": "parakeet",
        "ASSIST_LIVE_DOWNLOAD_MODELS": "true",
    }.items():
        monkeypatch.setenv(name, value)

    def timeout(*args, **kwargs):
        raise URLError(TimeoutError("synthetic download failure"))

    monkeypatch.setattr(startup, "urlopen", timeout)
    assert startup.main([]) == 1
    assert capsys.readouterr().out == (
        "FAIL server: startup configuration or model check failed: "
        "model_download_time_limit\n"
    )

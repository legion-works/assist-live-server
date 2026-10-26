import argparse
import asyncio
import hashlib
import json
import os
import re
import signal
import tempfile
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from aiohttp import web

from .app import create_app
from .assets import load_asset_manifest
from .auth import Registry
from .config import ProviderFactory, ServerConfig
from .store import SQLiteStore
from .turn.detector import model_path, verified_model


def boolean(name: str, default: str) -> bool:
    value = os.environ.get(name, default)
    if value not in {"true", "false"}:
        raise ValueError("invalid boolean option")
    return value == "true"


def download_records(document: dict[str, Any]) -> list[dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}

    def visit(rows: list[dict[str, Any]]) -> None:
        for row in rows:
            if row["id"] in records:
                raise ValueError("duplicate asset record")
            records[row["id"]] = row
            visit(row["components"])

    visit(document["assets"])
    result = []
    seen: set[str] = set()
    for entry in document["download_plan"]:
        row = records[entry["id"]]
        if (
            row["status"] != "included"
            or row["kind"] not in {"model", "voice", "tokenizer"}
            or row["download"] != "startup"
            or entry["id"] in seen
            or any(entry[key] != row[key] for key in ("url", "sha256"))
            or entry["path"] not in row["paths"]
            or not re.fullmatch(r"[a-f0-9]{64}", entry["sha256"])
            or not re.fullmatch(r"[a-f0-9]{40,64}|v?\d+(?:\.\d+)+", row["revision"])
            or row["revision"] not in entry["url"]
            or not entry["url"].startswith("https://")
            or Path(entry["path"]).is_absolute()
            or ".." in Path(entry["path"]).parts
        ):
            raise ValueError("invalid selected download plan")
        seen.add(entry["id"])
        result.append(entry)
    return result


def fetch_plan(cache: Path, document: dict[str, Any], *, download: bool) -> None:
    entries = download_records(document)
    cache.mkdir(parents=True, exist_ok=True)
    aliases: dict[str, str] = {}
    for entry in entries:
        path = model_path(cache, entry)
        if path.exists():
            verified_model(cache, entry)
        elif download:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    dir=path.parent, delete=False
                ) as output:
                    temporary = Path(output.name)
                    digest = hashlib.sha256()
                    with urlopen(entry["url"], timeout=120) as response:
                        while data := response.read(1024 * 1024):
                            digest.update(data)
                            output.write(data)
                    if digest.hexdigest() != entry["sha256"]:
                        raise ValueError("download SHA-256 mismatch")
                temporary.replace(path)
                verified_model(cache, entry)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
        else:
            continue
        # Provider loaders use basenames; the manifest retains canonical paths.
        if path.suffix in {".onnx", ".json", ".txt"}:
            name = path.name
            if name in aliases and aliases[name] != entry["sha256"]:
                raise ValueError("ambiguous model basename")
            aliases[name] = entry["sha256"]
            alias = cache / name
            if alias != path:
                if alias.exists():
                    verified_model(cache, {**entry, "path": name})
                else:
                    alias.hardlink_to(path)


async def serve() -> None:
    def provider(name: str, default: str) -> str | None:
        value = os.environ.get(name, default)
        return None if value == "none" else value

    config = await ServerConfig.load(
        stt_provider=provider("ASSIST_LIVE_STT_PROVIDER", "parakeet"),
        tts_provider=provider("ASSIST_LIVE_TTS_PROVIDER", "piper"),
        local_only=boolean("ASSIST_LIVE_LOCAL_ONLY", "true"),
        record_transcripts=boolean("ASSIST_LIVE_RECORD_TRANSCRIPTS", "false"),
        speaker_url=os.environ.get("ASSIST_LIVE_SPEAKER_URL") or None,
    )
    data = Path(os.environ.get("ASSIST_LIVE_DATA", "/data"))
    data.mkdir(parents=True, exist_ok=True, mode=0o700)
    cache = Path(os.environ.get("ASSIST_LIVE_MODEL_CACHE", str(data / "cache")))
    if config.stt_provider == "parakeet" or config.tts_provider == "piper":
        await asyncio.to_thread(
            fetch_plan,
            cache,
            load_asset_manifest(),
            download=boolean("ASSIST_LIVE_DOWNLOAD_MODELS", "true"),
        )
    factory = await ProviderFactory.load(config, cache=cache)
    registry_path = data / "registry.json"
    if registry_path.exists():
        registry = Registry.load(registry_path)
    else:
        registry = Registry(registry_path)
        registry.save()
    store = SQLiteStore(data / "turns.sqlite3")
    runner = web.AppRunner(
        create_app(registry, store, providers=factory), access_log=None
    )
    try:
        await runner.setup()
        host = os.environ.get("ASSIST_LIVE_HOST", "0.0.0.0")
        port = int(os.environ.get("ASSIST_LIVE_PORT", "8099"))
        if not 0 <= port <= 65535:
            raise ValueError("invalid port")
        site = web.TCPSite(runner, host, port)
        await site.start()
        address = runner.addresses[0]
        print(f"PASS server: listening {address[1]}", flush=True)
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)
        await stop.wait()
    finally:
        await runner.cleanup()
        await asyncio.to_thread(store.close)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fetch-models", action="store_true")
    parser.add_argument("--healthcheck", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.healthcheck:
            data = Path(os.environ.get("ASSIST_LIVE_DATA", "/data"))
            registry = Registry.load(data / "registry.json")
            token = next(iter(registry.tokens))
            port = int(os.environ.get("ASSIST_LIVE_PORT", "8099"))
            request = Request(
                f"http://127.0.0.1:{port}/health",
                headers={"Authorization": "Bearer " + token},
            )
            with urlopen(request, timeout=2) as response:
                if response.status != 200 or not json.load(response)["healthy"]:
                    raise ValueError("unhealthy server")
            print("PASS server: health checked")
        elif args.fetch_models:
            cache = Path(os.environ.get("ASSIST_LIVE_MODEL_CACHE", "/data/cache"))
            fetch_plan(
                cache,
                load_asset_manifest(os.environ.get("ASSIST_LIVE_ASSET_MANIFEST")),
                download=True,
            )
            print("PASS server: selected downloads checked")
        else:
            asyncio.run(serve())
    except Exception:
        print("FAIL server: startup configuration or model check failed", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

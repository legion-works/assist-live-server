import argparse
import asyncio
import hashlib
import json
import logging
import os
import re
import signal
import tempfile
from pathlib import Path
from time import monotonic
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# ONNX Runtime reads this opt-out before its first import, not at session setup.
os.environ["ORT_DISABLE_TELEMETRY"] = "1"

from .assets import (  # noqa: E402
    TURN_MODEL_IDS,
    load_asset_manifest,
    model_path,
    verified_model,
)
from .auth import Registry  # noqa: E402

DOWNLOAD_DEADLINE = 120.0
DOWNLOAD_SOCKET_TIMEOUT = 120.0
DOWNLOAD_MIN_BYTES_PER_SECOND = 256 * 1024
LOGGER = logging.getLogger(__name__)


class ModelDownloadLimitError(ValueError):
    def __init__(self, reason: str, asset: str) -> None:
        super().__init__(f"{reason}: {asset}")
        self.reason = reason


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
        byte_limit = entry.get("bytes")
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
            or type(byte_limit) is not int
            or byte_limit <= 0
            or ("bytes" in row and row["bytes"] != byte_limit)
        ):
            raise ValueError("invalid selected download plan")
        seen.add(entry["id"])
        result.append(entry)
    return result


def fetch_plan(
    cache: Path,
    document: dict[str, Any],
    *,
    download: bool,
    only: tuple[str, ...] | None = None,
    basename_aliases: bool = True,
) -> None:
    entries = [
        entry
        for entry in download_records(document)
        if only is None or entry["id"] in only
    ]
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
                    size = 0
                    byte_limit = entry["bytes"]
                    deadline = (
                        monotonic()
                        + DOWNLOAD_DEADLINE
                        + byte_limit / DOWNLOAD_MIN_BYTES_PER_SECOND
                    )
                    try:
                        with urlopen(
                            entry["url"], timeout=DOWNLOAD_SOCKET_TIMEOUT
                        ) as response:
                            while True:
                                if monotonic() >= deadline:
                                    raise TimeoutError
                                # A buffered read can wait for many socket reads.
                                data = response.read1(
                                    min(1024 * 1024, byte_limit - size + 1)
                                )
                                if monotonic() >= deadline:
                                    raise TimeoutError
                                if not data:
                                    break
                                size += len(data)
                                if size > byte_limit:
                                    raise ModelDownloadLimitError(
                                        "model_download_bytes_limit", entry["id"]
                                    )
                                digest.update(data)
                                output.write(data)
                    except (TimeoutError, URLError) as error:
                        if isinstance(error, URLError) and not isinstance(
                            error.reason, TimeoutError
                        ):
                            raise
                        raise ModelDownloadLimitError(
                            "model_download_time_limit", entry["id"]
                        ) from None
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
        if basename_aliases and path.suffix in {".onnx", ".json", ".txt"}:
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
    from .config import ProviderFactory, ServerConfig

    def provider(name: str, default: str) -> str | None:
        value = os.environ.get(name, default)
        return None if value == "none" else value

    config = await ServerConfig.load(
        stt_provider=provider("ASSIST_LIVE_STT_PROVIDER", "parakeet"),
        tts_provider=provider("ASSIST_LIVE_TTS_PROVIDER", "piper"),
        local_only=boolean("ASSIST_LIVE_LOCAL_ONLY", "true"),
        record_transcripts=boolean("ASSIST_LIVE_RECORD_TRANSCRIPTS", "false"),
        speaker_url=os.environ.get("ASSIST_LIVE_SPEAKER_URL") or None,
        stt_hint_bonus=float(os.environ.get("ASSIST_LIVE_STT_HINT_BONUS", "1.0")),
        end_rule=os.environ.get("ASSIST_LIVE_END_RULE", "partial"),
    )
    data = Path(os.environ.get("ASSIST_LIVE_DATA", "/data"))
    data.mkdir(parents=True, exist_ok=True, mode=0o700)
    cache = Path(os.environ.get("ASSIST_LIVE_MODEL_CACHE", str(data / "cache")))
    os.environ["ASSIST_LIVE_MODEL_CACHE"] = str(cache)
    download = boolean("ASSIST_LIVE_DOWNLOAD_MODELS", "true")
    if config.stt_provider == "parakeet" or config.tts_provider == "piper":
        await asyncio.to_thread(
            fetch_plan, cache, load_asset_manifest(), download=download
        )
    elif config.stt_provider is not None:
        # A cloud STT turn also uses the local turn detector models. No failure
        # of this fetch may stop the server or its TTS: the readiness check in
        # ProviderFactory.load reads the cache and reports STT as unavailable.
        # The catch is broad because download errors come from several
        # families (OSError, ValueError, http.client.HTTPException).
        # The turn detector reads canonical paths only, thus this fetch makes
        # no basename aliases: an alias error cannot hide behind a ready STT.
        try:
            await asyncio.to_thread(
                fetch_plan,
                cache,
                load_asset_manifest(),
                download=download,
                only=TURN_MODEL_IDS,
                basename_aliases=False,
            )
        except ModelDownloadLimitError as error:
            LOGGER.warning("%s", error.reason)
        except Exception:
            pass
    factory = await ProviderFactory.load(config, cache=cache)
    from aiohttp import web

    from .app import create_app
    from .store import SQLiteStore

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
            token = next(iter(registry.tokens), None)
            port = int(os.environ.get("ASSIST_LIVE_PORT", "8099"))
            request = Request(
                f"http://127.0.0.1:{port}/health",
                headers={"Authorization": "Bearer " + token} if token else {},
            )
            try:
                with urlopen(request, timeout=2) as response:
                    if response.status != 200 or not json.load(response)["healthy"]:
                        raise ValueError("unhealthy server")
            except HTTPError as error:
                with error:
                    if (
                        token is not None
                        or error.code != 401
                        or error.read(64) != b"unauthorized"
                    ):
                        raise ValueError("health response invalid") from None
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
    except Exception as error:
        failure = (
            "FAIL health: server unavailable or health response invalid"
            if args.healthcheck
            else "FAIL server: startup configuration or model check failed"
        )
        from .config import EndRuleError
        from .providers.stt import STTCredentialLengthError
        from .providers.tts import ElevenLabsLoggingError

        # These messages are fixed text without configured values.
        if isinstance(
            error, EndRuleError | STTCredentialLengthError | ElevenLabsLoggingError
        ):
            failure += ": " + str(error)
        elif isinstance(error, ModelDownloadLimitError):
            failure += ": " + error.reason
        print(failure, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

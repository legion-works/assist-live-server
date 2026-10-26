"""Read the packaged asset pins, or the same file in an editable checkout."""

import hashlib
import json
from pathlib import Path
from typing import Any


def asset_manifest_path() -> Path:
    packaged = Path(__file__).resolve().parent / "assets/manifest.json"
    if packaged.is_file():
        return packaged
    return Path(__file__).resolve().parents[2] / "assets/manifest.json"


def load_asset_manifest(path: Path | str | None = None) -> dict[str, Any]:
    return dict(json.loads(Path(path or asset_manifest_path()).read_text()))


def model_path(cache: Path, record: dict[str, Any]) -> Path:
    root = cache.resolve()
    path = (root / record["path"]).resolve()
    if not path.is_relative_to(root):
        raise ValueError("model download path must stay inside cache")
    return path


def verified_model(cache: Path, record: dict[str, Any]) -> Path:
    path = model_path(cache, record)
    if not path.is_file():
        raise ValueError(
            f"FAIL model cache: missing {record['id']}; run --fetch-models"
        )
    if hashlib.sha256(path.read_bytes()).hexdigest() != record["sha256"]:
        raise ValueError(f"model SHA-256 mismatch: {record['id']}")
    return path

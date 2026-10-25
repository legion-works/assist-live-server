"""Read the packaged asset pins, or the same file in an editable checkout."""

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

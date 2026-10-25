"""Keep one asset manifest in direct wheels and wheels from source archives."""

import importlib
from pathlib import Path
from typing import Any

BuildHookInterface: Any = importlib.import_module(
    "hatchling.builders.hooks.plugin.interface"
).BuildHookInterface


class AssetManifestHook(BuildHookInterface):
    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        root = Path(self.root)
        source = root / "assets/manifest.json"
        if not source.is_file():
            source = root.parent / "assets/manifest.json"
        if not source.is_file():
            raise FileNotFoundError("asset manifest required for server package")
        destination = (
            "assets/manifest.json"
            if self.target_name == "sdist"
            else "assist_live_server/assets/manifest.json"
        )
        build_data.setdefault("force_include", {})[str(source)] = destination

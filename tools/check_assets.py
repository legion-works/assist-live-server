"""Stop releases with incomplete asset rights or blocked components."""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))
ALLOWED = {"Apache-2.0", "MIT", "BSD-2-Clause", "BSD-3-Clause", "CC0-1.0", "CC-BY-4.0"}
KINDS = {"model", "voice", "runtime", "phonemizer", "tokenizer", "voice_data"}
ASSET_SUFFIXES = {
    ".onnx",
    ".pt",
    ".pth",
    ".tflite",
    ".safetensors",
    ".bin",
    ".gguf",
    ".ckpt",
    ".wav",
    ".so",
    ".dll",
    ".dylib",
}


def load_json(path: Path, errors: list[str]) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        errors.append(f"manifest input {path.name}: {error.__class__.__name__}")
        return None


def provider_configured(root: Path) -> bool:
    """An adapter module makes an empty included-asset list unsafe."""
    return any(
        path.name != "__init__.py"
        for path in (root / "server/assist_live_server/providers").rglob("*.py")
    )


def release_files(root: Path) -> list[Path]:
    """Use the publishable Git tree, or a plain exported release tree."""
    if (root / ".git").exists():
        top = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )
        if Path(top.stdout.strip()).resolve() != root.resolve():
            raise ValueError("release tree must be its own Git root")
        output = subprocess.run(
            ["git", "ls-files", "-co", "--exclude-standard", "-z"],
            cwd=root,
            check=True,
            capture_output=True,
        )
        return [root / os.fsdecode(name) for name in output.stdout.split(b"\0") if name]
    paths: list[Path] = []
    excluded = {
        ".git",
        ".venv",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        "dist",
        "build",
    }
    for directory, subdirectories, filenames in os.walk(root):
        subdirectories[:] = [name for name in subdirectories if name not in excluded]
        paths.extend(Path(directory) / name for name in filenames)
    return paths


def local_path(root: Path, value: object) -> Path | None:
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        return None
    path = (root / value).resolve()
    return path if path.is_relative_to(root.resolve()) else None


def https_url(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    return parsed.scheme == "https" and bool(parsed.netloc) and not parsed.username


def flatten(records: list[Any], errors: list[str]) -> list[dict[str, Any]]:
    result = []
    for record in records:
        if not isinstance(record, dict):
            errors.append("asset record must be an object")
            continue
        result.append(record)
        components = record.get("components")
        if not isinstance(components, list):
            errors.append(f"{record.get('id')}: components must be an explicit list")
        else:
            result.extend(flatten(components, errors))
    return result


def inventory(
    value: object, root: Path, label: str, errors: list[str]
) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        path = local_path(root, value)
        if path is None:
            errors.append(f"{label}: invalid inventory path")
            return []
        value = load_json(path, errors)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        errors.append(f"{label}: expected an asset-id list")
        return []
    return value


def check_included(record: dict[str, Any], root: Path, errors: list[str]) -> None:
    label = str(record.get("id"))
    revision = record.get("revision")
    if not isinstance(revision, str) or not re.fullmatch(
        r"[a-fA-F0-9]{40,64}", revision
    ):
        errors.append(f"{label}: immutable revision required")
        revision = ""
    if (
        not https_url(record.get("url"))
        or not revision
        or revision not in record["url"]
    ):
        errors.append(f"{label}: pinned asset URL required")
    digest = record.get("sha256")
    if not isinstance(digest, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", digest):
        errors.append(f"{label}: SHA-256 required")
    license_id = record.get("license")
    if not isinstance(license_id, str) or license_id not in ALLOWED:
        errors.append(f"{label}: license does not permit this Apache image policy")
    if record.get("redistribution") is not True:
        errors.append(f"{label}: redistribution permission required")
    if record.get("download") not in ("included", "startup"):
        errors.append(f"{label}: included or startup download behavior required")
    if (
        not isinstance(record.get("attribution"), str)
        or not record["attribution"].strip()
    ):
        errors.append(f"{label}: attribution required")
    paths = record.get("paths")
    if not isinstance(paths, list) or not paths:
        errors.append(f"{label}: artifact paths required")
        return
    for value in paths:
        path = local_path(root, value)
        if path is None:
            errors.append(f"{label}: invalid artifact path")
        elif path.is_file():
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                errors.append(f"{label}: artifact SHA-256 mismatch")


def check_absent(
    record: dict[str, Any],
    root: Path,
    image: list[str] | None,
    downloads: list[str],
    errors: list[str],
    tree: list[Path],
) -> None:
    label = str(record.get("id"))
    if record.get("download") != "none":
        errors.append(f"{label}: blocked asset must not download")
    paths = record.get("paths")
    if not isinstance(paths, list) or not paths:
        errors.append(f"{label}: absence paths required")
    else:
        for value in paths:
            path = local_path(root, value)
            if path is None or path.exists():
                errors.append(
                    f"{label}: blocked asset path in release tree or invalid path"
                )
    if label in (image or []) or label in downloads:
        errors.append(f"{label}: blocked asset in image inventory or download plan")
    # Tests and rights records describe blocked assets. They are not runtime inputs.
    candidates = [
        path
        for path in tree
        if path.is_file()
        and (
            path.relative_to(root).parts[0] in {"server", "client", "ha-app"}
            or (
                len(path.relative_to(root).parts) == 1
                and (
                    path.suffix in {".toml", ".lock", ".yml", ".yaml"}
                    or path.name.startswith("Dockerfile")
                )
            )
        )
    ]
    identifiers = record.get("identifiers", [])
    if not isinstance(identifiers, list) or not all(
        isinstance(token, str) and token for token in identifiers
    ):
        errors.append(f"{label}: invalid absence identifiers")
        identifiers = []
    tokens = [label, record.get("url"), *identifiers]
    for path in candidates:
        text = path.read_bytes().lower()
        if any(
            isinstance(token, str) and token.encode().lower() in text
            for token in tokens
        ):
            errors.append(f"{label}: blocked asset reference in release source")


def check_assets(manifest: Path, release: bool) -> list[str]:
    """Return all stop-work errors. Both delivery paths use the same policy."""
    errors: list[str] = []
    from tools.asset_rights import source_errors

    manifest = Path(manifest).resolve()
    document = load_json(manifest, errors)
    if isinstance(document, list):
        document = {"assets": document}
    if not isinstance(document, dict) or not isinstance(document.get("assets"), list):
        return errors + ["manifest must contain an asset list"]
    root_value = document.get("release_root", ".")
    if not isinstance(root_value, str) or Path(root_value).is_absolute():
        return errors + ["release_root must be a relative path"]
    root = (manifest.parent / root_value).resolve()
    if not root.is_dir():
        return errors + ["release tree missing"]
    records = flatten(document["assets"], errors)
    try:
        tree = release_files(root)
    except OSError, ValueError, subprocess.CalledProcessError:
        return errors + ["cannot read publishable release tree"]
    image = inventory(document.get("image_inventory"), root, "image inventory", errors)
    default_downloads = [
        item.get("id") for item in records if item.get("download") == "startup"
    ]
    downloads = inventory(
        document.get("download_plan", default_downloads), root, "download plan", errors
    )
    if downloads is None:
        errors.append("download plan required")
        downloads = []
    included = {
        item.get("id")
        for item in records
        if isinstance(item.get("id"), str)
        and item.get("status") == "confirmed_included"
    }
    ids: set[str] = set()
    for record in records:
        label = record.get("id")
        if not isinstance(label, str) or not re.fullmatch(
            r"[a-z0-9][a-z0-9._-]*", label
        ):
            errors.append("asset id required")
            continue
        if label in ids:
            errors.append(f"{label}: duplicate asset id")
        ids.add(label)
        errors.extend(source_errors(record))
        inspection = record.get("inspection", {})
        if isinstance(inspection, dict):
            packages = inspection.get("supporting_packages", [])
            if not isinstance(packages, list):
                errors.append(f"{label}: supporting package list required")
            else:
                for package in packages:
                    if isinstance(package, dict):
                        errors.extend(source_errors(package))
                    else:
                        errors.append(f"{label}: supporting package record required")
        if not isinstance(record.get("kind"), str) or record["kind"] not in KINDS:
            errors.append(f"{label}: asset kind required")
        if record.get("status") == "confirmed_included":
            check_included(record, root, errors)
        elif record.get("status") == "confirmed_blocked_absent":
            check_absent(record, root, image, downloads, errors, tree)
        else:
            errors.append(f"{label}: confirmed investigation status required")
    for label in (image or []) + downloads:
        if label not in included:
            errors.append(f"{label}: inventory asset has no approved included record")
    declared_paths: set[Path] = set()
    for record in records:
        paths = record.get("paths")
        if record.get("status") == "confirmed_included" and isinstance(paths, list):
            for value in paths:
                path = local_path(root, value)
                if path is not None:
                    declared_paths.add(path)
    for path in tree:
        if (
            path.suffix.lower() in ASSET_SUFFIXES
            and path.resolve() not in declared_paths
        ):
            errors.append(f"unlisted release-tree asset: {path.relative_to(root)}")
    for record in records:
        if record.get("status") == "confirmed_included":
            if (
                record.get("download") == "startup"
                and record.get("id") not in downloads
            ):
                errors.append(
                    f"{record.get('id')}: startup asset missing from download plan"
                )
            if image is not None and record.get("download") == "included":
                if record.get("id") not in image:
                    errors.append(
                        f"{record.get('id')}: asset missing from image inventory"
                    )
    if release and provider_configured(root):
        if not included:
            errors.append("empty included-asset manifest after provider configuration")
        if image is None:
            errors.append("image inventory required after provider configuration")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=ROOT / "assets/manifest.json")
    parser.add_argument("--release", action="store_true")
    args = parser.parse_args(argv)
    errors = check_assets(args.manifest, args.release)
    try:
        document = json.loads(args.manifest.read_text())
        if isinstance(document, list) or document.get("image_inventory") is None:
            print("NOT CHECKED image inventory: no image inventory exists")
    except OSError, ValueError, AttributeError:
        pass
    for error in errors:
        print(f"FAIL asset licenses: {error}")
    if errors:
        return 1
    print("PASS asset licenses")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

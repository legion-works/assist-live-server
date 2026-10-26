"""Check release records and artifacts without publishing them."""

import argparse
import hashlib
import importlib.metadata
import itertools
import json
import re
import sys
import tarfile
import tomllib
import zipfile
from email.parser import Parser
from pathlib import Path, PurePosixPath
from typing import Any, NoReturn
from urllib.error import HTTPError
from urllib.parse import unquote, urlparse
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))

from tools.asset_rights import pinned_source, source_errors  # noqa: E402
from tools.check_assets import check_assets, flatten  # noqa: E402

MODEL_SUFFIXES = {
    ".onnx",
    ".pt",
    ".pth",
    ".tflite",
    ".safetensors",
    ".bin",
    ".gguf",
    ".ckpt",
    ".pb",
    ".h5",
    ".npz",
    ".npy",
}
PROJECT_LABEL = (
    "Project code: Apache-2.0; third-party components: see /app/THIRD_PARTY_NOTICES.md"
)
MODEL_PACKAGES = {
    "numpy",
    "onnxruntime",
    "onnx-asr",
    "piper-tts",
    "torch",
    "transformers",
    "sherpa-onnx",
    "kokoro",
    "silero-vad",
}


PACKAGE_IDS = {
    "piper-runtime": "piper-tts",
    "piper-espeak-bridge": "piper-tts",
    "piper-espeak-data": "piper-tts",
    "piper-onnxruntime": "onnxruntime",
    "piper-numpy": "numpy",
    "piper-pathvalidate": "pathvalidate",
}


def lock_packages(path: Path) -> dict[str, dict[str, Any]]:
    return {row["name"]: row for row in tomllib.loads(path.read_text())["package"]}


def wheel_matches(url: str, tags: set[str]) -> bool:
    filename = PurePosixPath(unquote(urlparse(url).path)).name
    python, abi, platform = filename.removesuffix(".whl").rsplit("-", 3)[-3:]
    return bool(
        tags
        & {
            "-".join(parts)
            for parts in itertools.product(
                python.split("."), abi.split("."), platform.split(".")
            )
        }
    )


def provenance(lock: Path) -> list[dict[str, Any]]:
    packages = lock_packages(lock)
    result = []
    for distribution in importlib.metadata.distributions():
        name = str(distribution.metadata["Name"]).lower().replace("_", "-")
        if name not in packages or "wheels" not in packages[name]:
            continue
        tags = set(
            Parser().parsestr(distribution.read_text("WHEEL") or "").get_all("Tag", [])
        )
        wheels = [
            row for row in packages[name]["wheels"] if wheel_matches(row["url"], tags)
        ]
        if len(wheels) != 1 or distribution.version != packages[name]["version"]:
            raise ValueError("installed wheel does not match lock")
        result.append(
            {
                "name": name,
                "version": distribution.version,
                "url": wheels[0]["url"],
                "sha256": wheels[0]["hash"].removeprefix("sha256:"),
            }
        )
    return sorted(result, key=lambda row: row["name"])


def catalog_installed(document: dict[str, Any], lock: Path) -> dict[str, Any]:
    packages = lock_packages(lock)
    origins = {row["name"]: row for row in provenance(lock)}
    document["assets"] = [
        row for row in document["assets"] if not str(row["id"]).startswith("bundled-")
    ]
    document["image_inventory"] = [
        name for name in document["image_inventory"] if not name.startswith("bundled-")
    ]
    document["build_plan"] = []
    document["wheel_sources"] = {
        name: {"version": packages[name]["version"], "wheels": packages[name]["wheels"]}
        for name in ("numpy", "onnx-asr", "onnxruntime", "piper-tts", "pathvalidate")
    }
    rows = flatten(document["assets"], [])
    for row in rows:
        if row["id"] in PACKAGE_IDS:
            package = PACKAGE_IDS[row["id"]]
            row["download"] = "included"
            if row["id"] not in document["image_inventory"]:
                document["image_inventory"].append(row["id"])
            document["build_plan"].append(
                {
                    "id": row["id"],
                    "package": package,
                    "version": packages[package]["version"],
                    "wheels": packages[package]["wheels"],
                }
            )
    document["download_plan"] = [
        row for row in document["download_plan"] if row["id"] not in PACKAGE_IDS
    ]
    for name in ("numpy", "onnx-asr", "onnxruntime", "piper-tts"):
        distribution = importlib.metadata.distribution(name)
        origin = origins[name]
        license_value = str(
            distribution.metadata.get("License-Expression")
            or distribution.metadata.get("License")
            or "NOASSERTION"
        ).splitlines()[0]
        license_value = {"MIT License": "MIT"}.get(license_value, license_value)
        for file in sorted(distribution.files or [], key=str):
            if PurePosixPath(str(file)).suffix.lower() not in MODEL_SUFFIXES:
                continue
            content = distribution.locate_file(file).read_bytes()
            digest = hashlib.sha256(content).hexdigest()
            label = (
                "bundled-"
                + name
                + "-"
                + hashlib.sha256(str(file).encode()).hexdigest()[:16]
            )
            license_id = license_value
            field = (
                "License-Expression"
                if distribution.metadata.get("License-Expression")
                else "License"
            )
            quote = field + ": " + str(distribution.metadata[field])
            attribution = (
                f"{name} {distribution.version}. {license_id}. "
                "Preserve the wheel license and notices."
            )
            if str(file) == "piper/hebrew/nakdimon.onnx":
                license_id = "MIT"
                quote = distribution.locate_file("piper/hebrew/LICENSE").read_text()
                attribution = (
                    "Nakdimon. Copyright 2022, Elazar Gershuni. MIT. "
                    "Preserve piper/hebrew/LICENSE and SOURCE."
                )
            source = origin["url"] + "#member=" + str(file)
            record = {
                "id": label,
                "kind": "model",
                "status": "included",
                "url": source,
                "revision": distribution.version,
                "sha256": digest,
                "license": license_id,
                "license_sources": [
                    {
                        "kind": "license_text",
                        "url": source,
                        "quote": quote,
                        "revision_or_version": distribution.version,
                        "file_binding": {"path": str(file), "sha256": digest},
                    }
                ],
                "redistribution": True,
                "download": "included",
                "attribution": attribution,
                "paths": ["build/.venv/lib/python3.14/site-packages/" + str(file)],
                "components": [],
                "package": {
                    "name": name,
                    "version": distribution.version,
                    "wheel_sha256": origin["sha256"],
                    "wheel_url": origin["url"],
                    "member": str(file),
                },
                "size": len(content),
                "runtime_used": str(file) == "onnx_asr/preprocessors/data/fbanks.npz",
            }
            if record["runtime_used"]:
                record["usage_source"] = (
                    "https://github.com/istupakov/onnx-asr/blob/v0.12.0/src/onnx_asr/preprocessors/numpy_preprocessor.py#L22-L25"
                )
            document["assets"].append(record)
            document["image_inventory"].append(label)
    return document


def check_records(document: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    records = flatten(document["assets"], errors)
    by_id = {row["id"]: row for row in records}
    if len(by_id) != len(records):
        errors.append("duplicate asset record")
    for row in records:
        if row.get("status") != "included":
            continue
        errors.extend(source_errors(row))
        if not pinned_source(row.get("url"), row.get("revision")):
            errors.append(f"{row['id']}: pinned source required")
        if not re.fullmatch(r"[a-f0-9]{64}", str(row.get("sha256", ""))):
            errors.append(f"{row['id']}: SHA-256 required")
        if not isinstance(row.get("license"), str) or not row["license"].strip():
            errors.append(f"{row['id']}: applicable license required")
        if (
            not isinstance(row.get("attribution"), str)
            or not row["attribution"].strip()
        ):
            errors.append(f"{row['id']}: attribution required")
    seen = set()
    for entry in document["download_plan"]:
        row = by_id.get(entry["id"], {})
        if (
            entry["id"] in seen
            or row.get("status") != "included"
            or row.get("download") != "startup"
            or row.get("kind") not in {"model", "voice", "tokenizer"}
            or any(row.get(key) != entry.get(key) for key in ("url", "sha256"))
            or entry.get("path") not in row.get("paths", [])
        ):
            errors.append(
                f"{entry['id']}: download-plan entry differs from selected record"
            )
        seen.add(entry["id"])
    for row in records:
        if (
            row.get("status") == "included"
            and row.get("download") == "startup"
            and row["id"] not in seen
        ):
            errors.append(f"{row['id']}: missing download-plan entry")
    return errors


def check_wheel(path: Path) -> tuple[list[str], list[str]]:
    errors = []
    with zipfile.ZipFile(path) as archive:
        metadata = [
            name for name in archive.namelist() if name.endswith(".dist-info/METADATA")
        ]
        if len(metadata) != 1:
            return ["client wheel requires one metadata record"], []
        message = Parser().parsestr(archive.read(metadata[0]).decode())
        dependencies = list(message.get_all("Requires-Dist", []))
        if message.get("Name") != "assist-live-client":
            errors.append("not an assist-live-client wheel")
        names = {
            re.split(r"[\s<>=!~;\[]", dependency, maxsplit=1)[0]
            .lower()
            .replace("_", "-")
            for dependency in dependencies
        }
        if names != {"aiohttp"}:
            errors.append("client wheel dependencies must contain only aiohttp")
        for name in archive.namelist():
            if PurePosixPath(name).suffix.lower() in MODEL_SUFFIXES or name.startswith(
                "assist_live_server/"
            ):
                errors.append(f"client wheel contains server or model file: {name}")
    return errors, dependencies


def safe_member(name: str) -> str:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("unsafe archive member")
    return str(path)


def archive_json(archive: tarfile.TarFile, name: str) -> Any:
    stream = archive.extractfile(safe_member(name))
    if stream is None:
        raise ValueError("missing archive record")
    return json.load(stream)


def image_manifests(archive: tarfile.TarFile) -> list[tuple[dict[str, Any], list[str]]]:
    names = set(archive.getnames())
    if "manifest.json" in names:
        return [
            (archive_json(archive, row["Config"]), row["Layers"])
            for row in archive_json(archive, "manifest.json")
        ]
    result = []

    def blob(descriptor: dict[str, Any]) -> str:
        digest = descriptor["digest"]
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", digest):
            raise ValueError("unsupported OCI digest")
        return "blobs/sha256/" + digest.split(":")[1]

    def visit(descriptor: dict[str, Any]) -> None:
        manifest = archive_json(archive, blob(descriptor))
        if "manifests" in manifest:
            for child in manifest["manifests"]:
                visit(child)
        elif (
            descriptor.get("annotations", {}).get("vnd.docker.reference.type")
            != "attestation-manifest"
        ):
            config = archive_json(archive, blob(manifest["config"]))
            if config.get("os") != "unknown":
                result.append((config, [blob(layer) for layer in manifest["layers"]]))

    for descriptor in archive_json(archive, "index.json")["manifests"]:
        visit(descriptor)
    return result


def apache_claims(text: str, records: list[dict[str, Any]]) -> bool:
    names = {
        "third-party components",
        "third party components",
        "piper",
        "piper-tts",
        "lessac",
        "onnxruntime",
        "onnx-asr",
        "silero",
        "smart turn",
    }
    names.update(
        str(row["id"]).lower()
        for row in records
        if row.get("status") == "included" and row.get("license") != "Apache-2.0"
    )
    for line in text.splitlines():
        line = line.lower()
        if "apache-2.0" not in line and "apache license, version 2.0" not in line:
            continue
        if any(
            word in line
            for word in (
                "not ",
                "does not",
                "do not",
                "no ",
                "project code:",
                '"quote":',
            )
        ):
            continue
        if any(
            re.search(r"(?<![\w-])" + re.escape(name) + r"(?![\w-])", line)
            for name in names
        ):
            return True
    return False


def check_image(
    path: Path, document: dict[str, Any]
) -> tuple[list[str], dict[str, Any]]:
    errors = check_records(document)
    records = flatten(document["assets"], [])
    included = [row for row in records if row.get("status") == "included"]
    assets: list[dict[str, Any]] = []
    packages: set[tuple[str, str]] = set()
    platforms = []
    with tarfile.open(path, "r:*") as archive:
        manifests = image_manifests(archive)
        if not manifests:
            errors.append("image has no platform manifests")
        for config, layers in manifests:
            platform = config["os"] + "/" + config["architecture"]
            platforms.append(platform)
            labels = config.get("config", {}).get("Labels") or {}
            if labels.get("org.opencontainers.image.licenses") != PROJECT_LABEL:
                errors.append("project-scoped license label required")
            if any(
                apache_claims(f"{key}: {value}", records)
                for key, value in labels.items()
                if key != "org.opencontainers.image.licenses"
            ):
                errors.append("third-party Apache-2.0 claim in image labels")
            seen: set[str] = set()
            installed: dict[str, str] = {}
            origins: dict[str, dict[str, Any]] = {}
            for layer_name in layers:
                stream = archive.extractfile(safe_member(layer_name))
                if stream is None:
                    raise ValueError("missing image layer")
                with tarfile.open(fileobj=stream, mode="r|*") as layer:
                    for member in layer:
                        name = safe_member(member.name)
                        path_name = PurePosixPath(name)
                        if path_name.name.startswith(".wh."):
                            removed = str(
                                path_name.parent / path_name.name.removeprefix(".wh.")
                            )
                            opaque = path_name.name == ".wh..wh..opq"
                            for row in included:
                                if row["download"] == "included" and any(
                                    removed == item
                                    or removed.endswith("/" + item)
                                    or (
                                        opaque
                                        and item.startswith(str(path_name.parent) + "/")
                                    )
                                    for item in row["paths"]
                                ):
                                    errors.append(f"{row['id']}: bundled asset removed")
                            continue
                        model = PurePosixPath(
                            name
                        ).suffix.lower() in MODEL_SUFFIXES or any(
                            row["download"] == "included"
                            and row["kind"] in {"model", "voice", "tokenizer"}
                            and any(
                                name == item or name.endswith("/" + item)
                                for item in row["paths"]
                            )
                            for row in included
                        )
                        if not member.isfile():
                            if model and not member.isdir():
                                errors.append(
                                    f"{name}: model asset must be a regular file"
                                )
                            continue
                        metadata = name.endswith(".dist-info/METADATA")
                        text = member.size <= 4 * 1024 * 1024 and PurePosixPath(
                            name
                        ).suffix.lower() in {
                            ".txt",
                            ".md",
                            ".json",
                            ".yaml",
                            ".yml",
                            ".toml",
                            ".html",
                            ".csv",
                            ".py",
                            ".rst",
                            ".cfg",
                        }
                        text = text or (
                            member.size <= 4 * 1024 * 1024
                            and PurePosixPath(name).name.lower()
                            in {"license", "copying", "notice", "copyright"}
                        )
                        if not (model or metadata or text):
                            continue
                        content = layer.extractfile(member)
                        if content is None:
                            continue
                        if model:
                            hasher = hashlib.sha256()
                            while chunk := content.read(1024 * 1024):
                                hasher.update(chunk)
                            digest = hasher.hexdigest()
                            matches = [
                                row
                                for row in included
                                if any(
                                    name == item or name.endswith("/" + item)
                                    for item in row["paths"]
                                )
                            ]
                            if not matches:
                                errors.append(f"unlisted image asset: {name}")
                            for row in matches:
                                seen.add(row["id"])
                                if (
                                    row["download"] != "included"
                                    or row["id"] not in document["image_inventory"]
                                ):
                                    errors.append(
                                        f"{name}: image asset is not marked bundled"
                                    )
                                if digest != row["sha256"]:
                                    errors.append(f"{name}: SHA-256 mismatch")
                                if "size" in row and member.size != row["size"]:
                                    errors.append(f"{name}: size differs from record")
                            assets.append(
                                {
                                    "path": name,
                                    "extension": PurePosixPath(name).suffix,
                                    "size": member.size,
                                    "sha256": digest,
                                    "license": matches[0]["license"]
                                    if matches
                                    else "UNLISTED",
                                }
                            )
                        else:
                            data = content.read().decode("utf-8", errors="replace")
                            if metadata:
                                message = Parser().parsestr(data)
                                package = (
                                    str(message.get("Name", "unknown"))
                                    .lower()
                                    .replace("_", "-")
                                )
                                license_value = str(
                                    message.get("License-Expression")
                                    or message.get("License")
                                    or "NOASSERTION"
                                ).splitlines()[0]
                                packages.add((package, license_value))
                                installed[package] = str(message.get("Version", ""))
                                if (
                                    package
                                    in {
                                        "piper-tts",
                                        "onnxruntime",
                                        "numpy",
                                        "onnx-asr",
                                        "pathvalidate",
                                    }
                                    and "apache" in license_value.lower()
                                ):
                                    errors.append(
                                        f"{package}: third-party Apache-2.0 claim"
                                        " in metadata"
                                    )
                            elif name == "app/wheel-provenance.json":
                                origins = {row["name"]: row for row in json.loads(data)}
                            elif not name.endswith(
                                "assets/manifest.json"
                            ) and apache_claims(data, records):
                                errors.append(f"{name}: third-party Apache-2.0 claim")
            for entry in document.get("build_plan", []):
                package = entry["package"]
                origin = origins.get(package, {})
                if (
                    installed.get(package) != entry["version"]
                    or origin.get("version") != entry["version"]
                    or not any(
                        wheel["url"] == origin.get("url")
                        and wheel["hash"] == "sha256:" + str(origin.get("sha256"))
                        for wheel in entry["wheels"]
                    )
                ):
                    errors.append(f"{package}: build-time wheel provenance mismatch")
                else:
                    seen.add(entry["id"])
            for row in included:
                if str(row["id"]).startswith("bundled-"):
                    package = row["package"]["name"]
                    origin = origins.get(package, {})
                    source = document["wheel_sources"][package]
                    if installed.get(package) != row["package"]["version"] or not any(
                        wheel["url"] == origin.get("url")
                        and wheel["hash"] == "sha256:" + str(origin.get("sha256"))
                        for wheel in source["wheels"]
                    ):
                        errors.append(
                            f"{package}: bundled-file wheel provenance mismatch"
                        )
                if row["download"] == "included" and row["id"] not in seen:
                    errors.append(f"{row['id']}: bundled asset missing from image")
    inventory = {
        "platforms": sorted(set(platforms)),
        "assets": sorted(assets, key=lambda row: row["path"]),
        "licenses": sorted(
            [{"name": name, "license": value} for name, value in packages],
            key=lambda row: row["name"],
        ),
    }
    return list(dict.fromkeys(errors)), inventory


def check_pypi(version: str) -> list[str]:
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:(?:a|b|rc)\d+)?", version):
        return ["invalid PyPI version"]
    try:
        with urlopen(
            f"https://pypi.org/pypi/assist-live-client/{version}/json", timeout=20
        ) as response:
            document = json.load(response)
        if (
            document["info"]["name"] != "assist-live-client"
            or document["info"]["version"] != version
        ):
            return ["PyPI version metadata differs"]
        if not any(
            row["filename"].endswith(".whl") and not row.get("yanked", False)
            for row in document["urls"]
        ):
            return ["PyPI version has no available wheel"]
    except HTTPError as error:
        error.close()
        return ["required client version is not available on PyPI"]
    except OSError, ValueError, KeyError, TypeError:
        return ["required client version is not available on PyPI"]
    return []


class ReleaseParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        print("FAIL release: invalid command arguments")
        raise SystemExit(2)


def main(argv: list[str] | None = None) -> int:
    parser = ReleaseParser()
    parser.add_argument("--wheel", type=Path)
    parser.add_argument("--image", type=Path)
    parser.add_argument("--assets", type=Path, default=ROOT / "assets/manifest.json")
    parser.add_argument("--inventory", type=Path)
    parser.add_argument("--compare-inventory", type=Path)
    parser.add_argument("--expect-platform")
    parser.add_argument("--pypi-version")
    parser.add_argument("--require-published", action="store_true")
    parser.add_argument("--catalog-installed", action="store_true")
    parser.add_argument("--write-provenance", type=Path)
    parser.add_argument("--lock", type=Path, default=ROOT / "uv.lock")
    args = parser.parse_args(argv)
    errors: list[str] = []
    try:
        document = json.loads(args.assets.read_text())
        if args.write_provenance:
            args.write_provenance.write_text(
                json.dumps(provenance(args.lock), indent=2) + "\n"
            )
            print("PASS release: installed wheel provenance recorded")
            return 0
        if args.catalog_installed:
            catalog_installed(document, args.lock)
            args.assets.write_text(json.dumps(document, indent=2) + "\n")
            print("PASS release: bundled package records generated")
            return 0
        errors.extend(check_records(document))
        errors.extend(check_assets(args.assets, release=True))
        if args.wheel:
            wheels = (
                list(args.wheel.glob("assist_live_client-*.whl"))
                if args.wheel.is_dir()
                else [args.wheel]
            )
            if len(wheels) != 1:
                errors.append("exactly one client wheel required")
            else:
                failures, dependencies = check_wheel(wheels[0])
                errors.extend(failures)
                print("Client dependencies: " + json.dumps(dependencies))
        if args.image:
            failures, inventory = check_image(args.image, document)
            errors.extend(failures)
            for row in inventory["assets"]:
                print("Image asset: " + json.dumps(row, sort_keys=True))
            print("Image platforms: " + ", ".join(inventory["platforms"]))
            print(
                "Image licenses: " + json.dumps(inventory["licenses"], sort_keys=True)
            )
            if args.expect_platform and inventory["platforms"] != [
                args.expect_platform
            ]:
                errors.append("image platform differs from requested platform")
            if (
                args.compare_inventory
                and json.loads(args.compare_inventory.read_text())["licenses"]
                != inventory["licenses"]
            ):
                errors.append("platform license inventories differ")
            if args.inventory and not errors:
                args.inventory.write_text(json.dumps(inventory, indent=2) + "\n")
        if args.require_published and not args.pypi_version:
            errors.append("--require-published needs --pypi-version")
        if args.pypi_version:
            errors.extend(check_pypi(args.pypi_version))
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        tarfile.TarError,
        zipfile.BadZipFile,
    ) as error:
        errors.append(f"invalid release input ({type(error).__name__})")
    for failure in dict.fromkeys(errors):
        print("FAIL release: " + " ".join(failure.splitlines()))
    if errors:
        return 1
    print("PASS release checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

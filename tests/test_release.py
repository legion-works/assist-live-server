import copy
import hashlib
import importlib.util
import io
import json
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_service_and_app_users_match_their_data_mounts():
    service = (ROOT / "Dockerfile").read_text()
    assert service.rsplit("FROM runtime AS service", 1)[1].strip() == "USER 10001:10001"
    assert (
        "USER root"
        in service.split("FROM runtime AS app")[1].split("FROM runtime AS service")[0]
    )


def test_runtime_stage_keeps_base_intact_and_copies_no_build_tools():
    import re

    runtime = (ROOT / "Dockerfile").read_text().split(" AS runtime\n", 1)[1]
    assert not re.search(r"\brm\b|\bunlink\b|shutil\.rmtree", runtime)
    copies = [
        line for line in runtime.splitlines() if line.startswith("COPY --from=build ")
    ]
    assert copies == [
        "COPY --from=build /build/.venv /build/.venv",
        "COPY --from=build /build/wheel-provenance.json /app/wheel-provenance.json",
    ]
    assert not re.search(r"RUN .*\b(?:pip|uv|gcc|g\+\+|clang|make|cmake)\b", runtime)


def test_app_has_no_ha_api_access():
    config = yaml.safe_load((ROOT / "assist-live-server/config.yaml").read_text())
    assert "homeassistant_api" not in config
    assert "hassio_api" not in config
    assert not config.get("host_network", False)
    assert not config.get("privileged")
    assert set(config["ports"]) == {"8099/tcp"}
    assert set(config["arch"]) == {"amd64", "aarch64"}
    assert "init" not in config
    assert config["stage"] == "experimental"


def test_app_repository_layout_has_one_config_and_no_duplicate_dockerfile():
    from tools.check_assets import release_files

    config = yaml.safe_load((ROOT / "assist-live-server/config.yaml").read_text())
    assert (ROOT / "repository.yaml").is_file()
    configs = sorted(
        path.relative_to(ROOT).as_posix()
        for path in release_files(ROOT)
        if path.name in {"config.json", "config.yaml", "config.yml"}
    )
    assert configs == [f"{config['slug']}/config.yaml"]
    assert not (ROOT / config["slug"] / "Dockerfile").exists()


def test_runtime_image_disables_telemetry():
    runtime = (ROOT / "Dockerfile").read_text().split(" AS runtime\n", 1)[1]
    assert "ENV ORT_DISABLE_TELEMETRY=1" in runtime


def checker():
    spec = importlib.util.spec_from_file_location(
        "check_release", ROOT / "tools/check_release.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def asset_document():
    digest = hashlib.sha256(b"voice weights").hexdigest()
    pin = "a" * 40
    record = {
        "id": "voice",
        "kind": "voice",
        "status": "included",
        "url": f"https://example.com/{pin}/voice.onnx",
        "revision": pin,
        "sha256": digest,
        "license": "NOASSERTION",
        "redistribution": True,
        "download": "included",
        "attribution": "Voice source attribution.",
        "paths": ["models/voice.onnx"],
        "components": [],
        "license_sources": [
            {
                "kind": "model_card",
                "url": f"https://example.com/{pin}/card",
                "quote": "No separate model-weight license is stated.",
                "revision_or_version": pin,
                "file_binding": {"path": "voice.onnx", "sha256": digest},
            }
        ],
    }
    return {"assets": [record], "image_inventory": ["voice"], "download_plan": []}


def tar_bytes(files):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        for name, data in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    return output.getvalue()


def image_tar(tmp_path, files=None, labels=None, oci=False):
    layer = tar_bytes(files or {"models/voice.onnx": b"voice weights"})
    config = json.dumps(
        {
            "architecture": "amd64",
            "os": "linux",
            "config": {
                "User": "10001:10001",
                "Labels": labels
                or {
                    "org.opencontainers.image.licenses": (
                        "Project code: Apache-2.0; third-party components: "
                        "see /app/THIRD_PARTY_NOTICES.md"
                    )
                },
            },
        }
    ).encode()
    if oci:

        def blob(data):
            return "blobs/sha256/" + hashlib.sha256(data).hexdigest()

        manifest = json.dumps(
            {
                "schemaVersion": 2,
                "config": {"digest": "sha256:" + hashlib.sha256(config).hexdigest()},
                "layers": [{"digest": "sha256:" + hashlib.sha256(layer).hexdigest()}],
            }
        ).encode()
        index = json.dumps(
            {
                "manifests": [
                    {
                        "digest": "sha256:" + hashlib.sha256(manifest).hexdigest(),
                        "platform": {"os": "linux", "architecture": "amd64"},
                    }
                ]
            }
        ).encode()
        contents = {
            "index.json": index,
            blob(manifest): manifest,
            blob(config): config,
            blob(layer): layer,
        }
    else:
        manifest = json.dumps(
            [{"Config": "config.json", "Layers": ["layer.tar"]}]
        ).encode()
        contents = {
            "manifest.json": manifest,
            "config.json": config,
            "layer.tar": layer,
        }
    path = tmp_path / "image.tar"
    path.write_bytes(tar_bytes(contents))
    return path


@pytest.mark.parametrize("oci", [False, True])
def test_image_accepts_recorded_noassertion_voice(tmp_path, oci):
    errors, inventory = checker().check_image(
        image_tar(tmp_path, oci=oci), asset_document()
    )
    assert errors == []
    assert inventory["platforms"] == ["linux/amd64"]
    assert inventory["assets"][0]["size"] == 13
    assert inventory["assets"][0]["license"] == "NOASSERTION"


@pytest.mark.parametrize(
    "mutation, expected",
    [
        ("missing_record", "unlisted image asset"),
        ("wrong_hash", "SHA-256 mismatch"),
        ("missing_quote", "quote required"),
        ("missing_attribution", "attribution required"),
        ("unlisted", "unlisted image asset"),
        ("apache_label", "project-scoped license label required"),
        ("apache_file", "third-party Apache-2.0 claim"),
    ],
)
def test_release_record_mutations_fail(tmp_path, mutation, expected):
    document = asset_document()
    files = {"models/voice.onnx": b"voice weights"}
    labels = None
    if mutation == "missing_record":
        document["assets"] = []
    elif mutation == "wrong_hash":
        files["models/voice.onnx"] = b"wrong weights"
    elif mutation == "missing_quote":
        document["assets"][0]["license_sources"][0]["quote"] = ""
    elif mutation == "missing_attribution":
        document["assets"][0]["attribution"] = ""
    elif mutation == "unlisted":
        files["models/hidden.safetensors"] = b"unlisted"
    elif mutation == "apache_label":
        labels = {"org.opencontainers.image.licenses": "Apache-2.0"}
    elif mutation == "apache_file":
        files["app/component-license.txt"] = b"voice: Apache-2.0\n"
    errors, _ = checker().check_image(image_tar(tmp_path, files, labels), document)
    assert any(expected in error for error in errors), errors


def test_download_plan_requires_selected_exact_pins():
    document = asset_document()
    row = document["assets"][0]
    row["download"] = "startup"
    document["image_inventory"] = []
    document["download_plan"] = [
        {
            "id": "voice",
            "url": row["url"],
            "sha256": row["sha256"],
            "path": "models/voice.onnx",
        }
    ]
    assert checker().check_records(document) == []
    bad = copy.deepcopy(document)
    bad["download_plan"][0]["sha256"] = "0" * 64
    assert any("download-plan" in error for error in checker().check_records(bad))


def test_wheel_is_model_free_and_reports_dependencies(tmp_path):
    wheel = tmp_path / "assist_live_client-0.0.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("assist_live_client/__init__.py", "")
        archive.writestr(
            "assist_live_client-0.0.0.dist-info/METADATA",
            "Name: assist-live-client\nRequires-Dist: aiohttp<4,>=3.14.4\n",
        )
    assert checker().check_wheel(wheel) == ([], ["aiohttp<4,>=3.14.4"])
    with zipfile.ZipFile(wheel, "a") as archive:
        archive.writestr("assist_live_client/hidden.onnx", b"weights")
    assert checker().check_wheel(wheel)[0]


def test_cli_failure_has_named_lines_without_traceback(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools/check_release.py"),
            "--image",
            str(tmp_path / "missing.tar"),
            "--assets",
            str(ROOT / "assets/manifest.json"),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "FAIL release:" in result.stdout
    assert "Traceback" not in result.stdout + result.stderr


def test_pypi_requires_exact_available_version(monkeypatch):
    module = checker()

    class Response(io.BytesIO):
        pass

    monkeypatch.setattr(
        module,
        "urlopen",
        lambda *args, **kwargs: Response(
            b'{"info":{"name":"assist-live-client","version":"1.2.3"},"urls":[{"filename":"assist_live_client-1.2.3-py3-none-any.whl","yanked":false}]}'
        ),
    )
    assert module.check_pypi("1.2.3") == []
    assert module.check_pypi("1.2.4")


@pytest.mark.parametrize("bad", [False, True])
def test_build_sources_require_matching_installed_wheel(tmp_path, bad):
    document = asset_document()
    url = "https://example.com/runtime-1.0-py3-none-any.whl"
    document["build_plan"] = [
        {
            "id": "runtime",
            "package": "runtime",
            "version": "1.0",
            "wheels": [{"url": url, "hash": "sha256:" + "a" * 64}],
        }
    ]
    files = {
        "models/voice.onnx": b"voice weights",
        "app/wheel-provenance.json": json.dumps(
            [
                {
                    "name": "runtime",
                    "version": "1.0",
                    "url": url,
                    "sha256": ("b" if bad else "a") * 64,
                }
            ]
        ).encode(),
        "venv/runtime-1.0.dist-info/METADATA": (
            b"Name: runtime\nVersion: 1.0\nLicense: MIT\n"
        ),
    }
    errors, _ = checker().check_image(image_tar(tmp_path, files), document)
    assert bool(errors) is bad, errors


def test_catalog_is_deterministic_and_keeps_package_file_digests():
    module = checker()
    document = json.loads((ROOT / "assets/manifest.json").read_text())
    first = module.catalog_installed(copy.deepcopy(document), ROOT / "uv.lock")
    second = module.catalog_installed(copy.deepcopy(first), ROOT / "uv.lock")
    assert first == second
    fbanks = next(
        row
        for row in first["assets"]
        if row.get("package", {}).get("member")
        == "onnx_asr/preprocessors/data/fbanks.npz"
    )
    assert (
        fbanks["sha256"]
        == "7f3e3bd26f90946c5f91a3f60af6f4c5c2b07a94f2fb2da91bb325b9d26feb73"
    )
    assert fbanks["size"] == 19576 and fbanks["runtime_used"] is True
    assert (
        len([row for row in first["assets"] if row["id"].startswith("bundled-")]) >= 32
    )


def test_existing_package_navigation_does_not_become_wheel_provenance(tmp_path):
    document = asset_document()
    document["assets"][0]["package"] = {
        "version": "1.0",
        "url": "https://example.com/package/1.0",
    }
    assert checker().check_image(image_tar(tmp_path), document)[0] == []


def test_wheel_refuses_model_runtime_dependency(tmp_path):
    wheel = tmp_path / "client.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(
            "assist_live_client-0.0.0.dist-info/METADATA",
            "Name: assist-live-client\nRequires-Dist: aiohttp\nRequires-Dist: numpy\n",
        )
    assert checker().check_wheel(wheel)[0] == [
        "client wheel dependencies must contain only aiohttp"
    ]


def test_pypi_yanked_wheel_is_not_available(monkeypatch):
    module = checker()
    monkeypatch.setattr(
        module,
        "urlopen",
        lambda *args, **kwargs: io.BytesIO(
            b'{"info":{"name":"assist-live-client","version":"1.2.3"},"urls":[{"filename":"client.whl","yanked":true}]}'
        ),
    )
    assert module.check_pypi("1.2.3") == ["PyPI version has no available wheel"]


def test_image_refuses_model_symlink_without_reading_target(tmp_path):
    link_layer = io.BytesIO()
    with tarfile.open(fileobj=link_layer, mode="w") as layer:
        link = tarfile.TarInfo("hidden.onnx")
        link.type = tarfile.SYMTYPE
        link.linkname = "../../outside"
        layer.addfile(link)
    config = json.dumps(
        {
            "os": "linux",
            "architecture": "amd64",
            "config": {
                "Labels": {"org.opencontainers.image.licenses": checker().PROJECT_LABEL}
            },
        }
    ).encode()
    path = tmp_path / "link-image.tar"
    path.write_bytes(
        tar_bytes(
            {
                "manifest.json": b'[{"Config":"config.json","Layers":["layer.tar"]}]',
                "config.json": config,
                "layer.tar": link_layer.getvalue(),
            }
        )
    )
    errors, _ = checker().check_image(
        path, {"assets": [], "download_plan": [], "image_inventory": []}
    )
    assert any("model asset must be a regular file" in error for error in errors)


def test_bundled_model_cannot_be_deleted_in_a_later_layer(tmp_path):
    document = asset_document()
    image = image_tar(tmp_path)
    with tarfile.open(image) as archive:
        config = archive.extractfile("config.json").read()
        layer = archive.extractfile("layer.tar").read()
    image.write_bytes(
        tar_bytes(
            {
                "manifest.json": json.dumps(
                    [{"Config": "config.json", "Layers": ["layer.tar", "delete.tar"]}]
                ).encode(),
                "config.json": config,
                "layer.tar": layer,
                "delete.tar": tar_bytes({"models/.wh.voice.onnx": b""}),
            }
        )
    )
    errors, _ = checker().check_image(image, document)
    assert any("bundled asset removed" in error for error in errors)


def test_cli_argument_error_is_named_without_stderr():
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools/check_release.py"), "--unknown"],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert result.stdout == "FAIL release: invalid command arguments\n"
    assert result.stderr == ""

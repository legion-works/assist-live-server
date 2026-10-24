import json
from copy import deepcopy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def asset():
    return {
        "id": "test-voice",
        "kind": "voice",
        "status": "confirmed_included",
        "url": "https://example.com/models/" + "a" * 40 + "/voice.bin",
        "revision": "a" * 40,
        "sha256": "b" * 64,
        "license": "CC-BY-4.0",
        "license_sources": [
            {
                "kind": "model_card",
                "url": "https://example.com/models/" + "a" * 40 + "/README.md",
            }
        ],
        "redistribution": True,
        "download": "startup",
        "attribution": "Synthetic test voice. CC-BY-4.0.",
        "paths": ["models/test-voice.bin"],
        "components": [],
    }


def write_manifest(root, records, **fields):
    path = root / "manifest.json"
    path.write_text(
        json.dumps(
            {
                "schema": 1,
                "release_root": ".",
                "assets": records,
                "image_inventory": [],
                "download_plan": ["test-voice"] if records else [],
                **fields,
            }
        )
    )
    return path


def test_unknown_or_nc_asset_blocks_release(tmp_path):
    from tools.check_assets import check_assets

    path = tmp_path / "assets.json"
    for license_id in ("unknown", "CC-BY-NC-4.0"):
        path.write_text(json.dumps([{"id": "test-voice", "license": license_id}]))
        assert check_assets(path, release=True)


def test_piper_runtime_blocks_apache_image():
    from tools.check_assets import check_assets

    assert check_assets(ROOT / "tests/fixtures/assets/piper-gpl.json", release=True)


def test_unknown_fixture_blocks_release():
    from tools.check_assets import check_assets

    assert check_assets(ROOT / "tests/fixtures/assets/unknown.json", release=True)


def test_bootstrap_without_provider_accepts_empty_included_assets():
    from tools.check_assets import check_assets

    assert not check_assets(ROOT / "assets/manifest.json", release=True)


def test_valid_asset_and_bare_list(tmp_path):
    from tools.check_assets import check_assets

    path = write_manifest(tmp_path, [asset()])
    assert not check_assets(path, release=True)
    path.write_text(json.dumps([asset()]))
    assert not check_assets(path, release=True)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("sha256", None),
        ("license", "CC-BY-NC-4.0"),
        ("license", "CC-BY-ND-4.0"),
        ("license", "GPL-3.0-only"),
        ("license", "LGPL-2.1-only"),
        ("license", "NOASSERTION"),
        ("license_sources", []),
        (
            "license_sources",
            [{"kind": "model_card", "url": "https://img.shields.io/badge/MIT"}],
        ),
        ("revision", "main"),
        ("redistribution", False),
        ("attribution", ""),
        ("components", None),
    ],
)
def test_included_metadata_is_required(tmp_path, field, value):
    from tools.check_assets import check_assets

    record = asset()
    record[field] = value
    assert check_assets(write_manifest(tmp_path, [record]), release=True)


@pytest.mark.parametrize("download", ["included", "startup"])
def test_gpl_runtime_in_either_delivery_path(tmp_path, download):
    from tools.check_assets import check_assets

    record = asset()
    record.update(id="piper-runtime", license="GPL-3.0-only", download=download)
    assert any(
        "license" in error
        for error in check_assets(
            write_manifest(tmp_path, [record], download_plan=[]), release=True
        )
    )


def test_transitive_phonemizer_must_pass_same_policy(tmp_path):
    from tools.check_assets import check_assets

    record = asset()
    child = deepcopy(record)
    child.update(id="phonemizer", kind="phonemizer", license="GPL-3.0-only")
    record["components"] = [child]
    assert check_assets(write_manifest(tmp_path, [record]), release=True)


def test_provider_signal_refuses_empty_included_manifest(tmp_path):
    from tools.check_assets import check_assets

    path = write_manifest(tmp_path, [])
    assert not check_assets(path, release=True)
    providers = tmp_path / "server/assist_live_server/providers"
    providers.mkdir(parents=True)
    (providers / "__init__.py").write_text("")
    assert not check_assets(path, release=True)
    (providers / "local.py").write_text("")
    assert any("empty" in error for error in check_assets(path, release=True))


@pytest.mark.parametrize("location", ["tree", "image", "download", "source"])
def test_blocked_absent_checks_three_inputs(tmp_path, location):
    from tools.check_assets import check_assets

    document = json.loads(
        (ROOT / "tests/fixtures/assets/blocked-absent.json").read_text()
    )
    path = write_manifest(tmp_path, document["assets"], download_plan=[])
    assert not check_assets(path, release=True)
    if location == "tree":
        (tmp_path / "piper.bin").write_bytes(b"runtime")
    elif location == "source":
        (tmp_path / "server").mkdir()
        (tmp_path / "server/load.py").write_text("download('piper-runtime')")
    else:
        document = json.loads(path.read_text())
        document["image_inventory" if location == "image" else "download_plan"] = [
            "piper-runtime"
        ]
        path.write_text(json.dumps(document))
    assert check_assets(path, release=True)


def test_inventory_and_download_cannot_hide_unlisted_assets(tmp_path):
    from tools.check_assets import check_assets

    for field in ("image_inventory", "download_plan"):
        path = write_manifest(tmp_path, [], **{field: ["unlisted"]})
        assert check_assets(path, release=True)


def test_release_tree_cannot_hide_unlisted_model(tmp_path):
    from tools.check_assets import check_assets

    path = write_manifest(tmp_path, [])
    assert not check_assets(path, release=True)
    (tmp_path / "unlisted.onnx").write_bytes(b"model")
    assert check_assets(path, release=True)


def test_local_artifact_hash_is_checked(tmp_path):
    from tools.check_assets import check_assets

    record = asset()
    (tmp_path / "models").mkdir()
    (tmp_path / "models/test-voice.bin").write_bytes(b"changed")
    assert any(
        "mismatch" in error
        for error in check_assets(write_manifest(tmp_path, [record]), release=True)
    )


def test_missing_image_is_explicit_and_blocks_configured_release(tmp_path, capsys):
    from tools.check_assets import check_assets, main

    path = write_manifest(tmp_path, [asset()], image_inventory=None)
    assert not check_assets(path, release=True)
    assert main(["--manifest", str(path), "--release"]) == 0
    assert "NOT CHECKED image inventory" in capsys.readouterr().out
    providers = tmp_path / "server/assist_live_server/providers"
    providers.mkdir(parents=True)
    (providers / "local.py").write_text("")
    assert check_assets(path, release=True)


def test_malformed_inputs_return_errors(tmp_path):
    from tools.check_assets import check_assets

    for value in ("{", "null", '{"assets": 3}', "[null]", '[{"id": []}]'):
        path = tmp_path / "manifest.json"
        path.write_text(value)
        assert check_assets(path, release=True)

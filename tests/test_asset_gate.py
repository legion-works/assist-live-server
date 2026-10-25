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
                "quote": "Synthetic test voice uses CC-BY-4.0.",
                "revision_or_version": "a" * 40,
                "file_binding": {"path": "voice.bin", "sha256": "b" * 64},
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


@pytest.mark.parametrize("status", ["confirmed_included", "confirmed_blocked_absent"])
@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize(
    "missing",
    ["license_sources", "quote", "url", "revision_or_version", "file_binding"],
)
def test_every_source_field_is_required_in_both_decisions(
    tmp_path, status, nested, missing
):
    from tools.check_assets import check_assets, main

    record = asset()
    target = deepcopy(record) if nested else record
    if nested:
        target["id"] = "child-asset"
        record["components"] = [target]
    target.update(status=status, download="none" if "blocked" in status else "startup")
    if missing == "license_sources":
        del target[missing]
    else:
        del target["license_sources"][0][missing]
    path = write_manifest(tmp_path, [record], download_plan=[])
    errors = check_assets(path, release=True)
    assert any(target["id"] in e and "license source" in e for e in errors)
    assert main(["--manifest", str(path), "--release"]) == 1


@pytest.mark.parametrize("field", ["evidence", "license_claims"])
@pytest.mark.parametrize("nested", [False, True])
def test_parallel_rights_lists_are_rejected_by_the_shared_gate(tmp_path, field, nested):
    from tools.check_assets import check_assets

    record = asset()
    target = deepcopy(record) if nested else record
    if nested:
        target["id"] = "nested"
        record["components"] = [target]
    target[field] = []
    assert any(
        field in e for e in check_assets(write_manifest(tmp_path, [record]), True)
    )


@pytest.mark.parametrize("binding", [None, {"path": "voice.bin", "sha256": "c" * 64}])
def test_included_source_must_bind_the_downloaded_file_digest(tmp_path, binding):
    from tools.check_assets import check_assets

    record = asset()
    source = record["license_sources"][0]
    if binding is None:
        del source["file_binding"]
        source["scope"] = "Repository code only, not model weights."
    else:
        source["file_binding"] = binding
    assert any(
        "file binding" in e
        for e in check_assets(write_manifest(tmp_path, [record]), True)
    )


@pytest.mark.parametrize("value", [None, "", "   "])
@pytest.mark.parametrize("status", ["confirmed_included", "confirmed_blocked_absent"])
def test_empty_license_quote_is_a_named_failure(tmp_path, capsys, value, status):
    from tools.check_assets import main

    record = asset()
    record.update(status=status, download="none" if "blocked" in status else "startup")
    record["license_sources"][0]["quote"] = value
    path = write_manifest(tmp_path, [record], download_plan=[])
    assert main(["--manifest", str(path), "--release"]) == 1
    assert (
        "FAIL asset licenses: test-voice: license source quote required"
        in capsys.readouterr().out
    )


@pytest.mark.parametrize("packages", [None, {}, [None]])
def test_malformed_supporting_sources_return_named_failures(tmp_path, capsys, packages):
    from tools.check_assets import main

    record = asset()
    record["inspection"] = {"supporting_packages": packages}
    path = write_manifest(tmp_path, [record])
    assert main(["--manifest", str(path), "--release"]) == 1
    assert (
        "FAIL asset licenses: test-voice: supporting package" in capsys.readouterr().out
    )


@pytest.mark.parametrize(
    "probe", ["probe_smart_turn", "probe_kokoro_licenses", "probe_silero_license"]
)
def test_every_probe_renders_all_manifest_records(probe):
    from importlib import import_module

    from tools.check_assets import flatten
    from tools.probe_kokoro_licenses import permitted

    module = import_module("tools." + probe)
    records = flatten(
        json.loads((ROOT / "assets/manifest.json").read_text())["assets"], []
    )
    for record in records:
        text = module.render_report(record)
        assert (
            record["sha256"] in text if record.get("sha256") else record["id"] in text
        )
        assert record["status"] in text
        assert permitted(record) == (record["status"] == "confirmed_included")

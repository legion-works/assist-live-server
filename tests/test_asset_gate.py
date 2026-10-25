import json
from copy import deepcopy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def asset(license_id="CC-BY-4.0"):
    return {
        "id": "test-voice",
        "kind": "voice",
        "status": "included",
        "url": "https://example.com/models/" + "a" * 40 + "/voice.bin",
        "revision": "a" * 40,
        "sha256": "b" * 64,
        "license": license_id,
        "license_sources": [
            {
                "kind": "model_card",
                "url": "https://example.com/models/" + "a" * 40 + "/README.md",
                "quote": f"Synthetic test voice uses {license_id}.",
                "revision_or_version": "a" * 40,
                "file_binding": {"path": "voice.bin", "sha256": "b" * 64},
            }
        ],
        "redistribution": True,
        "download": "startup",
        "attribution": f"Synthetic test voice. {license_id}.",
        "paths": ["models/test-voice.bin"],
        "components": [],
    }


def write_manifest(root, records, **fields):
    (root / "THIRD_PARTY_NOTICES.md").write_text(
        "\n".join(
            f"{item['id']}: {item.get('attribution', '')}"
            for item in [
                *records,
                *(c for r in records for c in r.get("components", []) or []),
            ]
        )
    )
    path = root / "manifest.json"
    path.write_text(
        json.dumps(
            {
                "schema": 1,
                "release_root": ".",
                "assets": records,
                "image_inventory": [],
                "download_plan": [
                    download_entry(item)
                    for item in records
                    if item.get("status") == "included"
                ],
                **fields,
            }
        )
    )
    return path


def test_incomplete_unknown_or_nc_records_fail(tmp_path):
    from tools.check_assets import check_assets

    path = tmp_path / "assets.json"
    for license_id in ("unknown", "CC-BY-NC-4.0"):
        path.write_text(json.dumps([{"id": "test-voice", "license": license_id}]))
        assert check_assets(path, release=True)


def test_incomplete_piper_runtime_record_fails():
    from tools.check_assets import check_assets

    assert check_assets(ROOT / "tests/fixtures/assets/piper-gpl.json", release=True)


def test_unknown_fixture_blocks_release():
    from tools.check_assets import check_assets

    assert check_assets(ROOT / "tests/fixtures/assets/unknown.json", release=True)


def test_bootstrap_without_provider_accepts_empty_included_assets():
    from tools.check_assets import check_assets

    assert not check_assets(ROOT / "assets/manifest.json", release=True)


def test_valid_asset_and_bare_list_requires_download_plan(tmp_path):
    from tools.check_assets import check_assets

    path = write_manifest(tmp_path, [asset()])
    assert not check_assets(path, release=True)
    path.write_text(json.dumps([asset()]))
    assert any("download plan required" in error for error in check_assets(path, True))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("sha256", None),
        ("license", "not-a-license"),
        ("license", ""),
        ("license_sources", []),
        (
            "license_sources",
            [{"kind": "model_card", "url": "https://img.shields.io/badge/MIT"}],
        ),
        ("revision", "main"),
        ("redistribution", None),
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

    record = asset("GPL-3.0-only")
    record.update(
        id="piper-runtime", kind="runtime", license="GPL-3.0-only", download=download
    )
    path = write_manifest(
        tmp_path,
        [record],
        download_plan=["piper-runtime"] if download == "startup" else [],
        image_inventory=["piper-runtime"] if download == "included" else [],
    )
    assert check_assets(path, release=True) == []


def test_transitive_phonemizer_must_pass_same_policy(tmp_path):
    from tools.check_assets import check_assets

    record = asset()
    child = deepcopy(record)
    child.update(id="phonemizer", kind="phonemizer", license="GPL-3.0-only")
    record["components"] = [child]
    assert (
        check_assets(
            write_manifest(
                tmp_path, [record], download_plan=[download_entry(record), "phonemizer"]
            ),
            release=True,
        )
        == []
    )


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
def test_not_selected_checks_three_inputs(tmp_path, location):
    from tools.check_assets import check_assets

    document = json.loads(
        (ROOT / "tests/fixtures/assets/not-selected.json").read_text()
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


@pytest.mark.parametrize("status", ["included", "not_selected"])
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
    target.update(
        status=status,
        download="none" if status == "not_selected" else "startup",
        reason="technical",
    )
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
@pytest.mark.parametrize("status", ["included", "not_selected"])
def test_empty_license_quote_is_a_named_failure(tmp_path, capsys, value, status):
    from tools.check_assets import main

    record = asset()
    record.update(
        status=status,
        download="none" if status == "not_selected" else "startup",
        reason="technical",
    )
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
        assert permitted(record) == (record["status"] == "included")


@pytest.mark.parametrize("license_id", ["GPL-3.0-or-later", "CC-BY-NC-4.0"])
def test_license_value_does_not_prevent_recorded_asset_selection(tmp_path, license_id):
    from tools.check_assets import check_assets

    record = asset(license_id)
    assert check_assets(write_manifest(tmp_path, [record]), True) == []


@pytest.mark.parametrize("license_id", ["GPL-3.0-or-later", "CC-BY-NC-4.0"])
@pytest.mark.parametrize("missing", ["quote", "binding", "notice"])
def test_selected_asset_requires_quote_binding_and_notice(
    tmp_path, license_id, missing
):
    from tools.check_assets import check_assets

    record = asset(license_id)
    if missing == "quote":
        record["license_sources"][0]["quote"] = ""
    elif missing == "binding":
        source = record["license_sources"][0]
        del source["file_binding"]
        source["scope"] = "Repository code only."
    path = write_manifest(tmp_path, [record])
    if missing == "notice":
        (tmp_path / "THIRD_PARTY_NOTICES.md").write_text("Unrelated component.")
    errors = check_assets(path, True)
    expected = {
        "quote": "quote required",
        "binding": "file binding",
        "notice": "notice",
    }
    assert any(expected[missing] in error for error in errors)


def test_immutable_owner_comment_can_bind_a_pinned_model(tmp_path):
    from tools.check_assets import check_assets

    record = asset("MIT")
    source = record["license_sources"][0]
    source.update(
        kind="owner_statement",
        url="https://github.com/snakers4/silero-vad/issues/816#issuecomment-6393620007",
        revision_or_version="v6.2.3",
        quote="MIT",
    )
    assert check_assets(write_manifest(tmp_path, [record]), True) == []


def test_public_source_rendering_has_no_python_dict_syntax():
    from tools.asset_rights import render_sources

    text = render_sources(asset())
    assert "{'path':" not in text
    assert "voice.bin" in text
    assert "b" * 64 in text


def test_download_plan_file_uses_the_same_digest_binding_rule(tmp_path):
    from tools.check_assets import check_assets

    record = asset()
    (tmp_path / "downloads.json").write_text(json.dumps([download_entry(record)]))
    path = write_manifest(tmp_path, [record], download_plan="downloads.json")
    assert check_assets(path, True) == []
    source = record["license_sources"][0]
    del source["file_binding"]
    source["scope"] = "Code only, not the downloaded voice."
    path = write_manifest(tmp_path, [record], download_plan="downloads.json")
    assert any("exact file binding" in error for error in check_assets(path, True))


@pytest.mark.parametrize("reason", [None, "", "technical cause"])
def test_not_selected_requires_a_reason_code(tmp_path, reason):
    from tools.check_assets import check_assets

    record = asset()
    record.update(status="not_selected", download="none", reason=reason)
    path = write_manifest(tmp_path, [record], download_plan=[])
    assert any("reason required" in error for error in check_assets(path, True))


def test_dataset_snapshot_is_scoped_and_not_a_download_grant():
    from tools.asset_rights import source_errors

    record = asset()
    record["license_sources"] = [
        {
            "kind": "dataset_license_snapshot",
            "url": "https://example.com/dataset/license.html",
            "revision_or_version": "c" * 64,
            "quote": "Research use only.",
            "scope": "Dataset text captured by content digest, not an immutable URL.",
        }
    ]
    assert any("exact file binding" in error for error in source_errors(record))
    record.update(status="not_selected", reason="technical", download="none")
    assert source_errors(record) == []


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/example/project/issues/1",
        "https://example.com/issues/1#issuecomment-1",
    ],
)
def test_owner_statement_requires_a_comment_identifier(tmp_path, url):
    from tools.check_assets import check_assets

    record = asset()
    record["license_sources"][0].update(kind="owner_statement", url=url)
    assert any(
        "immutable revision" in error
        for error in check_assets(write_manifest(tmp_path, [record]), True)
    )


@pytest.mark.parametrize(
    "license_id",
    [
        "LicenseRef-MIT",
        "LicenseRef-Apache-2.0",
        "MIT OR LicenseRef-Unproven",
        "OpenRAIL-M",
    ],
)
def test_unrecognized_license_reference_cannot_replace_noassertion(
    tmp_path, capsys, license_id
):
    from tools.asset_rights import permitted
    from tools.check_assets import main

    record = asset(license_id)
    record["license_sources"][0]["quote"] = (
        "This model has no grant. Use at your own risk."
    )
    path = write_manifest(tmp_path, [record])
    assert main(["--manifest", str(path), "--release"]) == 1
    assert "test-voice: recognized license record required" in capsys.readouterr().out
    assert not permitted(record)


def test_noassertion_records_the_absence_of_a_model_grant(tmp_path):
    from tools.asset_rights import permitted
    from tools.check_assets import check_assets

    record = asset("NOASSERTION")
    record["license_sources"][0]["quote"] = (
        "This model has no grant. Use at your own risk."
    )
    assert check_assets(write_manifest(tmp_path, [record]), True) == []
    assert permitted(record)


def download_entry(record):
    return {
        "id": record["id"],
        "url": record["url"],
        "sha256": record["sha256"],
        "path": record["paths"][0],
    }


@pytest.mark.parametrize("kind", ["model", "voice", "tokenizer"])
@pytest.mark.parametrize("plan", [[], ["test-voice"]])
def test_model_outside_image_requires_a_pinned_download_entry(tmp_path, kind, plan):
    from tools.check_assets import check_assets

    record = asset()
    record.update(kind=kind, download="included")
    errors = check_assets(write_manifest(tmp_path, [record], download_plan=plan), True)
    assert any("pinned download-plan entry required" in error for error in errors)


def test_image_model_needs_no_download_entry(tmp_path):
    from tools.check_assets import check_assets

    record = asset()
    record.update(kind="model", download="included")
    assert (
        check_assets(
            write_manifest(
                tmp_path, [record], download_plan=[], image_inventory=[record["id"]]
            ),
            True,
        )
        == []
    )


@pytest.mark.parametrize("field", ["url", "sha256", "path"])
@pytest.mark.parametrize("value", [None, "incorrect"])
def test_download_entry_must_match_the_selected_file(tmp_path, field, value):
    from tools.check_assets import check_assets

    record = asset()
    entry = download_entry(record)
    entry[field] = value
    errors = check_assets(
        write_manifest(tmp_path, [record], download_plan=[entry]), True
    )
    assert any("download-plan entry does not match" in error for error in errors)


@pytest.mark.parametrize("as_file", [False, True])
def test_pinned_model_download_entry_is_usable_inline_or_from_file(tmp_path, as_file):
    from tools.check_assets import check_assets

    record = asset()
    plan = [download_entry(record)]
    if as_file:
        (tmp_path / "downloads.json").write_text(json.dumps(plan))
        plan = "downloads.json"
    assert (
        check_assets(write_manifest(tmp_path, [record], download_plan=plan), True) == []
    )


def test_duplicate_download_entry_is_ambiguous(tmp_path):
    from tools.check_assets import check_assets

    record = asset()
    entry = download_entry(record)
    errors = check_assets(
        write_manifest(tmp_path, [record], download_plan=[entry, entry]), True
    )
    assert any("duplicate download-plan entry" in error for error in errors)


@pytest.mark.parametrize("missing", [None, "kind", "quote", "source"])
def test_registered_custom_license_requires_its_named_exact_term(tmp_path, missing):
    from tools.asset_rights import permitted
    from tools.check_assets import check_assets

    record = asset("LicenseRef-OpenRAIL-M")
    source = {
        "kind": "LicenseRef-OpenRAIL-M",
        "url": "https://example.com/models/" + "a" * 40 + "/LICENSE",
        "revision_or_version": "a" * 40,
        "scope": "Model terms, with the restrictions in the cited full license.",
        "quote": (
            "Section II: INTELLECTUAL PROPERTY RIGHTS\n\n"
            "Both copyright and patent grants apply to the Model, Derivatives of "
            "the Model and Complementary\n"
            "Material. The Model and Derivatives of the Model are subject to "
            "additional terms as described in Section III.\n\n"
            "2. Grant of Copyright License. Subject to the terms and conditions "
            "of this License, each Contributor\n"
            "hereby grants to You a perpetual, worldwide, non-exclusive, no-charge, "
            "royalty-free, irrevocable copyright license to reproduce, prepare, "
            "publicly display, publicly perform, sublicense, and distribute the\n"
            "Complementary Material, the Model, and Derivatives of the Model."
        ),
    }
    if missing == "kind":
        source["kind"] = "license_text"
    elif missing == "quote":
        source["quote"] = "This model has no grant. Use at your own risk."
    if missing != "source":
        record["license_sources"].append(source)
    errors = check_assets(write_manifest(tmp_path, [record]), True)
    if missing is None:
        assert errors == []
        assert permitted(record)
    else:
        assert any("recognized license record required" in error for error in errors)
        assert not permitted(record)

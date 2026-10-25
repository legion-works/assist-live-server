from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_conflicting_evidence_blocks_asset():
    from tools.probe_silero_license import resolve_license

    assert resolve_license(["MIT", "CC-BY-NC-4.0"]) == "blocked"


@pytest.mark.parametrize(
    "statements",
    [
        [],
        ["MIT", ""],
        ["MIT", "Apache-2.0"],
        ["CC-BY-NC-4.0"],
        ["NOASSERTION"],
        ["mit"],
        [None],
    ],
)
def test_unknown_or_mixed_terms_do_not_select_a_permissive_license(statements):
    from tools.probe_silero_license import resolve_license

    assert resolve_license(statements) == "blocked"


def test_repeated_consistent_terms_have_a_single_result():
    from tools.probe_silero_license import resolve_license

    assert resolve_license(["MIT", "MIT"]) == "MIT"


def copied_manifest(tmp_path):
    import json

    document = json.loads((ROOT / "assets/manifest.json").read_text())
    document["release_root"] = "."
    path = tmp_path / "manifest.json"
    record = next(a for a in document["assets"] if a["id"] == "silero-vad")
    return path, document, record


def test_current_conflicting_record_is_blocked_and_requires_explicit_consent(tmp_path):
    import json

    from tools.probe_silero_license import check_decision

    path, document, record = copied_manifest(tmp_path)
    path.write_text(json.dumps(document))
    assert (
        check_decision(path, allow_blocked=True)["status"] == "confirmed_blocked_absent"
    )
    with pytest.raises(ValueError, match="--allow-blocked-absent"):
        check_decision(path, allow_blocked=False)
    record["license"] = "MIT"
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="unresolved file license"):
        check_decision(path, allow_blocked=True)


@pytest.mark.parametrize(
    "mutation", ["unknown_status", "included", "tree", "download", "image"]
)
def test_release_state_cannot_bypass_the_conflict(tmp_path, mutation):
    import json

    from tools.probe_silero_license import check_decision

    path, document, record = copied_manifest(tmp_path)
    if mutation == "unknown_status":
        record["status"] = "unknown"
    elif mutation == "included":
        record.update(
            status="confirmed_included",
            license="MIT",
            redistribution=True,
            download="startup",
        )
        document["download_plan"].append(record["id"])
    elif mutation == "tree":
        target = tmp_path / record["paths"][0]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"blocked model")
    elif mutation == "download":
        document["download_plan"].append(record["id"])
    else:
        document["image_inventory"] = [record["id"]]
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError):
        check_decision(path, allow_blocked=True)


@pytest.mark.parametrize(
    "field", ["quote", "revision_or_version", "url", "file_binding"]
)
def test_source_records_must_have_quotes_pins_and_matching_file_digest(tmp_path, field):
    import json

    from tools.probe_silero_license import check_decision

    path, document, record = copied_manifest(tmp_path)
    record["license_sources"][0][field] = ""
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="license source"):
        check_decision(path, allow_blocked=True)


@pytest.mark.parametrize("field", ["revision", "sha256", "package_hash", "member_hash"])
def test_model_and_package_pins_are_required_even_when_blocked(tmp_path, field):
    import json

    from tools.probe_silero_license import check_decision

    path, document, record = copied_manifest(tmp_path)
    if field in ("revision", "sha256"):
        record[field] = None
    elif field == "package_hash":
        record["package"]["sha256"] = ""
    else:
        record["package"]["model_sha256"] = "0" * 64
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="model or package pin"):
        check_decision(path, allow_blocked=True)


def test_badge_alone_never_approves_a_file(tmp_path):
    import json

    from tools.probe_silero_license import check_decision

    path, document, record = copied_manifest(tmp_path)
    record["license_sources"] = [
        next(c for c in record["license_sources"] if c["kind"] == "badge_image")
    ]
    path.write_text(json.dumps(document))
    assert (
        check_decision(path, allow_blocked=True)["status"] == "confirmed_blocked_absent"
    )
    record.update(
        status="confirmed_included",
        license="MIT",
        redistribution=True,
        download="startup",
    )
    document["download_plan"].append(record["id"])
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="pinned license text or model card required"):
        check_decision(path, allow_blocked=True)


def test_consistent_file_scoped_license_can_pass_the_existing_asset_gate(
    tmp_path, capsys, monkeypatch
):
    import json

    from tools.probe_silero_license import check_decision, main

    path, document, record = copied_manifest(tmp_path)
    claim = record["license_sources"][0]
    claim.update(
        kind="license_text",
        quote="This exact model file is licensed under MIT.",
    )
    record["license_sources"] = [claim]
    record.update(
        status="confirmed_included",
        license="MIT",
        redistribution=True,
        download="startup",
    )
    document["download_plan"].append(record["id"])
    path.write_text(json.dumps(document))
    assert check_decision(path, allow_blocked=False)["status"] == "confirmed_included"
    monkeypatch.setenv("ASSIST_LIVE_PROBE_DIRECTORY", str(tmp_path))
    report = tmp_path / "included.md"
    assert (
        main(["--record-decision", "--check", str(path), "--report", str(report)]) == 0
    )
    output = capsys.readouterr().out
    assert output.count("PASS included rights") == 1
    assert "PASS blocked asset absent" not in output
    assert "confirmed_included" in report.read_text()
    assert "stays blocked" not in report.read_text()


def test_conflict_is_extracted_from_quotes_in_the_single_source_shape(tmp_path):
    import json

    from tools.probe_silero_license import check_decision, source_statements

    path, document, record = copied_manifest(tmp_path)
    assert all(
        set(s) == {"kind", "url", "quote", "revision_or_version", "file_binding"}
        for s in record["license_sources"]
    )
    assert "license_claims" not in record
    assert "evidence" not in record
    source = record["license_sources"][0]
    source["quote"] = "The README states MIT and CC BY-NC 4.0."
    assert set(source_statements([source])) == {"MIT", "CC-BY-NC-4.0"}
    record["license_sources"] = [source]
    path.write_text(json.dumps(document))
    assert (
        check_decision(path, allow_blocked=True)["status"] == "confirmed_blocked_absent"
    )


@pytest.mark.parametrize("field", ["path", "sha256"])
def test_source_binding_must_name_the_selected_file_and_digest(tmp_path, field):
    import json

    from tools.probe_silero_license import check_decision

    path, document, record = copied_manifest(tmp_path)
    record["license_sources"][0]["file_binding"][field] = "wrong"
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="license source"):
        check_decision(path, allow_blocked=True)


def test_report_uses_manifest_file_path_in_the_record_and_question():
    import json

    from tools.probe_silero_license import render_report

    record = next(
        a
        for a in json.loads((ROOT / "assets/manifest.json").read_text())["assets"]
        if a["id"] == "silero-vad"
    )
    record["url"] = record["url"].replace(
        "src/silero_vad/data/silero_vad.onnx", "models/selected.onnx"
    )
    for source in record["license_sources"]:
        source["file_binding"]["path"] = "models/selected.onnx"
    text = render_report(record)
    assert "- File: `models/selected.onnx`." in text
    assert "What license applies to `models/selected.onnx`" in text


@pytest.mark.parametrize("field", ["evidence", "license_claims"])
def test_parallel_rights_fields_are_rejected(tmp_path, field):
    import json

    from tools.probe_silero_license import check_decision

    path, document, record = copied_manifest(tmp_path)
    record[field] = []
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="use license_sources"):
        check_decision(path, allow_blocked=True)


def test_cli_records_one_blocked_result_and_all_conflicting_sources(
    tmp_path, capsys, monkeypatch
):
    from tools.probe_silero_license import main

    monkeypatch.setenv("ASSIST_LIVE_PROBE_DIRECTORY", str(tmp_path))
    report = tmp_path / "report.md"
    assert (
        main(
            [
                "--record-decision",
                "--allow-blocked-absent",
                "--check",
                str(ROOT / "assets/manifest.json"),
                "--report",
                str(report),
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    assert output.count("PASS decision recorded") == 1
    assert output.count("PASS blocked asset absent") == 1
    assert "PASS included rights" not in output
    text = report.read_text()
    for value in (
        "CC-BY-NC-4.0",
        "MIT",
        "METADATA",
        "NOT CHECKED",
        "Prototype byte identity: `unverified`",
        "Upstream clarification draft",
    ):
        assert value in text
    assert (tmp_path / "decision.json").is_file()

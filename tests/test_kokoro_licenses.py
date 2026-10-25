from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_voice_without_evidence_is_not_permitted():
    from tools.probe_kokoro_licenses import permitted

    assert not permitted({"license": "Apache-2.0", "evidence": ""})


def test_report_uses_manifest_inspection_and_data_digest():
    import json

    from tools.check_assets import flatten
    from tools.probe_kokoro_licenses import render_report

    record = next(
        a
        for a in json.loads((ROOT / "assets/manifest.json").read_text())["assets"]
        if a["id"] == "kokoro-82m"
    )
    record["inspection"].update(candidate_voice="candidate", espeak_version="9.9.9")
    data = next(a for a in flatten([record], []) if a["id"] == "kokoro-espeak-data")
    data["tree_sha256"] = "d" * 64
    text = render_report(record)
    assert "The candidate voice was `candidate`." in text
    assert "reported version `9.9.9`" in text
    assert "Its tree digest is `" + "d" * 64 + "`." in text


def voice():
    revision = "a" * 40
    return {
        "id": "test-voice",
        "kind": "voice",
        "status": "included",
        "license": "Apache-2.0",
        "revision": revision,
        "sha256": "b" * 64,
        "url": f"https://example.com/{revision}/voice.pt",
        "attribution": "Synthetic test voice. Apache-2.0.",
        "redistribution": True,
        "download": "startup",
        "components": [],
        "license_sources": [
            {
                "kind": "license_text",
                "url": f"https://example.com/{revision}/LICENSE",
                "quote": "The voice file uses the Apache License, Version 2.0.",
                "revision_or_version": revision,
                "file_binding": {"path": "voice.pt", "sha256": "b" * 64},
            }
        ],
    }


def test_voice_digest_and_attribution_are_explicit():
    from tools.probe_kokoro_licenses import permitted, voice_allowlist

    record = voice()
    assert permitted(record)
    assert voice_allowlist(record) == [
        {
            "id": "test-voice",
            "url": record["url"],
            "sha256": "b" * 64,
            "attribution": record["attribution"],
        }
    ]


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("license", "not-a-license"),
        ("license", []),
        ("status", "unknown"),
        ("status", "not_selected"),
        ("sha256", ""),
        ("attribution", " "),
        ("components", None),
        ("license_sources", "Apache-2.0"),
        ("license_sources", []),
    ],
)
def test_incomplete_or_not_selected_voice_is_not_downloadable(key, value):
    from tools.probe_kokoro_licenses import permitted, voice_allowlist

    record = voice()
    record[key] = value
    assert not permitted(record)
    assert voice_allowlist(record) == []


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("url", "https://img.shields.io/badge/Apache-2.0"),
        ("quote", " "),
        ("revision_or_version", "main"),
        ("file_binding", {"path": "voice.pt", "sha256": "c" * 64}),
    ],
)
def test_evidence_must_bind_this_voice_and_file(key, value):
    from tools.probe_kokoro_licenses import permitted

    record = voice()
    record["license_sources"][0][key] = value
    assert not permitted(record)


def test_parent_cannot_hide_a_not_selected_runtime():
    from tools.probe_kokoro_licenses import permitted, voice_allowlist

    parent = voice()
    parent["kind"] = "model"
    parent["components"] = [voice(), {"status": "not_selected"}]
    assert not permitted(parent)
    assert voice_allowlist(parent) == []


def manifest_copy(tmp_path):
    import json

    document = json.loads((ROOT / "assets/manifest.json").read_text())
    document["release_root"] = "."
    (tmp_path / "THIRD_PARTY_NOTICES.md").write_text(
        (ROOT / "THIRD_PARTY_NOTICES.md").read_text()
    )
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(document))
    return path, document


def test_not_selected_decision_checks_tree_and_download_plan(tmp_path):
    import json

    from tools.probe_kokoro_licenses import check_decision

    path, document = manifest_copy(tmp_path)
    record, defaults = check_decision(path, allow_not_selected=True)
    assert record["status"] == "not_selected"
    assert defaults == []
    with pytest.raises(ValueError, match="--allow-not-selected"):
        check_decision(path, allow_not_selected=False)
    document["download_plan"].append("kokoro-82m")
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="not-selected asset"):
        check_decision(path, allow_not_selected=True)
    document["download_plan"].remove("kokoro-82m")
    document["image_inventory"] = ["kokoro-82m"]
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="not-selected asset"):
        check_decision(path, allow_not_selected=True)
    document["image_inventory"] = None
    path.write_text(json.dumps(document))
    artifact = tmp_path / record["paths"][0]
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_bytes(b"not-selected artifact")
    with pytest.raises(ValueError, match="not-selected asset path"):
        check_decision(path, allow_not_selected=True)


def test_default_voice_cannot_bypass_empty_allowlist(tmp_path):
    import json

    from tools.probe_kokoro_licenses import check_decision

    path, document = manifest_copy(tmp_path)
    document["kokoro_default_voices"] = [voice()]
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="default voice allowlist"):
        check_decision(path, allow_not_selected=True)


def test_unknown_status_and_incomplete_inclusion_fail(tmp_path):
    import json

    from tools.probe_kokoro_licenses import check_decision

    path, document = manifest_copy(tmp_path)
    record = next(a for a in document["assets"] if a["id"] == "kokoro-82m")
    record["status"] = "unknown"
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="included or not_selected status"):
        check_decision(path, allow_not_selected=True)
    record["status"] = "included"
    record["license"] = "GPL-3.0-or-later"
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="download behavior required"):
        check_decision(path, allow_not_selected=True)


def test_cli_records_one_not_selected_decision(tmp_path, capsys, monkeypatch):
    from tools.probe_kokoro_licenses import main

    report = tmp_path / "report.md"
    monkeypatch.setenv("ASSIST_LIVE_PROBE_DIRECTORY", str(tmp_path))
    assert (
        main(
            [
                "--record-decision",
                "--allow-not-selected",
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
    assert output.count("PASS not-selected asset absent") == 1
    assert "PASS included rights" not in output
    text = report.read_text()
    assert "not_selected" in text
    assert "GPL-3.0-or-later" in text
    assert "<3.13" in text
    assert "NOT CHECKED" in text
    assert "Default voice allowlist: `[]`" in text

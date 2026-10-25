"""Check Kokoro rights records and default voice digests without a model runtime."""

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ASSET_ID = "kokoro-82m"

if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))


def permitted(record: dict[str, Any]) -> bool:
    from tools.asset_rights import permitted as confirmed

    return confirmed(record)


def voice_allowlist(record: dict[str, Any]) -> list[dict[str, str]]:
    if not permitted(record):
        return []
    if record.get("kind") == "voice":
        return [{key: record[key] for key in ("id", "url", "sha256", "attribution")}]
    return [
        voice
        for component in record["components"]
        for voice in voice_allowlist(component)
    ]


def check_decision(
    manifest: Path, *, allow_blocked: bool
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    from tools.check_assets import check_assets

    errors = check_assets(manifest, release=True)
    if errors:
        raise ValueError("; ".join(errors))
    document = json.loads(manifest.read_text())
    record = next(item for item in document["assets"] if item["id"] == ASSET_ID)
    if record["status"] == "confirmed_blocked_absent" and not allow_blocked:
        raise ValueError("blocked asset needs --allow-blocked-absent")
    if record["status"] == "confirmed_included" and (
        record.get("stop_causes") or not permitted(record)
    ):
        raise ValueError("included Kokoro chain lacks file-scoped evidence")
    defaults = voice_allowlist(record)
    if document.get("kokoro_default_voices") != defaults:
        raise ValueError("default voice allowlist does not agree with confirmed rights")
    return record, defaults


def render_report(
    record: dict[str, Any], defaults: list[dict[str, str]] | None = None
) -> str:
    from tools.asset_rights import render_record, render_sources
    from tools.check_assets import flatten

    if record.get("id") != ASSET_ID:
        return render_record(record)
    if defaults is None:
        defaults = voice_allowlist(record)
    records = flatten([record], [])
    parts = {item["id"]: item for item in records}
    inspection = record["inspection"]
    code = parts["kokoro-code"]
    misaki = parts["kokoro-misaki"]
    data = parts["kokoro-espeak-data"]
    spacy = parts["kokoro-spacy-english-model"]
    versions = f"kokoro=={code['version']}` with `misaki=={misaki['version']}"
    voice_record = next(item for item in records if item["kind"] == "voice")
    voice_path = voice_record["url"].split(f"/resolve/{voice_record['revision']}/", 1)[
        -1
    ]
    voice_shape = tuple(inspection["voice_shape"])
    config_name = parts["kokoro-config"]["url"].rsplit("/", 1)[-1]
    components = []
    for item in records:
        lines = [
            f"### `{item['id']}`",
            "",
            f"- Status: `{item['status']}`.",
            f"- License: `{item['license']}`.",
            f"- Version or revision: `{item.get('version') or item.get('revision')}`.",
            f"- Source: {item['url']}",
            f"- SHA-256: `{item.get('sha256')}`.",
            "",
            "Attribution or stop cause:",
            "",
            "```text",
            item["attribution"],
            "```",
        ]
        lines.append(render_sources(item))
        if item.get("archive_path"):
            lines.append(f"Archive member: `{item['archive_path']}`.")
        if item.get("file_digests"):
            lines.extend(
                [
                    "",
                    "Individual file digests:",
                    "",
                    "```json",
                    json.dumps(item["file_digests"], indent=2),
                    "```",
                ]
            )
        components.append("\n".join(lines))
    supporting = []
    for item in record["inspection"]["supporting_packages"]:
        supporting.append(
            f"### `{item['name']}=={item['version']}`\n\n"
            f"- Package license: `{item['license']}`.\n"
            f"- Python requirement: `{item['requires_python']}`.\n"
            f"- Source: {item['source']}\n"
            f"- License file for inspection: `{item['license_file']}`.\n"
            f"- License-file SHA-256: `{item['license_sha256']}`.\n\n"
            + render_sources(item)
        )
    decision = (
        "Stop Kokoro runtime inclusion and its release."
        if record["status"] == "confirmed_blocked_absent"
        else "Use only the confirmed files and default voice digests below."
    )
    return (
        f"""# Kokoro runtime and voice rights

## Decision

{decision}
Status: `{record["status"]}`. The project is experimental and has no release.
Default voice allowlist: `{json.dumps(defaults)}`.

No voice is a default selection.
No Kokoro model, voice, dictionary, or runtime is in this release tree.
Image inventory: `NOT CHECKED`. There is no image.

The packaged Python requirements do not include the server interpreter.
The English phonemizer chain also contains GPL and LGPL components.
The recorded stop causes are:

"""
        + "```json\n"
        + json.dumps(record["stop_causes"], indent=2)
        + "\n```"
        + f"""

## Actual first-use inspection

The candidate is `{versions}` in scratch Python `3.12`.
No candidate package is a repository dependency.
The client and server package dependencies stay unchanged.
All caches and downloaded files stayed in scratch storage.
`ASSIST_LIVE_PROBE_DIRECTORY` selects that storage for the decision tool.
The tool uses the standard library only.
It does not install or download runtime components.

The inspection used `KPipeline(lang_code="a", trf=False, device="cpu")`.
The candidate voice was `{inspection["candidate_voice"]}`.
The Hugging Face download calls used the pinned revision in the records below.
The upstream example uses that voice.
It is an inspection candidate, not an approved default.
The first-use download list was:

- `{config_name}` with vocabulary and model configuration.
- `{record["url"].rsplit("/", 1)[-1]}` with the model weights.
- `{spacy["url"].rsplit("/", 1)[-1]}` for English tagging.
- `{voice_path}`, with tensor shape `{voice_shape}`.

The inspection loaded the model and voice but made no voice output or audio file.
It tested English G2P and the eSpeak fallback on a synthetic out-of-dictionary word.
The fallback used the bundled `libespeak-ng.so`, which
reported version `{inspection["espeak_version"]}`.
`misaki/espeak.py` sets the library and data paths from `espeakng_loader` explicitly.
The English path loads `us_gold.json`, `us_silver.json`, and `num2words`.
It also loads the spaCy tagger.

The package supplies dictionaries and eSpeak data.
They are not first-use network downloads.

The eSpeak data bundle contains {data["file_count"]} files.
Its tree digest is `{data["tree_sha256"]}`.
The digest includes sorted relative paths and individual SHA-256 values.
The format is compact, key-sorted JSON.
The complete file inventory stays in scratch storage.
Necessary English data digests are below.

The loader wheel has no license file.
Its MIT source license does not apply to its bundled GPL parts.

The inspection did not select other languages.
It did not download their optional G2P models and dictionaries.
No rights approval applies to those paths or to other voices.

## File and package records

Access date for all sources: 2026-10-24.
The manifest records the source statement that sets each license decision.
`NOASSERTION` is a blocked rights finding, not permission to use a file.

The weights have an Apache statement and a complete digest in the same model page.
The voice table supplies a name and short digest.
It supplies no voice-specific license statement.
The model-level license tag alone does not supply approval for the voice or config.
Package statements come from the versioned wheel and its attached license.
Source headers also supply license statements.

"""
        + "\n\n".join(components)
        + """

## Other package declarations

These versions are from the scratch environment, not the repository environment.
The attached package licenses supply these statements.
These declarations do not supply approval for all bundled third-party parts.
They also do not supply approval for all their dependencies.

The complete Kokoro chain stays blocked. No package below is a new release selection.
NumPy has the same license expression as the recorded probe dependency in the notices.

"""
        + "\n\n".join(supporting)
        + """

## Release boundary and reproduction

The manifest has an explicit empty `kokoro_default_voices` list.
The tool refuses an unknown status or a blocked file in the release tree.
It also refuses an unapproved default voice.
The asset gate checks all nested components, the image inventory, and the download plan.

No Kokoro component is in that plan. The Smart Turn record stays unchanged.
An accepted blocked-absent decision is not satisfactory synthesis or a release approval.

```sh
uv run pytest tests/test_kokoro_licenses.py -q
uv run python tools/probe_kokoro_licenses.py --record-decision \\
  --allow-blocked-absent --check assets/manifest.json \\
  --report docs/research/q7-kokoro.md
uv run python tools/check_assets.py --manifest assets/manifest.json --release
```

A design decision is necessary before runtime work can continue.
This inspection selects no replacement model or runtime.
"""
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record-decision", action="store_true")
    parser.add_argument("--allow-blocked-absent", action="store_true")
    parser.add_argument("--check", type=Path, default=ROOT / "assets/manifest.json")
    parser.add_argument(
        "--report", type=Path, default=ROOT / "docs/research/q7-kokoro.md"
    )
    args = parser.parse_args(argv)
    try:
        record, defaults = check_decision(
            args.check, allow_blocked=args.allow_blocked_absent
        )
        if args.record_decision:
            directory = Path(
                os.environ.get(
                    "ASSIST_LIVE_PROBE_DIRECTORY",
                    str(Path(tempfile.gettempdir()) / "assist-live-kokoro-rights"),
                )
            )
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "decision.json").write_text(
                json.dumps(
                    {
                        "status": record["status"],
                        "default_voices": defaults,
                        "stop_causes": record["stop_causes"],
                    },
                    indent=2,
                )
                + "\n"
            )
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(render_report(record, defaults))
            print("PASS decision recorded")
        print(
            "PASS included rights"
            if record["status"] == "confirmed_included"
            else "PASS blocked asset absent"
        )
        return 0
    except (OSError, ValueError, KeyError, StopIteration) as error:
        print(f"FAIL Kokoro licenses: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

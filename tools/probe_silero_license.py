"""Keep conflicting Silero model rights out of release assets."""

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
ASSET_ID = "silero-vad"
MODEL_PATH = "src/silero_vad/data/silero_vad.onnx"

if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))


def resolve_license(statements: list[str]) -> str:
    from tools.check_assets import ALLOWED

    if not statements or not all(isinstance(value, str) for value in statements):
        return "blocked"
    licenses = set(statements)
    if len(licenses) != 1 or statements[0] not in ALLOWED:
        return "blocked"
    return statements[0]


def source_statements(sources: list[dict[str, Any]]) -> list[str]:
    from tools.check_assets import ALLOWED

    statements = []
    for source in sources:
        quote = source["quote"]
        identifiers = [
            value
            for value in sorted(ALLOWED)
            if re.search(r"\b" + re.escape(value) + r"\b", quote)
        ]
        if re.search(r"\bCC[ -]+BY[ -]+NC[ -]+4\.0\b", quote):
            identifiers.append("CC-BY-NC-4.0")
        if not identifiers and source["kind"] != "badge_link":
            identifiers.append("NOASSERTION")
        statements.extend(identifiers)
    return statements


def check_decision(manifest: Path, *, allow_blocked: bool) -> dict[str, Any]:
    from tools.check_assets import check_assets, https_url

    errors = check_assets(manifest, release=True)
    if errors:
        raise ValueError("; ".join(errors))
    document = json.loads(manifest.read_text())
    record = next(item for item in document["assets"] if item["id"] == ASSET_ID)
    revision, digest = record.get("revision"), record.get("sha256")
    package = record.get("package")
    if (
        not isinstance(revision, str)
        or not re.fullmatch(r"[a-fA-F0-9]{40}", revision)
        or not isinstance(digest, str)
        or not re.fullmatch(r"[a-fA-F0-9]{64}", digest)
        or not https_url(record.get("url"))
        or revision not in record["url"]
        or not isinstance(package, dict)
        or not https_url(package.get("url"))
        or not isinstance(package.get("sha256"), str)
        or not re.fullmatch(r"[a-fA-F0-9]{64}", package["sha256"])
        or package.get("model_sha256") != digest
    ):
        raise ValueError("invalid model or package pin")
    if any(name in record for name in ("evidence", "license_claims")):
        raise ValueError("use license_sources only for rights statements")
    sources = record.get("license_sources")
    if not isinstance(sources, list) or not sources:
        raise ValueError("license source records required")
    for source in sources:
        if (
            not isinstance(source, dict)
            or set(source)
            != {"kind", "url", "quote", "revision_or_version", "file_binding"}
            or not isinstance(source.get("kind"), str)
            or not source["kind"].strip()
            or not isinstance(source.get("quote"), str)
            or not source["quote"].strip()
            or not https_url(source.get("url"))
            or not (
                (
                    revision in source["url"]
                    and source["revision_or_version"] == revision
                )
                or (
                    source["url"].split("#", 1)[0] == package["url"]
                    and source["revision_or_version"] == package["version"]
                )
            )
            or not isinstance(source.get("file_binding"), dict)
            or source["file_binding"] != {"path": MODEL_PATH, "sha256": digest}
        ):
            raise ValueError("invalid or unpinned license source record")
    resolved = resolve_license(source_statements(sources))
    file_scoped = any(
        source.get("kind") in ("license_text", "model_card")
        and resolved in source_statements([source])
        for source in sources
    )
    expected = (
        "confirmed_included"
        if resolved != "blocked" and file_scoped
        else "confirmed_blocked_absent"
    )
    if record["status"] != expected:
        raise ValueError("decision does not agree with file-scoped license evidence")
    if expected == "confirmed_blocked_absent":
        if record["license"] != "NOASSERTION":
            raise ValueError("unresolved file license must remain NOASSERTION")
        if not allow_blocked:
            raise ValueError("blocked asset needs --allow-blocked-absent")
    elif record["license"] != resolved:
        raise ValueError("included license does not agree with source records")
    return dict(record)


def render_report(record: dict[str, Any]) -> str:
    package = record["package"]
    sources = []
    for source in record["license_sources"]:
        statement = ", ".join(source_statements([source])) or "navigation only"
        sources.append(
            f"### `{source['kind']}` at `{urlparse(source['url']).fragment}`\n\n"
            f"- Statement from source: `{statement}`.\n"
            f"- Type: `{source['kind']}`.\n"
            f"- Source: {source['url']}\n"
            f"- Source revision or version: `{source['revision_or_version']}`.\n"
            f"- Bound file: `{source['file_binding']['path']}`.\n"
            f"- File SHA-256: `{source['file_binding']['sha256']}`.\n\n"
            f"```text\n{source['quote']}\n```"
        )
    if record["status"] == "confirmed_included":
        return (
            "# Silero model rights\n\n## Decision\n\n"
            "Use only the pinned file with its confirmed license and attribution.\n"
            f"Status: `{record['status']}`. License: `{record['license']}`.\n"
            "The project is experimental and has no release.\n"
            "Image inventory: `NOT CHECKED`. There is no image.\n\n"
            f"Model source: {record['url']}\n\n"
            f"Revision: `{record['revision']}`.\n\n"
            f"SHA-256: `{record['sha256']}`.\n\n"
            f"Download behavior: `{record['download']}`.\n\n"
            f"Attribution: `{record['attribution']}`.\n\n"
            "## File-scoped source statements\n\n" + "\n\n".join(sources)
        )
    search = record["clarification_search"]
    owners = []
    for source in search["owner_examples"]:
        owners.append(
            f"- Source: {source['url']}\n"
            f"- Date: `{source['date']}`.\n"
            f"- Author association: `{source['author_association']}`.\n\n"
            f"```text\n{source['quote']}\n```\n\n"
            f"Scope record: `{source['scope']}`."
        )
    history = []
    for source in search["badge_history"]:
        history.append(
            f"- Source: {source['url']}\n"
            f"- Date: `{source['date']}`.\n"
            f"- Location: `{source['location']}`.\n\n"
            f"```text\n{source['quote']}\n```"
        )
    decision = (
        "Stop model inclusion and the VAD-dependent server runtime."
        if record["status"] == "confirmed_blocked_absent"
        else "Use only the pinned file with its confirmed license and attribution."
    )
    return (
        f"""# Silero model rights

## Decision

{decision}
Status: `{record["status"]}`. File license: `{record["license"]}`.

The project is experimental and has no release.
The release tree and startup download plan do not include the model file.
Image inventory: `NOT CHECKED`. There is no image.

The general MIT statements conflict with the non-commercial badge text.
This investigation found no file-scoped clarification in the examined sources.
It does not select the more permissive statement or a replacement VAD.
A badge supplies no file-license approval.

## Selected file and package

- Tag: `{record["tag"]}`.
- Commit: `{record["revision"]}`.
- File: `src/silero_vad/data/silero_vad.onnx`.
- Source: {record["url"]}
- SHA-256: `{record["sha256"]}`.
- Size: `{record["bytes"]}` bytes.
- PyPI version: `{package["version"]}`.
- Wheel source: {package["url"]}
- Wheel SHA-256: `{package["sha256"]}`.
- Wheel member: `{package["model_member"]}`.
- Member SHA-256: `{package["model_sha256"]}`.

The release file and packaged model bytes are the same.
The model and wheel stayed in scratch storage during inspection.
No model bytes, package, or new dependency are in this repository.
The decision checker uses the standard library only.
`ASSIST_LIVE_PROBE_DIRECTORY` selects scratch storage for the decision record.

Package metadata source: {package["metadata_url"]}
`Requires-Python` is `{package["requires_python"]}`.
This includes the server floor `3.14.2`.
The PyPI `license` and `license_expression` fields are `null`.
The classifier says `MIT License`. The wheel also includes the complete MIT text.
The attached license digest is `{package["license_sha256"]}`.

## Prototype contract and model metadata

Prototype byte identity: `unverified`.
The prototype names `silero_vad.onnx` and inputs `input`, `state`, and `sr`.
Its binary, download URL, revision, and digest are not available.
This decision is for the selected release file.
It does not claim prototype byte identity.

The installed ONNX Runtime `{record["inspection"]["onnxruntime_version"]}`
loaded this file.
Its actual input names agree with the prototype contract:

```json
{json.dumps(record["inspection"]["inputs"], indent=2)}
```

The model metadata has no license statement:

```json
{json.dumps(record["inspection"]["model_metadata"], indent=2)}
```

The package and selected model directory have no `model card`.
The investigation examined all textual package members for license statements.
The six license-related lines are in `METADATA` and its attached `LICENSE`.
The statements below include the complete license text and each badge part.

## License statements

Access date for all sources: `2026-10-24`.
The package source has a pinned version and wheel SHA-256.
Repository sources have the selected commit as their pin.
General declarations do not supply file-scoped clarification.
No source below resolves the contradictory model-rights statements.

This model uses only `license_sources` for rights statements.
Each entry has `kind`, `url`, `quote`, and `revision_or_version`.
The same entry has `file_binding` with `path` and `sha256`.
The checker reads license identifiers from `quote`.
The decision uses no other source list.

"""
        + "\n\n".join(sources)
        + f"""

## Read-only clarification search

The repository issue search used `license`, `CC BY-NC`, `badge`, and `commercial`.
Result counts were `{json.dumps(search["issue_queries"], sort_keys=True)}`.
The positive `license` result set checked that the search could find known issues.
The investigation read the nine issues and their comments:
`{json.dumps(search["issue_threads_read"])}`.

It examined titles and bodies for all
`{search["discussion_titles_and_bodies"]}` discussions.
The three term results were discussion numbers
`{json.dumps(search["discussion_matches"])}`.
The `license` term in discussion `111` is part of a Python startup message.
Discussions `246` and `336` are about custom training and tuning.
The investigation examined their replies for an `OWNER` or `MEMBER` license statement.

No examined owner or member statement gives a license for this file and revision.
The owner replies below are about paid services, not a model-weight license grant.

"""
        + "\n\n".join(owners)
        + f"""

## Badge history

The investigation examined `{search["readme_commits"]}` root README commits.
It followed the renamed README to its initial path and first commit.
The first commit includes the alt text, before the selected model was available.
The historical image and `link target` referred to a different model project.
A subsequent diff changed the image to MIT but kept the non-commercial alt text.

"""
        + "\n\n".join(history)
        + f"""

This history suggests inherited text.
It supplies no license statement for the selected model.
Historical AGPL image text is not a license finding for this release.
The current conflict stays unresolved in the file-license policy.

## Upstream clarification draft

This investigation sent no upstream message and opened no issue.
Do not send this proposed question without operator approval:

What license applies to `src/silero_vad/data/silero_vad.onnx` at `{record["tag"]}`,
commit `{record["revision"]}`, SHA-256 `{record["sha256"]}`?

The PyPI `silero-vad=={package["version"]}` wheel contains the same model bytes.
Its SHA-256 is `{package["sha256"]}`.
The `LICENSE`, README body, and package classifier state `MIT`.
The README badge alt text states `CC BY-NC 4.0` in the repository and package.

Does MIT apply to these model weights for commercial use and redistribution,
with no non-commercial restriction?
Please give a file-scoped license statement for this revision or digest.
Please also make the badge text agree with the model license.

## Release checks and reproduction

The checker rejects conflicting or unknown terms and a badge-only approval.
It checks the source locations, pinned URLs, source statements, and file digest.
The asset gate also checks blocked paths and runtime source.
It checks the download plan.

It examines the image inventory when a record is available.
There is no image to examine here.
The Piper, Smart Turn, and Kokoro records stay unchanged.

```sh
uv run pytest tests/test_silero_license.py -q
uv run python tools/probe_silero_license.py --record-decision \\
  --allow-blocked-absent --check assets/manifest.json \\
  --report docs/research/q8-silero.md
uv run python tools/check_assets.py --manifest assets/manifest.json --release
```

The VAD-dependent runtime stays blocked until file rights have a satisfactory result.
This investigation selects no alternative model.
"""
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record-decision", action="store_true")
    parser.add_argument("--allow-blocked-absent", action="store_true")
    parser.add_argument("--check", type=Path, default=ROOT / "assets/manifest.json")
    parser.add_argument(
        "--report", type=Path, default=ROOT / "docs/research/q8-silero.md"
    )
    args = parser.parse_args(argv)
    try:
        record = check_decision(args.check, allow_blocked=args.allow_blocked_absent)
        if args.record_decision:
            directory = Path(
                os.environ.get(
                    "ASSIST_LIVE_PROBE_DIRECTORY",
                    str(Path(tempfile.gettempdir()) / "assist-live-silero-rights"),
                )
            )
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "decision.json").write_text(
                json.dumps(
                    {
                        "status": record["status"],
                        "license": record["license"],
                        "revision": record["revision"],
                        "sha256": record["sha256"],
                    },
                    indent=2,
                )
                + "\n"
            )
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(render_report(record))
            print("PASS decision recorded")
        print(
            "PASS included rights"
            if record["status"] == "confirmed_included"
            else "PASS blocked asset absent"
        )
        return 0
    except (OSError, ValueError, KeyError, StopIteration) as error:
        print(f"FAIL Silero license: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

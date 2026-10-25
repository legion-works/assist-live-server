"""Record the pinned Silero model license and the upstream owner statement."""

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ASSET_ID = "silero-vad"
MODEL_PATH = "src/silero_vad/data/silero_vad.onnx"

if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))


def resolve_license(statements: list[str]) -> str:
    from tools.asset_rights import LICENSE_IDS

    if not statements or not all(isinstance(value, str) for value in statements):
        return "NOASSERTION"
    licenses = set(statements)
    if len(licenses) != 1 or statements[0] not in LICENSE_IDS:
        return "NOASSERTION"
    return statements[0]


def source_statements(sources: list[dict[str, Any]]) -> list[str]:
    from tools.asset_rights import LICENSE_IDS

    statements = []
    for source in sources:
        quote = source["quote"]
        identifiers = [
            value
            for value in sorted(LICENSE_IDS)
            if re.search(r"\b" + re.escape(value) + r"\b", quote)
        ]
        if re.search(r"\bCC[ -]+BY[ -]+NC[ -]+4\.0\b", quote):
            identifiers.append("CC-BY-NC-4.0")
        if not identifiers and source["kind"] not in ("badge_link", "license_pointer"):
            identifiers.append("NOASSERTION")
        statements.extend(identifiers)
    return statements


def check_decision(manifest: Path, *, allow_not_selected: bool) -> dict[str, Any]:
    from tools.asset_rights import license_sources
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
    sources = license_sources(record)
    for source in sources:
        if source.get("file_binding") != {"path": MODEL_PATH, "sha256": digest}:
            raise ValueError("invalid or unpinned license source record")
    owners = [source for source in sources if source["kind"] == "owner_statement"]
    if owners:
        if any(source["revision_or_version"] != record["tag"] for source in owners):
            raise ValueError("owner statement release does not match selected model")
        if record["license"] != resolve_license(source_statements(owners)):
            raise ValueError("included license does not agree with owner statement")
    if record["status"] == "not_selected":
        if not allow_not_selected:
            raise ValueError("not-selected asset needs --allow-not-selected")
    return dict(record)


def render_report(record: dict[str, Any]) -> str:
    from tools.asset_rights import license_sources, render_record, render_sources

    if record.get("id") != ASSET_ID:
        return render_record(record)
    sources = license_sources(record)
    package = record["package"]
    model_path = sources[0]["file_binding"]["path"]
    owners = [source for source in sources if source["kind"] == "owner_statement"]
    owner_text = (
        "\n\n".join(
            f"Source: {source['url']}\n\n```text\n{source['quote']}\n```"
            for source in owners
        )
        or "No owner statement is in this record."
    )
    return (
        "# Silero model rights\n\n## Decision\n\n"
        f"Status: `{record['status']}`. License: `{record['license']}`.\n"
        "The project is experimental and has no release.\n"
        "Image inventory: `NOT CHECKED`. There is no image.\n\n"
        "## Owner statement\n\n"
        "The repository owner answered the file-scoped question with this text:\n\n"
        f"{owner_text}\n\n"
        "Author association: `OWNER`. Access date: 2026-10-25.\n"
        "Statement time: `2026-10-25T04:51:19Z`.\n"
        "The issue closed as completed at `2026-10-25T04:51:32Z`.\n"
        "\n"
        "The README badge alt text states `CC BY-NC 4.0`.\n"
        "That identifier is `CC-BY-NC-4.0`.\n"
        "The owner statement resolves that text for this file and revision.\n\n"
        "## Selected file and package\n\n"
        f"- File: `{model_path}`.\n"
        f"- Tag: `{record['tag']}`.\n"
        f"- Revision: `{record['revision']}`.\n"
        f"- Model source: {record['url']}\n"
        f"- SHA-256: `{record['sha256']}`.\n"
        f"- Package: `silero-vad=={package['version']}`.\n"
        f"- Package source: {package['url']}\n"
        f"- Package SHA-256: `{package['sha256']}`.\n"
        f"- Package model SHA-256: `{package['model_sha256']}`.\n"
        f"- Download behavior: `{record['download']}`.\n"
        f"- Attribution:\n\n```text\n{record['attribution']}\n```\n\n"
        "The package contains the same model bytes. No model is in this repository.\n"
        "Prototype byte identity: `unverified`.\n"
        "The prototype binary and digest are not available.\n\n"
        "## File-scoped question\n\n"
        f"What license applies to `{model_path}` at `{record['tag']}`, "
        f"commit `{record['revision']}`, SHA-256 `{record['sha256']}`?\n\n"
        "The source records below keep the initial MIT and badge statements.\n"
        "The package statements come from `METADATA` and its attached license.\n"
        "Sources accessed: 2026-10-24 and 2026-10-25.\n\n"
        "## Source statements\n\n" + render_sources(record) + "\n\n"
        "## Reproduction\n\n"
        "```sh\nuv run pytest tests/test_silero_license.py -q\n"
        "uv run python tools/probe_silero_license.py --record-decision \\\n"
        "  --check assets/manifest.json --report docs/research/q8-silero.md\n"
        "uv run python tools/check_assets.py "
        "--manifest assets/manifest.json --release\n```\n"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record-decision", action="store_true")
    parser.add_argument("--allow-not-selected", action="store_true")
    parser.add_argument("--check", type=Path, default=ROOT / "assets/manifest.json")
    parser.add_argument(
        "--report", type=Path, default=ROOT / "docs/research/q8-silero.md"
    )
    args = parser.parse_args(argv)
    try:
        record = check_decision(args.check, allow_not_selected=args.allow_not_selected)
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
            if record["status"] == "included"
            else "PASS not-selected asset absent"
        )
        return 0
    except (OSError, ValueError, KeyError, StopIteration) as error:
        print(f"FAIL Silero license: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

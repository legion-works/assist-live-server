"""Use one source-record contract for release checks and rights reports."""

import hashlib
import re
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import unquote

LICENSE_IDS = {
    "Apache-2.0",
    "MIT",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "CC0-1.0",
    "CC-BY-4.0",
    "CC-BY-NC-4.0",
    "CC-BY-ND-4.0",
    "CC-BY-NC-SA-4.0",
    "GPL-3.0-only",
    "GPL-3.0-or-later",
    "LGPL-2.1-only",
    "LGPL-2.1-or-later",
    "NOASSERTION",
    "Unicode-DFS-2016",
    "0BSD",
    "Zlib",
}


CUSTOM_LICENSE_TERMS = {
    "LicenseRef-OpenRAIL-M": (
        "33db5a3eb67f221fb04ff5d89805591f8e41dbcf86fb78919b2d3eb6a16276a8"
    ),
}


def license_recorded(value: object, sources: object = None) -> bool:
    if not isinstance(value, str):
        return False
    entries = sources if isinstance(sources, list) else []
    return all(
        token in LICENSE_IDS
        or (
            token in CUSTOM_LICENSE_TERMS
            and any(
                isinstance(source, dict)
                and source.get("kind") == token
                and isinstance(source.get("quote"), str)
                and hashlib.sha256(source["quote"].encode()).hexdigest()
                == CUSTOM_LICENSE_TERMS[token]
                for source in entries
            )
        )
        for token in re.split(r" (?:AND|OR) ", value)
    )


def pinned_source(url: object, pin: object, kind: object = None) -> bool:
    if not isinstance(url, str) or not isinstance(pin, str):
        return False
    if not re.fullmatch(
        r"(?:[a-fA-F0-9]{40,64}|v?\d+(?:\.\d+)+(?:[a-zA-Z0-9.+-]*))", pin
    ):
        return False
    if kind == "owner_statement":
        return bool(
            re.fullmatch(
                r"https://github\.com/[^/]+/[^/]+/issues/\d+#issuecomment-\d+", url
            )
        )
    if kind == "dataset_license_snapshot":
        return bool(re.fullmatch(r"[a-f0-9]{64}", pin))
    return pin in unquote(url)


def source_errors(record: dict[str, Any]) -> list[str]:
    from tools.check_assets import https_url

    label = str(record.get("id", record.get("name", "asset")))
    errors = []
    for field in ("evidence", "license_claims"):
        if field in record:
            errors.append(f"{label}: legacy rights field {field}; use license_sources")
    project = record.get("upstream_project", {})
    if not isinstance(project, dict) or set(project) - {
        "repository",
        "revision",
        "url",
    }:
        errors.append(f"{label}: upstream_project must contain navigation only")
    sources = record.get("license_sources")
    if not isinstance(sources, list) or not sources:
        return errors + [f"{label}: license source records required"]
    bound = False
    for source in sources:
        if not isinstance(source, dict):
            errors.append(f"{label}: license source record must be an object")
            continue
        required = {"kind", "url", "quote", "revision_or_version"}
        if set(source) not in (required | {"file_binding"}, required | {"scope"}):
            errors.append(
                f"{label}: license source fields and file binding or scope required"
            )
        for field in required:
            if not isinstance(source.get(field), str) or not source[field].strip():
                errors.append(f"{label}: license source {field} required")
        url, pin = source.get("url"), source.get("revision_or_version")
        if not https_url(url):
            errors.append(f"{label}: license source HTTPS URL required")
        if not pinned_source(url, pin, source.get("kind")):
            errors.append(
                f"{label}: license source immutable revision or version required"
            )
        binding = source.get("file_binding")
        if binding is not None:
            if (
                not isinstance(binding, dict)
                or set(binding) != {"path", "sha256"}
                or not isinstance(binding.get("path"), str)
                or not binding["path"].strip()
                or PurePosixPath(binding["path"]).is_absolute()
                or ".." in PurePosixPath(binding["path"]).parts
                or not isinstance(binding.get("sha256"), str)
                or not re.fullmatch(r"[a-fA-F0-9]{64}", binding["sha256"])
                or (
                    record.get("sha256") is not None
                    and binding["sha256"] != record["sha256"]
                )
            ):
                errors.append(
                    f"{label}: license source file binding mismatch or invalid"
                )
            elif (
                binding["sha256"] == record.get("sha256")
                and source.get("kind")
                in ("license_text", "model_card", "owner_statement")
                and not re.search(r"badge|shields\.io", str(url), re.IGNORECASE)
            ):
                bound = True
        elif not isinstance(source.get("scope"), str) or not source["scope"].strip():
            errors.append(
                f"{label}: license source file binding or non-empty scope required"
            )
    if record.get("status") == "included" and not bound:
        errors.append(
            f"{label}: pinned license text or model card required"
            " with exact file binding"
        )
    return errors


def license_sources(record: dict[str, Any]) -> list[dict[str, Any]]:
    errors = source_errors(record)
    if errors:
        raise ValueError("; ".join(errors))
    return list(record["license_sources"])


def permitted(record: dict[str, Any]) -> bool:
    from tools.check_assets import https_url

    return (
        record.get("status") == "included"
        and license_recorded(record.get("license"), record.get("license_sources"))
        and isinstance(record.get("redistribution"), bool)
        and record.get("download") in ("startup", "included")
        and isinstance(record.get("attribution"), str)
        and bool(record["attribution"].strip())
        and isinstance(record.get("revision"), str)
        and https_url(record.get("url"))
        and pinned_source(record.get("url"), record["revision"])
        and not source_errors(record)
        and isinstance(record.get("components"), list)
        and all(
            isinstance(child, dict) and permitted(child)
            for child in record["components"]
        )
    )


def render_sources(record: dict[str, Any]) -> str:
    sections = []
    for source in license_sources(record):
        binding = source.get("file_binding")
        detail = (
            f"File: `{binding['path']}`. SHA-256: `{binding['sha256']}`."
            if binding
            else f"Scope record:\n\n```text\n{source['scope']}\n```"
        )
        sections.append(
            f"- License source: {source['url']}\n"
            f"- Source revision or version: `{source['revision_or_version']}`.\n"
            f"- {detail}\n\n"
            f"License statement:\n\n```text\n{source['quote']}\n```"
        )
    return "\n\n".join(sections)


def render_record(record: dict[str, Any]) -> str:
    return (
        f"# Asset rights: `{record['id']}`\n\n"
        f"Status: `{record['status']}`. License: `{record.get('license')}`.\n\n"
        f"Source: {record.get('url')}\n\n"
        f"Revision or version: `{record.get('revision') or record.get('version')}`.\n\n"
        f"SHA-256: `{record.get('sha256')}`.\n\n"
        "Image inventory: `NOT CHECKED`. There is no image.\n\n"
        + render_sources(record)
    )

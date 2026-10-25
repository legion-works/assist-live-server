"""Use one source-record contract for release checks and rights reports."""

import re
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import unquote


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
        if (
            not isinstance(pin, str)
            or not re.fullmatch(
                r"(?:[a-fA-F0-9]{40,64}|v?\d+(?:\.\d+)+(?:[a-zA-Z0-9.+-]*))", pin
            )
            or not isinstance(url, str)
            or pin not in unquote(url)
        ):
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
                and source.get("kind") in ("license_text", "model_card")
                and not re.search(r"badge|shields\.io", str(url), re.IGNORECASE)
            ):
                bound = True
        elif not isinstance(source.get("scope"), str) or not source["scope"].strip():
            errors.append(
                f"{label}: license source file binding or non-empty scope required"
            )
    if record.get("status") == "confirmed_included" and not bound:
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
    from tools.check_assets import ALLOWED, https_url

    return (
        record.get("status") == "confirmed_included"
        and isinstance(record.get("license"), str)
        and record.get("license") in ALLOWED
        and record.get("redistribution") is True
        and record.get("download") in ("startup", "included")
        and isinstance(record.get("attribution"), str)
        and bool(record["attribution"].strip())
        and isinstance(record.get("revision"), str)
        and bool(re.fullmatch(r"[a-fA-F0-9]{40,64}", record["revision"]))
        and https_url(record.get("url"))
        and record["revision"] in record["url"]
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
        detail = source.get("file_binding", source.get("scope"))
        sections.append(
            f"- License source: {source['url']}\n"
            f"- Source revision or version: `{source['revision_or_version']}`.\n"
            f"- File or scope: `{detail}`.\n\n"
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

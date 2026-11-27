"""Check CI boundaries and the separate Python distributions."""

import argparse
import json
import os
import re
import subprocess
import tempfile
import tomllib
import zipfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
APPROVAL = "${{ vars.ASSIST_LIVE_RELEASES_APPROVED }}"


PUBLIC_TREE_STEP = re.compile(
    r"set -eu\n"
    r"curl -fsSL https://raw\.githubusercontent\.com/legion-works/assist-live/"
    r"[a-f0-9]{40}/tools/check_public_tree\.py"
    r' -o "\$RUNNER_TEMP/check_public_tree\.py"\n'
    r'echo "[a-f0-9]{64}  \$RUNNER_TEMP/check_public_tree\.py" \| sha256sum -c -\n'
    + re.escape(
        'set --\nif [ -n "${PRIVATE_DENYLIST:-}" ]; then\n'
        '  set -- --private-denylist "$PRIVATE_DENYLIST"\nfi\n'
        'uv run --no-project --python 3.14 python "$RUNNER_TEMP/check_public_tree.py" '
        '--history --root . "$@"'
    )
)


def _publishes_only_checked_client_wheel(workflow: dict) -> bool:
    jobs = workflow["jobs"]
    build = jobs["build"]["steps"]
    publish = jobs["publish"]["steps"]
    check, upload = build[-2:]
    uploads = [
        step
        for step in build
        if step.get("uses", "").startswith("actions/upload-artifact@")
    ]
    return (
        set(jobs) == {"build", "publish"}
        and jobs["publish"].get("needs") == "build"
        and "if" not in jobs["publish"]
        and not jobs["build"].get("continue-on-error")
        and "if" not in check
        and not check.get("continue-on-error")
        and check.get("run", "").splitlines()[-1:]
        == [
            "uv run --locked python tools/check_release.py --wheel dist "
            '--assets assets/manifest.json --pypi-version "$CLIENT_VERSION"'
        ]
        and uploads == [upload]
        and "if" not in upload
        and upload.get("with")
        == {
            "name": "client-dist",
            "path": "dist/assist_live_client-*.whl",
            "if-no-files-found": "error",
        }
        and len(publish) == 3
        and publish[1].get("uses", "").startswith("actions/download-artifact@")
        and publish[1].get("with") == {"name": "client-dist", "path": "dist/"}
        and publish[2].get("uses", "").startswith("pypa/gh-action-pypi-publish@")
        and publish[2].get("with") == {"packages-dir": "dist/"}
    )


def check_ci(root: Path = ROOT) -> list[str]:
    errors: list[str] = []
    workflows = {}
    for name in ("ci", "release-please", "release-artifacts", "publish-client"):
        path = root / f".github/workflows/{name}.yml"
        try:
            text = path.read_text()
            workflow = yaml.safe_load(text)
            workflows[name] = workflow
            if workflow.get("permissions") != {"contents": "read"}:
                errors.append(f"{name}: top-level read-only permissions required")
            for job_name, job in workflow["jobs"].items():
                permissions = job.get("permissions", {"contents": "read"})
                if (name, job_name) == ("publish-client", "publish"):
                    if job.get("permissions") != {"id-token": "write"}:
                        errors.append(
                            f"{name}: {job_name}: id-token-only permissions required"
                        )
                elif (name, job_name) == ("release-please", "release-please"):
                    if permissions != {"contents": "write", "pull-requests": "write"}:
                        errors.append(
                            f"{name}: {job_name}: release metadata permissions required"
                        )
                elif permissions != {"contents": "read"}:
                    errors.append(
                        f"{name}: {job_name}: read-only job permissions required"
                    )
                for step in job["steps"]:
                    if "uses" in step and not re.fullmatch(
                        r"[\w.-]+/[\w./-]+@[a-f0-9]{40}", step["uses"]
                    ):
                        errors.append(f"{name}: full action commit required")
            for line in text.splitlines():
                if re.search(r"\buses:", line) and not re.search(
                    r"# v\d+\.\d+\.\d+$", line
                ):
                    errors.append(f"{name}: action version comment required")
            if name != "ci":
                # PyYAML uses YAML 1.1 and reads the GitHub key `on` as True.
                triggers = workflow.get("on", workflow.get(True))
                if triggers != {"workflow_dispatch": None}:
                    errors.append(f"{name}: manual dispatch only")
                for job in workflow["jobs"].values():
                    first = job["steps"][0]
                    if first.get("env", {}).get("RELEASES_APPROVED") != APPROVAL or (
                        first.get("run") != 'test "$RELEASES_APPROVED" = "true"'
                    ):
                        errors.append(
                            f"{name}: fail-closed first-step release guard required"
                        )
        except OSError, ValueError, KeyError, TypeError, AttributeError, yaml.YAMLError:
            errors.append(f"{name}: invalid workflow")
    ci = workflows.get("ci", {}).get("jobs", {})
    try:
        public_tree = [
            step
            for job in ci.values()
            for step in job["steps"]
            if "check_public_tree.py" in step.get("run", "")
        ]
        if (
            len(public_tree) != 1
            or public_tree[0] not in ci["checks"]["steps"]
            or public_tree[0].get("if") != "matrix.check == 'public-tree'"
            or public_tree[0].get("continue-on-error")
            or not PUBLIC_TREE_STEP.fullmatch(public_tree[0]["run"].rstrip("\n"))
        ):
            raise ValueError
    except ValueError, KeyError, TypeError, AttributeError:
        errors.append("ci: public-tree checker requires a commit and verified SHA-256")
    try:
        if not _publishes_only_checked_client_wheel(workflows["publish-client"]):
            raise ValueError
    except ValueError, KeyError, TypeError, AttributeError, IndexError:
        errors.append("publish-client: only checked client wheels can be published")
    try:
        matrix = ci["checks"]["strategy"]["matrix"]
        image_pins = set(
            re.findall(
                r"^FROM python:(\d+\.\d+\.\d+)-",
                (root / "Dockerfile").read_text(),
                re.M,
            )
        )
        if len(image_pins) != 1 or set(matrix["python"]) != image_pins:
            errors.append("ci: checks: Python matrix must match the image Python pin")
        floor = tomllib.loads((root / "pyproject.toml").read_text())["project"][
            "requires-python"
        ].removeprefix(">=")
        if {"check": "unit", "python": floor} not in matrix["include"]:
            errors.append(
                "ci: checks: unit matrix must include the declared Python floor"
            )
    except OSError, ValueError, KeyError, TypeError:
        errors.append("ci: checks: invalid Python matrix or version sources")
    final = ci.get("ci-complete", {})
    if final.get("if") != "always()" or set(final.get("needs", [])) != set(ci) - {
        "ci-complete"
    }:
        errors.append("ci-complete must depend on every job and always run")
    try:
        packages = json.loads((root / "release-please-config.json").read_text())[
            "packages"
        ]
        manifest = json.loads((root / ".release-please-manifest.json").read_text())
        if set(packages) != {"server", "client"} or set(manifest) != set(packages):
            errors.append("separate server and client releases required")
        if len({value["component"] for value in packages.values()}) != 2:
            errors.append("separate release tag components required")
    except OSError, ValueError, KeyError, TypeError:
        errors.append("release manifest configuration invalid")
    return errors


def check_packages(
    root: Path = ROOT, packages: tuple[str, ...] = ("client", "server")
) -> None:
    """Build both distributions. Install each wheel without the workspace."""
    environment = {**os.environ, "UV_LINK_MODE": "copy"}
    with tempfile.TemporaryDirectory(
        prefix="assist-live-packages-", dir=os.environ.get("RUNNER_TEMP")
    ) as directory:
        scratch = Path(directory)
        built: dict[str, Path] = {}
        build_packages = ("client", "server") if "server" in packages else packages
        for package in build_packages:
            output = scratch / package
            subprocess.run(
                [
                    "uv",
                    "build",
                    "--package",
                    f"assist-live-{package}",
                    "--out-dir",
                    str(output),
                ],
                cwd=root,
                env=environment,
                check=True,
            )
            wheels = list(output.glob("*.whl"))
            if len(wheels) != 1 or len(list(output.glob("*.tar.gz"))) != 1:
                raise ValueError(f"{package}: wheel and sdist required")
            with zipfile.ZipFile(wheels[0]) as wheel:
                if package == "client" and any(
                    name.startswith("assist_live_server/") for name in wheel.namelist()
                ):
                    raise ValueError("server module in client wheel")
            built[package] = wheels[0]
        for package in packages:
            venv = scratch / f"{package}-venv"
            subprocess.run(
                ["uv", "venv", str(venv)], cwd=scratch, env=environment, check=True
            )
            python = venv / "bin/python"
            subprocess.run(
                ["uv", "pip", "install", "--python", str(python), str(built[package])]
                + ([str(built["client"])] if package == "server" else []),
                cwd=scratch,
                env=environment,
                check=True,
            )
            code = f"import assist_live_{package}"
            if package == "client":
                code += (
                    "; import importlib.util; "
                    "assert importlib.util.find_spec('assist_live_server') is None"
                )
            subprocess.run([str(python), "-I", "-c", code], cwd=scratch, check=True)
    print("PASS isolated " + " and ".join(packages) + " packages")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packages", action="store_true")
    parser.add_argument("--package", choices=("client", "server"))
    args = parser.parse_args()
    if args.packages or args.package:
        try:
            check_packages(
                packages=(args.package,) if args.package else ("client", "server")
            )
        except (OSError, ValueError, subprocess.CalledProcessError) as failure:
            print(f"FAIL isolated packages: {failure}")
            return 1
        return 0
    errors = check_ci()
    for error in errors:
        print(f"FAIL CI: {error}")
    if errors:
        return 1
    print("PASS CI boundaries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Check CI boundaries and the separate Python distributions."""

import argparse
import json
import os
import re
import subprocess
import tempfile
import zipfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
APPROVAL = "${{ vars.ASSIST_LIVE_RELEASES_APPROVED }}"


def check_ci(root: Path = ROOT) -> list[str]:
    errors: list[str] = []
    workflows = {}
    for name in ("ci", "release-please", "release-artifacts"):
        path = root / f".github/workflows/{name}.yml"
        try:
            text = path.read_text()
            workflow = yaml.safe_load(text)
            workflows[name] = workflow
            if workflow.get("permissions") != {"contents": "read"}:
                errors.append(f"{name}: top-level read-only permissions required")
            for job in workflow["jobs"].values():
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
        for package in packages:
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
            venv = scratch / f"{package}-venv"
            subprocess.run(
                ["uv", "venv", str(venv)], cwd=scratch, env=environment, check=True
            )
            python = venv / "bin/python"
            subprocess.run(
                ["uv", "pip", "install", "--python", str(python), str(wheels[0])],
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

"""Check client publication and the separate Python distributions."""

import argparse
import os
import subprocess
import tempfile
import zipfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


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
    # PyYAML uses YAML 1.1 and reads the GitHub key `on` as True.
    triggers = workflow.get("on", workflow.get(True))
    return (
        triggers == {"workflow_dispatch": None}
        and workflow.get("permissions") == {"contents": "read"}
        and jobs["build"].get("permissions", {"contents": "read"})
        == {"contents": "read"}
        and jobs["publish"].get("permissions") == {"id-token": "write"}
        and set(jobs) == {"build", "publish"}
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
    path = root / ".github/workflows/publish-client.yml"
    try:
        workflow = yaml.safe_load(path.read_text())
    except OSError, ValueError, yaml.YAMLError:
        return ["publish-client: invalid workflow"]
    try:
        if _publishes_only_checked_client_wheel(workflow):
            return []
    except ValueError, KeyError, TypeError, AttributeError, IndexError:
        pass
    return ["publish-client: only checked client wheels can be published"]


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
    print("PASS client publication check")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

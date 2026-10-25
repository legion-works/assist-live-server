import copy
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_server_wheel_requires_client(tmp_path):
    import email
    import subprocess
    import tomllib
    import zipfile

    subprocess.run(
        ["uv", "build", "--package", "assist-live-server", "--out-dir", str(tmp_path)],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    wheel_path = next(tmp_path.glob("*.whl"))
    with zipfile.ZipFile(wheel_path) as wheel:
        metadata_path = next(
            n for n in wheel.namelist() if n.endswith(".dist-info/METADATA")
        )
        metadata = email.message_from_bytes(wheel.read(metadata_path))
    client_version = tomllib.loads((ROOT / "client/pyproject.toml").read_text())[
        "project"
    ]["version"]
    assert f"assist-live-client=={client_version}" in metadata.get_all(
        "Requires-Dist", []
    )


def test_ci_structure_and_pins():
    from tools.check_ci import check_ci

    assert not check_ci(ROOT)


@pytest.mark.parametrize("result", ["failure", "skipped", "cancelled", "success"])
def test_ci_complete_fails_unless_all_jobs_succeed(tmp_path, result):
    import json
    import os
    import subprocess

    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    jobs = workflow["jobs"]
    final = jobs["ci-complete"]
    assert final["if"] == "always()"
    assert set(final["needs"]) == set(jobs) - {"ci-complete"}
    results = {name: {"result": "success"} for name in final["needs"]}
    results[final["needs"][0]]["result"] = result
    step = final["steps"][0]
    assert step["env"]["NEEDS_JSON"] == "${{ toJSON(needs) }}"
    completed = subprocess.run(
        ["bash", "-e", "-c", step["run"]],
        env={**os.environ, "NEEDS_JSON": json.dumps(results)},
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert (completed.returncode == 0) == (result == "success"), completed.stdout


def test_release_guard_and_action_pin_mutations(tmp_path):
    import shutil

    from tools.check_ci import check_ci

    shutil.copytree(ROOT / ".github", tmp_path / ".github")
    for filename in ("release-please-config.json", ".release-please-manifest.json"):
        shutil.copyfile(ROOT / filename, tmp_path / filename)
    assert not check_ci(tmp_path)
    path = tmp_path / ".github/workflows/release-artifacts.yml"
    original = path.read_text()
    workflow = yaml.safe_load(original)
    mutated = copy.deepcopy(workflow)
    del mutated["jobs"]["client"]["steps"][0]["env"]
    path.write_text(yaml.safe_dump(mutated))
    assert check_ci(tmp_path)
    path.write_text(original.replace("34e114876b0b11c390a56381ad16ebd13914f8d5", "v4"))
    assert check_ci(tmp_path)


@pytest.mark.parametrize("approval", ["", "false", "TRUE", "true"])
def test_release_guard_runs_fail_closed(tmp_path, approval):
    import os
    import subprocess

    for name in ("release-please", "release-artifacts"):
        workflow = yaml.safe_load((ROOT / f".github/workflows/{name}.yml").read_text())
        for job in workflow["jobs"].values():
            guard = job["steps"][0]
            assert guard["env"]["RELEASES_APPROVED"] == (
                "${{ vars.ASSIST_LIVE_RELEASES_APPROVED }}"
            )
            result = subprocess.run(
                ["bash", "-e", "-c", guard["run"]],
                env={**os.environ, "RELEASES_APPROVED": approval},
                cwd=tmp_path,
                capture_output=True,
            )
            assert (result.returncode == 0) == (approval == "true")

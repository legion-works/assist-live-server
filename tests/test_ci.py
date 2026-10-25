import copy
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_ci_and_task_fetch_pinned_turn_models(tmp_path):
    import hashlib
    import json
    import os
    import shlex
    import subprocess

    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    steps = workflow["jobs"]["checks"]["steps"]
    fetch = next(
        step for step in steps if step.get("name") == "Fetch pinned turn models"
    )
    assert fetch["if"] == "matrix.check == 'unit'"
    task = yaml.safe_load((ROOT / "Taskfile.yml").read_text())
    assert task["tasks"]["unit"]["cmds"][0] == fetch["run"]
    records = json.loads((ROOT / "assets/manifest.json").read_text())["assets"]
    selected = [
        row for row in records if row["id"] in ("silero-vad", "smart-turn-v3.2-cpu")
    ]
    assert len(selected) == 2
    assert all(len(row["sha256"]) == 64 and row["revision"] for row in selected)
    source = tmp_path / "model"
    source.write_bytes(b"synthetic model bytes")
    for record in selected:
        record["url"] = source.as_uri()
        record["sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps({"download_plan": [r["id"] for r in selected], "assets": selected})
    )
    env = {
        **os.environ,
        "ASSIST_LIVE_ASSET_MANIFEST": str(manifest),
        "ASSIST_LIVE_MODEL_CACHE": str(tmp_path / "cache"),
    }
    command = shlex.split(fetch["run"])
    good = subprocess.run(command, cwd=ROOT, env=env, text=True, capture_output=True)
    assert good.returncode == 0, good.stdout + good.stderr
    assert "PASS pinned turn models fetched with SHA-256 checks" in good.stdout
    for record in selected:
        assert (
            hashlib.sha256(
                (tmp_path / "cache" / f"{record['id']}.onnx").read_bytes()
            ).hexdigest()
            == record["sha256"]
        )
    source.write_bytes(b"wrong hash")
    env["ASSIST_LIVE_MODEL_CACHE"] = str(tmp_path / "bad-cache")
    bad = subprocess.run(command, cwd=ROOT, env=env, text=True, capture_output=True)
    assert bad.returncode == 1
    assert "SHA-256 mismatch" in bad.stdout


@pytest.mark.parametrize("direct_wheel", [False, True])
def test_server_wheel_requires_client(tmp_path, direct_wheel):
    import email
    import subprocess
    import tomllib
    import zipfile

    subprocess.run(
        ["uv", "build", "--package", "assist-live-server", "--out-dir", str(tmp_path)]
        + (["--wheel"] if direct_wheel else []),
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
    requirements = metadata.get_all("Requires-Dist", [])
    assert "numpy==2.5.4" in requirements
    assert "onnxruntime==1.31.0" in requirements
    assert not any("torch" in value or "silero-vad" in value for value in requirements)
    with zipfile.ZipFile(wheel_path) as wheel:
        manifest = wheel.read("assist_live_server/assets/manifest.json")
    assert manifest == (ROOT / "assets/manifest.json").read_bytes()


def test_client_wheel_has_no_server_or_manifest(tmp_path):
    import subprocess
    import zipfile

    subprocess.run(
        ["uv", "build", "--package", "assist-live-client", "--out-dir", str(tmp_path)],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    with zipfile.ZipFile(next(tmp_path.glob("*.whl"))) as wheel:
        assert not any(
            name.startswith("assist_live_server/") or name.endswith("manifest.json")
            for name in wheel.namelist()
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

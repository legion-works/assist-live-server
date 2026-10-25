import copy
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_secret_scans_include_history_and_full_checkout(tmp_path):
    import json
    import os
    import subprocess
    import sys

    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    steps = workflow["jobs"]["secret-scan"]["steps"]
    checkout = next(
        step for step in steps if step.get("uses", "").startswith("actions/checkout@")
    )
    assert checkout["with"]["fetch-depth"] == 0
    scan = next(step for step in steps if step.get("name") == "Scan history and files")
    script = tmp_path / "gitleaks"
    script.write_text(
        f"#!{sys.executable}\nimport json, os, sys\n"
        "with open(os.environ['CALLS'], 'a') as f:\n"
        "    f.write(json.dumps(sys.argv[1:]) + '\\n')\n"
    )
    script.chmod(0o755)
    log = tmp_path / "calls"
    env = {
        **os.environ,
        "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
        "CALLS": str(log),
    }
    subprocess.run(
        ["bash", "-e", "-c", scan["run"]],
        env=env,
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    assert {json.loads(line)[0] for line in log.read_text().splitlines()} == {
        "git",
        "dir",
    }
    task = yaml.safe_load((ROOT / "Taskfile.yml").read_text())
    log.write_text("")
    for command in task["tasks"]["secret-scan"]["cmds"]:
        subprocess.run(
            ["bash", "-e", "-c", command],
            env=env,
            cwd=tmp_path,
            check=True,
            capture_output=True,
        )
    assert {json.loads(line)[0] for line in log.read_text().splitlines()} == {
        "git",
        "dir",
    }


def test_ci_runs_tests_on_declared_floor():
    import tomllib

    floor = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"][
        "requires-python"
    ].removeprefix(">=")
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    job = workflow["jobs"]["checks"]
    matrix = job["strategy"]["matrix"]
    assert {"check": "unit", "python": floor} in matrix["include"]
    setup = next(
        step
        for step in job["steps"]
        if step.get("uses", "").startswith("astral-sh/setup-uv@")
    )
    assert setup["with"]["python-version"] == "${{ matrix.python }}"
    run = next(step for step in job["steps"] if step.get("run") == 'task "$CHECK"')
    assert run["env"]["CHECK"] == "${{ matrix.check }}"
    task = yaml.safe_load((ROOT / "Taskfile.yml").read_text())
    assert "uv run --locked pytest -q" in task["tasks"]["unit"]["cmds"]


@pytest.mark.parametrize("use_denylist", [False, True])
def test_public_tree_is_in_local_and_ci_gates(tmp_path, use_denylist):
    import json
    import os
    import subprocess
    import sys

    task = yaml.safe_load((ROOT / "Taskfile.yml").read_text())
    assert {"task": "public-tree"} in task["tasks"]["check"]["cmds"]
    command = task["tasks"]["public-tree"]["cmds"][0]
    workspace = tmp_path / "workspace"
    server = workspace / "server"
    server.mkdir(parents=True)
    checker = workspace / "assist-live/tools/check_public_tree.py"
    checker.parent.mkdir(parents=True)
    checker.write_text("# Synthetic command-path fixture\n")
    script = tmp_path / "uv"
    script.write_text(
        f"#!{sys.executable}\nimport json, os, sys\n"
        "with open(os.environ['CALLS'], 'w') as f:\n"
        "    json.dump(sys.argv[1:], f)\n"
    )
    script.chmod(0o755)
    log = tmp_path / "calls"
    denylist = tmp_path / "denylist.txt"
    denylist.write_text("synthetic-only-marker\n")
    env = {
        **os.environ,
        "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
        "CALLS": str(log),
        "PRIVATE_DENYLIST": str(denylist) if use_denylist else "",
        "RUNNER_TEMP": str(tmp_path / "runner"),
    }
    Path(env["RUNNER_TEMP"]).mkdir()
    curl = tmp_path / "curl"
    curl.write_text(
        f"#!{sys.executable}\nimport os, pathlib, sys\n"
        "if os.environ.get('FAIL_CURL'): sys.exit(22)\n"
        "pathlib.Path(sys.argv[-1]).write_text('# fixture\\n')\n"
    )
    curl.chmod(0o755)
    subprocess.run(
        ["bash", "-e", "-c", command],
        cwd=server,
        env=env,
        check=True,
        capture_output=True,
    )
    args = json.loads(log.read_text())
    assert args[:6] == [
        "run",
        "--no-project",
        "--python",
        "3.14",
        "python",
        "../assist-live/tools/check_public_tree.py",
    ]
    assert "--history" in args and args[args.index("--root") + 1] == "."
    assert ("--private-denylist" in args) == use_denylist
    if use_denylist:
        assert args[args.index("--private-denylist") + 1] == str(denylist)
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    checks = workflow["jobs"]["checks"]
    assert "public-tree" in checks["strategy"]["matrix"]["check"]
    checkout = next(
        step
        for step in checks["steps"]
        if step.get("uses", "").startswith("actions/checkout@")
    )
    assert checkout["with"]["fetch-depth"] == 0
    assert (
        len(
            [
                step
                for step in checks["steps"]
                if step.get("uses", "").startswith("actions/checkout@")
            ]
        )
        == 1
    )
    public = next(
        step for step in checks["steps"] if step.get("name") == "Check public tree"
    )
    assert public["if"] == "matrix.check == 'public-tree'"
    assert (
        "curl -fsSL https://raw.githubusercontent.com/legion-works/assist-live/main/"
        'tools/check_public_tree.py -o "$RUNNER_TEMP/check_public_tree.py"'
        in public["run"]
    )
    log.unlink()
    ci_result = subprocess.run(
        ["bash", "-e", "-c", public["run"]],
        cwd=server,
        env=env,
        check=True,
        capture_output=True,
    )
    import hashlib

    assert hashlib.sha256(b"# fixture\n").hexdigest().encode() in ci_result.stdout
    ci_args = json.loads(log.read_text())
    assert ci_args[:5] == args[:5]
    assert ci_args[5] == str(Path(env["RUNNER_TEMP"]) / "check_public_tree.py")
    assert ci_args[6:] == args[6:]
    log.unlink()
    failed = subprocess.run(
        ["bash", "-e", "-c", public["run"]],
        cwd=server,
        env={**env, "FAIL_CURL": "1"},
        capture_output=True,
    )
    assert failed.returncode == 22
    assert not log.exists()
    checker.unlink()
    result = subprocess.run(
        ["bash", "-e", "-c", command],
        cwd=server,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == "NOT CHECKED public tree"
    assert not log.exists()
    assert not (ROOT / "tools/check_public_tree.py").exists()
    assert not (ROOT / "tests/test_public_tree.py").exists()
    install = next(
        step for step in checks["steps"] if step.get("name") == "Install Gitleaks"
    )
    assert install["if"] == "matrix.check == 'unit' || matrix.check == 'public-tree'"
    assert (
        "79a3ab579b53f71efd634f3aaf7e04a0fa0cf206b7ed434638d1547a2470a66e"
        in install["run"]
    )


def test_ci_matches_shared_workflow_tool_versions():
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    steps = workflow["jobs"]["checks"]["steps"]
    task = next(
        step for step in steps if step.get("uses", "").startswith("go-task/setup-task@")
    )
    assert task["with"]["version"] == "3.51.1"
    actionlint = next(
        step for step in steps if step.get("name") == "Install actionlint"
    )
    assert "actionlint_1.7.12_linux_amd64.tar.gz" in actionlint["run"]
    assert (
        "8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8"
        in actionlint["run"]
    )
    assert "sha256sum -c -" in actionlint["run"]


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
        record["path"] = f"models/{record['id']}.onnx"
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "download_plan": [
                    {k: r[k] for k in ("id", "url", "sha256", "path")} for r in selected
                ],
                "assets": selected,
            }
        )
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
                (tmp_path / "cache" / record["path"]).read_bytes()
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

    completed = subprocess.run(
        [
            "uv",
            "build",
            "--offline",
            "--package",
            "assist-live-server",
            "--out-dir",
            str(tmp_path),
        ]
        + (["--wheel"] if direct_wheel else []),
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
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
        [
            "uv",
            "build",
            "--offline",
            "--package",
            "assist-live-client",
            "--out-dir",
            str(tmp_path),
        ],
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

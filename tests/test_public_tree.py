import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "tools/check_public_tree.py"


def git(root, *args):
    return subprocess.check_output(
        ["git", "-C", str(root), *args],
        env={
            **os.environ,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_AUTHOR_NAME": "Project tests",
            "GIT_COMMITTER_NAME": "Project tests",
            "GIT_AUTHOR_EMAIL": "tests@example.invalid",
            "GIT_COMMITTER_EMAIL": "tests@example.invalid",
        },
        stderr=subprocess.PIPE,
        text=True,
    ).strip()


@pytest.fixture
def repository(tmp_path):
    root = tmp_path / "public"
    root.mkdir()
    git(root, "init", "-b", "main")
    return root


def run_checker(root, denylist=None, *, history=True, env=None):
    return subprocess.run(
        [sys.executable, str(CHECKER), "--root", str(root)]
        + (["--history"] if history else [])
        + (["--private-denylist", str(denylist)] if denylist else []),
        env=env,
        capture_output=True,
        text=True,
    )


def test_optional_denylist_is_explicit(repository):
    result = run_checker(repository)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.count("NOT CHECKED private denylist") == 1
    assert "PASS public data matches=0" in result.stdout
    assert "PASS history secrets commits=0" in result.stdout


@pytest.mark.parametrize(
    "payload",
    [
        "/" + "home" + "/fixture",
        "/" + "Users" + "/fixture",
        "C:" + "\\Users\\fixture",
        "10" + ".1.2.3.",
        "172" + ".20.1.2",
        "192" + ".168.1.2",
        "169" + ".254.1.2",
        "100" + ".64.1.2",
        "fd" + "12:3456::1",
    ],
)
def test_private_paths_and_addresses_fail_without_disclosure(repository, payload):
    (repository / "data.txt").write_text(payload)
    result = run_checker(repository)
    assert result.returncode == 1
    assert "FAIL public data" in result.stdout
    assert payload not in result.stdout
    assert str(repository) not in result.stdout


def test_public_addresses_and_urls_are_permitted(repository):
    (repository / "data.txt").write_text(
        "192.0.2.1 198.51.100.8 203.0.113.9 127.0.0.1\nhttps://example.com/home/user\n2026.10.3\n"
    )
    result = run_checker(repository)
    assert result.returncode == 0, result.stdout


def test_removed_private_text_is_checked_in_history(repository, tmp_path):
    denylist = tmp_path / "denylist.txt"
    denylist.write_text("synthetic-restricted-marker\n")
    path = repository / "data.txt"
    path.write_text("synthetic-restricted-marker\n")
    git(repository, "add", "data.txt")
    git(repository, "commit", "-m", "Add synthetic fixture")
    path.write_text("Public text.\n")
    git(repository, "add", "data.txt")
    git(repository, "commit", "-m", "Replace synthetic fixture")
    assert run_checker(repository, denylist, history=False).returncode == 0
    result = run_checker(repository, denylist)
    assert result.returncode == 1
    assert "denylist=1" in result.stdout
    assert "synthetic-restricted-marker" not in result.stdout
    assert "NOT CHECKED private denylist" not in result.stdout


def test_denylist_is_external_and_nonempty(repository, tmp_path):
    denylist = repository / "denylist.txt"
    denylist.write_text("synthetic-restricted-marker\n")
    assert run_checker(repository, denylist).returncode == 1
    denylist = tmp_path / "empty.txt"
    denylist.write_text("# no entries\n")
    assert run_checker(repository, denylist).returncode == 1


def test_missing_history_scanner_fails_closed(repository, tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "git").symlink_to(shutil.which("git"))
    result = run_checker(repository, env={**os.environ, "PATH": str(bin_dir)})
    assert result.returncode == 1
    assert "FAIL history secrets missing_tool=1" in result.stdout


def test_nested_root_is_rejected(repository):
    nested = repository / "nested"
    nested.mkdir()
    result = run_checker(nested)
    assert result.returncode == 1
    assert "FAIL own Git root" in result.stdout


def test_symlink_is_not_followed(repository, tmp_path):
    target = tmp_path / "outside.txt"
    target.write_text("Public text.\n")
    (repository / "link").symlink_to(target)
    result = run_checker(repository)
    assert result.returncode == 1
    assert "file_type=1" in result.stdout

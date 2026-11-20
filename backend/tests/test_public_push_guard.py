"""A normal Git push must audit the ref in its calling worktree."""

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CHECKER = ROOT / "scripts/check_public_push.py"
HOOK = ROOT / ".githooks/pre-push"
PUBLIC_URL = "https://github.com/mschr703/jarvis-local-assistant.git"
PRIVATE_URL = "https://github.com/mschr703CODEX/jarvis-private-modules.git"


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True,
                          capture_output=True, text=True).stdout.strip()


def new_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "synthetic repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "tester@example.test")
    git(repo, "config", "user.name", "Synthetic Test")
    git(repo, "config", "commit.gpgsign", "false")
    (repo / "README.md").write_text("safe\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "commit", "-qm", "safe initial commit")
    return repo


def ref_line(repo: Path, ref: str = "HEAD") -> str:
    oid = git(repo, "rev-parse", ref)
    return f"refs/heads/main {oid} refs/heads/main {'0' * len(oid)}\n"


def run_checker(repo: Path, url: str = PUBLIC_URL, ref: str = "HEAD") -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(CHECKER), "origin", url], cwd=repo, check=False,
                          input=ref_line(repo, ref), capture_output=True, text=True)


def test_public_remote_recognition_keeps_private_module_remote_out():
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        spec = importlib.util.spec_from_file_location("jarvis_push_guard", CHECKER)
        assert spec and spec.loader
        guard = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(guard)
        assert guard.is_public_jarvis_remote(PUBLIC_URL)
        assert guard.is_public_jarvis_remote("git@github.com:mschr703/jarvis-local-assistant.git")
        assert not guard.is_public_jarvis_remote(PRIVATE_URL)
    finally:
        sys.path.remove(str(ROOT / "scripts"))


def test_force_added_kangentic_board_blocks_public_push(tmp_path):
    repo = new_repo(tmp_path)
    (repo / ".gitignore").write_text("kangentic.json\n", encoding="utf-8")
    (repo / "kangentic.json").write_text('{"board":"synthetic"}\n', encoding="utf-8")
    git(repo, "add", ".gitignore")
    git(repo, "add", "-f", "kangentic.json")
    git(repo, "commit", "-qm", "force-added local board")
    result = run_checker(repo)
    assert result.returncode == 1
    assert "kangentic.json" in result.stderr
    assert "local Kangentic board or project data" in result.stderr
    assert "synthetic" not in result.stderr


def test_deleted_binary_secret_and_non_head_ref_block_public_push(tmp_path):
    repo = new_repo(tmp_path)
    git(repo, "checkout", "-qb", "feature")
    secret = b"sk-or-v1-" + b"x" * 30
    (repo / "picture.bin").write_bytes(b"\x00binary\x00" + secret + b"\x00")
    git(repo, "add", "picture.bin")
    git(repo, "commit", "-qm", "synthetic binary fixture")
    (repo / "picture.bin").unlink()
    git(repo, "add", "-u")
    git(repo, "commit", "-qm", "remove synthetic binary fixture")
    pushed_sha = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "main")
    result = run_checker(repo, ref=pushed_sha)
    assert result.returncode == 1
    assert "OpenRouter key" in result.stderr
    assert secret.decode() not in result.stderr


def test_clean_public_ref_passes_and_private_remote_is_unrestricted(tmp_path):
    repo = new_repo(tmp_path)
    public = run_checker(repo)
    assert public.returncode == 0, public.stderr
    (repo / "kangentic.json").write_text("local only", encoding="utf-8")
    git(repo, "add", "-f", "kangentic.json")
    git(repo, "commit", "-qm", "private fixture")
    private = run_checker(repo, url=PRIVATE_URL)
    assert private.returncode == 0, private.stderr


def test_versioned_hook_runs_from_linked_worktree(tmp_path):
    shell = shutil.which("sh") or r"C:\Program Files\Git\bin\sh.exe"
    if not Path(shell).is_file():
        pytest.skip("Git shell is unavailable")
    repo = new_repo(tmp_path)
    linked = tmp_path / "linked Kangentic worktree"
    git(repo, "worktree", "add", "-qb", "kangentic", str(linked))
    git(repo, "config", "--local", "core.hooksPath", str(ROOT / ".githooks"))
    assert git(linked, "config", "--get", "core.hooksPath") == str(ROOT / ".githooks")
    (linked / ".kangentic").mkdir()
    (linked / ".kangentic" / "state.json").write_text("local", encoding="utf-8")
    git(linked, "add", "-f", ".kangentic/state.json")
    git(linked, "commit", "-qm", "force-added local state")
    result = subprocess.run([shell, str(HOOK), "origin", PUBLIC_URL], cwd=linked, check=False,
                            input=ref_line(linked), capture_output=True, text=True)
    assert result.returncode == 1
    assert ".kangentic" in result.stderr


def test_git_push_origin_head_to_main_is_blocked_before_upload(tmp_path):
    repo = new_repo(tmp_path)
    bare = tmp_path / "local bare.git"
    subprocess.run(["git", "init", "--bare", "-q", str(bare)], check=True)
    git(repo, "remote", "add", "origin", PUBLIC_URL)
    git(repo, "config", f"url.{bare.as_uri()}.insteadOf", PUBLIC_URL)
    resolved = git(repo, "remote", "get-url", "--push", "origin")
    assert resolved == bare.as_uri()
    git(repo, "config", "--local", "core.hooksPath", str(ROOT / ".githooks"))
    (repo / "kangentic.local.json").write_text("local", encoding="utf-8")
    git(repo, "add", "-f", "kangentic.local.json")
    git(repo, "commit", "-qm", "force-added local settings")
    pushed = subprocess.run(["git", "push", "origin", "HEAD:main"], cwd=repo,
                            check=False, capture_output=True, text=True,
                            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    assert pushed.returncode != 0
    assert "Public push blocked" in pushed.stderr
    uploaded = subprocess.run(["git", "--git-dir", str(bare), "rev-parse", "--verify",
                               "refs/heads/main"], check=False, capture_output=True)
    assert uploaded.returncode != 0

"""Publication guard catches secret-like filenames and synthetic credentials."""

import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("jarvis_public_audit", ROOT / "scripts/audit_public.py")
assert SPEC and SPEC.loader
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)
PATTERNS, sensitive_path = audit.PATTERNS, audit.sensitive_path


def test_history_finds_deleted_secret_before_public_push(tmp_path):
    repo = tmp_path / "history"
    repo.mkdir()

    def git(*args):
        return subprocess.run(["git", *args], cwd=repo, check=True,
                              capture_output=True)

    git("init", "-q")
    git("config", "user.email", "tester@example.test")
    git("config", "user.name", "Synthetic Test")
    secret = b"sk-or-v1-" + b"x" * 30
    secret_file = repo / ".env.local"
    secret_file.write_bytes(b"KEY=" + secret + b"\n")
    git("add", ".env.local")
    git("-c", "commit.gpgsign=false", "commit", "-qm", "synthetic secret")
    secret_file.unlink()
    git("add", "-u")
    git("-c", "commit.gpgsign=false", "commit", "-qm", "delete secret")
    findings = audit.history_findings(repo, secret)
    assert ("HEAD history: .env.local", "local environment file") in findings
    assert ("HEAD history patch", "OpenRouter key") in findings
    assert ("HEAD history patch", "provided secret") in findings


def test_sensitive_filenames_and_public_example():
    for name in (".env", ".env.local", "prod.env.production", "service.env",
                 "vault_credentials.json", "backup.zip", "private.key",
                 "certificate.p12", "jarvis.db", "jarvis.db-journal", "app.log",
                 "browser-profile/Default/Cookies",
                 "chat-attachments/123.txt", "nested/chat-attachments/123.txt"):
        assert sensitive_path(Path(name)), name
    assert sensitive_path(Path(".env.example")) is None
    assert sensitive_path(Path("README.md")) is None


def test_git_ignores_local_secrets_but_not_example():
    for name in (".env.local", "prod.env.production", "backup.zip",
                 "private.key", "vault_credentials.json", "app.log", "jarvis.db-journal"):
        result = subprocess.run(["git", "check-ignore", "--no-index", "-q", name],
                                cwd=ROOT, check=False)
        assert result.returncode == 0, name
    example = subprocess.run(["git", "check-ignore", "--no-index", "-q",
                              ".env.example"], cwd=ROOT, check=False)
    assert example.returncode == 1


def test_synthetic_token_patterns():
    assert PATTERNS["OpenRouter key"].search(b"sk-or-v1-" + b"x" * 30)
    assert PATTERNS["GitHub token"].search(b"ghp_" + b"x" * 35)
    assert PATTERNS["private key block"].search(b"-----BEGIN " + b"PRIVATE KEY-----")


def test_missing_tracked_file_fails_cleanly_before_staging_deletion(tmp_path, monkeypatch,
                                                                   capsys):
    repo = tmp_path / "missing"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / "README.md").write_text("safe\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.email=test@example.test", "-c",
                    "user.name=Synthetic Test", "-c", "commit.gpgsign=false",
                    "commit", "-qm", "safe"], cwd=repo, check=True)
    (repo / "README.md").unlink()
    monkeypatch.setattr(audit, "ROOT", repo)
    monkeypatch.setattr(sys, "argv", ["audit_public.py"])
    assert audit.main() == 1
    assert "tracked file missing or unreadable" in capsys.readouterr().out


def test_staged_secret_is_caught_even_if_worktree_was_cleaned(tmp_path, monkeypatch,
                                                               capsys):
    repo = tmp_path / "staged"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    path = repo / "README.md"
    path.write_text("safe\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.email=test@example.test", "-c",
                    "user.name=Synthetic Test", "-c", "commit.gpgsign=false",
                    "commit", "-qm", "safe"], cwd=repo, check=True)
    path.write_bytes(b"key=sk-or-v1-" + b"x" * 30)
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
    path.write_text("safe again\n", encoding="utf-8")
    monkeypatch.setattr(audit, "ROOT", repo)
    monkeypatch.setattr(sys, "argv", ["audit_public.py"])
    assert audit.main() == 1
    assert "Git index: README.md: OpenRouter key" in capsys.readouterr().out

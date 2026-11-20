"""Check publishable files for obvious credentials and machine-specific markers."""

import argparse
import getpass
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = {
    "OpenRouter key": re.compile(rb"sk-or-v1-[A-Za-z0-9_-]{20,}"),
    "GitHub token": re.compile(rb"(?:github_pat_|ghp_)[A-Za-z0-9_]{20,}"),
    "Google API key": re.compile(rb"AIza[A-Za-z0-9_-]{30,}"),
    "AWS access key": re.compile(rb"AKIA[A-Z0-9]{16}"),
    "private key block": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "local user path": re.compile(rb"(?i)C:\\Users\\mschr(?:\\|\b)"),
    "personal email": re.compile(rb"(?i)mschreiner7031?@gmail\.[A-Za-z]+"),
}


def sensitive_path(relative: Path) -> str | None:
    """Catch forced additions that bypass .gitignore, including binary files."""
    name = relative.name.lower()
    parts = [part.lower() for part in relative.parts]
    if name in {"kangentic.json", "kangentic.local.json"} or ".kangentic" in parts:
        return "local Kangentic board or project data"
    if name == "settings.local.json" and ".claude" in parts:
        return "local agent settings"
    if name == ".env" or name.endswith(".env") or ".env." in name:
        if name == ".env.example":
            return None
        return "local environment file"
    if name == "vault_credentials.json":
        return "local credential vault"
    if name.endswith((".db", ".db-shm", ".db-wal", ".db-journal",
                      ".sqlite", ".sqlite-shm", ".sqlite-wal",
                      ".sqlite3", ".sqlite3-shm", ".sqlite3-wal")):
        return "local database"
    if name.endswith((".pem", ".key", ".p12", ".pfx")):
        return "key or certificate file"
    if name.endswith(".zip"):
        return "archive requiring private-data review"
    if name.endswith(".log"):
        return "local log file"
    if "chat-attachments" in parts or (parts and parts[0] in
                                       {"data", "backups", "browser-profile"}):
        return "local application data"
    return None


def publishable_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT, check=True, capture_output=True,
    )
    return [ROOT / token.decode("utf-8", errors="surrogateescape")
            for token in result.stdout.split(b"\0") if token]


def staged_file_contents(root: Path) -> list[tuple[Path, bytes]]:
    """Inspect the exact bytes Git would commit, even if the worktree differs."""
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z"],
        cwd=root, check=True, capture_output=True,
    )
    staged = []
    for token in result.stdout.split(b"\0"):
        if not token:
            continue
        relative = Path(token.decode("utf-8", errors="surrogateescape"))
        blob = subprocess.run(["git", "show", f":{relative.as_posix()}"],
                              cwd=root, check=True, capture_output=True).stdout
        staged.append((relative, blob))
    return staged


def history_findings(root: Path, secret: bytes = b"", ref: str = "HEAD") -> list[tuple[str, str]]:
    """Scan commits reachable from ref, including files removed before a push."""
    history_label = "HEAD" if ref == "HEAD" else "pushed ref"
    names = subprocess.run(
        ["git", "log", "--format=", "--name-only", "-z", ref],
        cwd=root, check=True, capture_output=True,
    ).stdout
    findings = []
    seen_paths = set()
    for token in names.split(b"\0"):
        if not token:
            continue
        relative = Path(token.decode("utf-8", errors="surrogateescape"))
        label = sensitive_path(relative)
        if label and (str(relative), label) not in seen_paths:
            findings.append((f"{history_label} history: {relative}", label))
            seen_paths.add((str(relative), label))
    patch = subprocess.run(
        ["git", "log", "--format=", "--patch", ref],
        cwd=root, check=True, capture_output=True,
    ).stdout
    for label, pattern in PATTERNS.items():
        if pattern.search(patch):
            findings.append((f"{history_label} history patch", label))
    if secret and secret in patch:
        findings.append((f"{history_label} history patch", "provided secret"))
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-stdin-secret", action="store_true",
                        help="read one secret without echo and compare against publishable files")
    args = parser.parse_args()
    secret = getpass.getpass("Secret to compare: ").encode() if args.check_stdin_secret else b""
    findings: list[tuple[str, str]] = []
    files = publishable_files()
    for path in files:
        relative_path = path.relative_to(ROOT)
        relative = str(relative_path)
        file_risk = sensitive_path(relative_path)
        if file_risk:
            findings.append((relative, file_risk))
            continue
        if path.is_symlink():
            findings.append((relative, "tracked link requires manual review"))
            continue
        try:
            content = path.read_bytes()
        except OSError:
            findings.append((relative, "tracked file missing or unreadable; stage deletion or restore"))
            continue
        for label, pattern in PATTERNS.items():
            if pattern.search(content):
                findings.append((relative, label))
        if secret and secret in content:
            findings.append((relative, "provided secret"))
    staged = staged_file_contents(ROOT)
    for relative_path, content in staged:
        relative = str(relative_path)
        for label, pattern in PATTERNS.items():
            if pattern.search(content):
                findings.append((f"Git index: {relative}", label))
        if secret and secret in content:
            findings.append((f"Git index: {relative}", "provided secret"))
    findings.extend(history_findings(ROOT, secret))
    for name, label in findings:
        print(f"{name}: {label}")
    print(f"Scanned {len(files)} publishable files, {len(staged)} staged blobs and HEAD history; "
          f"{len(findings)} findings.")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())

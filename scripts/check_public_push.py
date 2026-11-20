"""Pre-push audit of the exact Git refs sent to a public Jarvis repository.

Git invokes this from the checkout that is pushing, including a linked
worktree. The script itself may live in a different checkout because the
installer points every worktree at one absolute, versioned hook directory.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

from audit_public import PATTERNS, history_findings, sensitive_path

ZERO_SHA = re.compile(r"^0{40,64}$")
HEX_SHA = re.compile(r"^[0-9a-fA-F]{40,64}$")


def is_public_jarvis_remote(url: str) -> bool:
    """Recognize HTTPS and SSH GitHub URLs for Jarvis source repositories."""
    value = url.strip()
    if value.lower().startswith("git@github.com:"):
        host, path = "github.com", value.split(":", 1)[1]
    elif "://" in value:
        parsed = urlsplit(value)
        host, path = (parsed.hostname or "").lower(), parsed.path
    else:
        return False
    pieces = path.strip("/").split("/")
    if len(pieces) != 2:
        return False
    repository = pieces[1].removesuffix(".git").lower()
    return host == "github.com" and repository == "jarvis-local-assistant"


def git(root: Path, *args: str, input_bytes: bytes | None = None) -> bytes:
    return subprocess.run(
        ["git", *args], cwd=root, input=input_bytes,
        check=True, capture_output=True,
    ).stdout


def is_public_push_target(root: Path, remote_name: str, remote_url: str) -> bool:
    """Check the resolved URL and its unrewritten Git configuration."""
    if is_public_jarvis_remote(remote_url):
        return True
    for setting in ("pushurl", "url"):
        configured = subprocess.run(
            ["git", "config", "--get-all", f"remote.{remote_name}.{setting}"],
            cwd=root, check=False, capture_output=True, text=True,
        )
        if configured.returncode == 0:
            return any(is_public_jarvis_remote(url) for url in configured.stdout.splitlines())
        if configured.returncode != 1:
            raise RuntimeError("Git remote configuration could not be read")
    return False


def pushed_commits(stdin: str, root: Path) -> list[str]:
    commits: list[str] = []
    for line in stdin.splitlines():
        fields = line.split()
        if len(fields) != 4:
            raise ValueError("malformed pre-push ref line")
        local_ref, local_sha, _remote_ref, _remote_sha = fields
        if not HEX_SHA.fullmatch(local_sha):
            raise ValueError(f"invalid local object ID for {local_ref}")
        if ZERO_SHA.fullmatch(local_sha):  # Ref deletion uploads no object.
            continue
        commit = git(root, "rev-parse", "--verify", f"{local_sha}^{{commit}}")
        resolved = commit.decode("ascii").strip()
        if resolved not in commits:
            commits.append(resolved)
    return commits


def tree_findings(root: Path, commit: str) -> list[tuple[str, str]]:
    findings: list[tuple[str, str]] = []
    for entry in git(root, "ls-tree", "-r", "-z", commit).split(b"\0"):
        if not entry:
            continue
        meta, raw_path = entry.split(b"\t", 1)
        mode, _kind, _oid = meta.split()
        path = Path(raw_path.decode("utf-8", errors="surrogateescape"))
        label = sensitive_path(path)
        if label:
            findings.append((f"pushed tree: {path}", label))
        if mode == b"120000":
            findings.append((f"pushed tree: {path}", "tracked link requires review"))
    return findings


def blob_findings(root: Path, commit: str) -> list[tuple[str, str]]:
    """Scan every reachable blob, including binary files deleted in a later commit."""
    object_ids = git(root, "rev-list", "--objects", "--no-object-names", commit).splitlines()
    findings: list[tuple[str, str]] = []
    seen: set[str] = set()
    with subprocess.Popen(
        ["git", "cat-file", "--batch"], cwd=root,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    ) as process:
        assert process.stdin and process.stdout
        for oid in object_ids:
            if not HEX_SHA.fullmatch(oid.decode("ascii")):
                raise ValueError("invalid Git object ID")
            process.stdin.write(oid + b"\n")
            process.stdin.flush()
            header = process.stdout.readline().split()
            if len(header) != 3 or header[0] != oid:
                raise ValueError("Git object stream is incomplete")
            size = int(header[2])
            payload = process.stdout.read(size)
            ending = process.stdout.read(1)
            if len(payload) != size or ending != b"\n":
                raise ValueError("Git object stream ended early")
            if header[1] != b"blob":
                continue
            for label, pattern in PATTERNS.items():
                if pattern.search(payload) and label not in seen:
                    findings.append((f"reachable Git blob {oid.decode('ascii')[:12]}", label))
                    seen.add(label)
        process.stdin.close()
        if process.wait(timeout=30) != 0:
            raise RuntimeError("Git object scan failed")
    return findings


def audit_ref(root: Path, commit: str) -> list[tuple[str, str]]:
    findings = tree_findings(root, commit)
    findings.extend(history_findings(root, ref=commit))
    findings.extend(blob_findings(root, commit))
    messages = git(root, "log", "--format=%B", commit)
    for label, pattern in PATTERNS.items():
        if pattern.search(messages):
            findings.append(("pushed commit message", label))
    return list(dict.fromkeys(findings))


def main(argv: list[str] | None = None, stdin: str | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2:
        print("Usage: check_public_push.py <remote-name> <remote-url>", file=sys.stderr)
        return 2
    remote_name, remote_url = args
    root = Path.cwd()
    try:
        if not is_public_push_target(root, remote_name, remote_url):
            return 0
        refs = pushed_commits(sys.stdin.read() if stdin is None else stdin, root)
        findings: list[tuple[str, str]] = []
        for commit in refs:
            findings.extend(audit_ref(root, commit))
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        print("Public push blocked: Git refs could not be audited.", file=sys.stderr)
        return 1
    if findings:
        for path, reason in list(dict.fromkeys(findings)):
            print(f"{path}: {reason}", file=sys.stderr)
        print("Public push blocked by the Jarvis publication audit.", file=sys.stderr)
        return 1
    print(f"Public push audit passed for {len(refs)} ref(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())

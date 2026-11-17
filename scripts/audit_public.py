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


def publishable_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT, check=True, capture_output=True,
    )
    return [ROOT / token.decode("utf-8", errors="surrogateescape")
            for token in result.stdout.split(b"\0") if token]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-stdin-secret", action="store_true",
                        help="read one secret without echo and compare against publishable files")
    args = parser.parse_args()
    secret = getpass.getpass("Secret to compare: ").encode() if args.check_stdin_secret else b""
    findings: list[tuple[str, str]] = []
    files = publishable_files()
    for path in files:
        content = path.read_bytes()
        relative = str(path.relative_to(ROOT))
        for label, pattern in PATTERNS.items():
            if pattern.search(content):
                findings.append((relative, label))
        if secret and secret in content:
            findings.append((relative, "provided secret"))
    for name, label in findings:
        print(f"{name}: {label}")
    print(f"Scanned {len(files)} publishable files; {len(findings)} findings.")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())

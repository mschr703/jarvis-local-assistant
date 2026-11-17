"""Registrierung, konfigurierbare Kombination und verständlicher Konflikt auf Windows."""

import os
import subprocess
import sys
from pathlib import Path

HOTKEY_SCRIPT = Path(__file__).with_name("hotkey.py")


def start(combo: str) -> subprocess.Popen:
    environment = os.environ.copy()
    environment["JARVIS_HOTKEY"] = combo
    return subprocess.Popen([sys.executable, str(HOTKEY_SCRIPT)], env=environment,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, bufsize=1)


def main() -> None:
    for combo in ("CTRL+ALT+SHIFT+9", "CTRL+ALT+SHIFT+8", "CTRL+ALT+SHIFT+7"):
        first = start(combo)
        line = first.stdout.readline()
        if "aktiv" in line:
            break
        first.wait(timeout=5)
    else:
        raise RuntimeError("Keine freie Hotkey-Kombination für isolierte Probe")
    try:
        second = start(combo)
        output, _ = second.communicate(timeout=10)
        assert second.returncode == 2, (second.returncode, output)
        assert "belegt oder nicht verfügbar" in output, output
        assert first.poll() is None
        print({"registered": combo, "conflict_reported": True})
    finally:
        first.terminate()
        first.wait(timeout=5)


if __name__ == "__main__":
    main()

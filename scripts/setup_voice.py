"""Install the local German STT model into Jarvis' private data directory."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from faster_whisper import WhisperModel


def main() -> None:
    windows_home = Path(os.environ.get("USERPROFILE", Path.home()))
    default = (Path(os.environ.get("LOCALAPPDATA", windows_home / "AppData/Local"))
               / "JarvisLocal" if sys.platform == "win32" else Path.home() / ".local/share/jarvis-local")
    root = Path(os.environ.get("JARVIS_VOICE_MODEL_DIR",
                               Path(os.environ.get("JARVIS_DATA_DIR", default)) / "voice-models"))
    root.mkdir(parents=True, exist_ok=True)
    WhisperModel("base", device="cpu", compute_type="int8", download_root=str(root), cpu_threads=4)
    print(f"Lokales Whisper-base-Modell bereit: {root}")


if __name__ == "__main__":
    main()

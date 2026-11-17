"""Startet die echte Ollama-Planprobe mit eigenem Profil und HTTP-Server."""

import socket
import threading
import time
from pathlib import Path
from tempfile import TemporaryDirectory

import uvicorn
from jarvis.app import create_app
from jarvis.config import Settings
from smoke_plan import main as smoke_plan


def main() -> None:
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", 8765)) == 0:
            raise RuntimeError("Port 8765 ist belegt; isolierte Planprobe nicht gestartet")
    with TemporaryDirectory(prefix="jarvis-plan-isolated-") as temporary:
        settings = Settings(JARVIS_DATA_DIR=Path(temporary) / "data",
                            JARVIS_PROVIDER="ollama", JARVIS_OLLAMA_MODEL="qwen3:4b-instruct")
        server = uvicorn.Server(uvicorn.Config(create_app(settings), host="127.0.0.1",
                                               port=8765, log_level="error"))
        worker = threading.Thread(target=server.run, daemon=True)
        worker.start()
        try:
            deadline = time.monotonic() + 30
            while not server.started and worker.is_alive() and time.monotonic() < deadline:
                time.sleep(0.05)
            if not server.started:
                raise RuntimeError("Isolierter Jarvis-Server wurde nicht bereit")
            smoke_plan()
        finally:
            server.should_exit = True
            worker.join(timeout=20)
            if worker.is_alive():
                raise RuntimeError("Isolierter Jarvis-Server konnte nicht gestoppt werden")


if __name__ == "__main__":
    main()

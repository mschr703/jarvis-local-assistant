"""Öffnet ein restauriertes Profil über die echte FastAPI-App und prüft Kernbestände."""

import argparse
import json
from pathlib import Path

from fastapi.testclient import TestClient
from jarvis.app import create_app
from jarvis.config import Settings


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("data_dir", type=Path)
    args = parser.parse_args()
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=args.data_dir))) as client:
        client.get("/session").raise_for_status()
        counts = {name: len(client.get(path).json()) for name, path in {
            "chats": "/chats", "drafts": "/drafts", "plans": "/plans",
            "runs": "/runs", "tasks": "/tasks", "memories": "/memories",
        }.items()}
        counts["exports"] = len(list((args.data_dir / "outputs").glob("*")))
        counts["notes"] = len(list((args.data_dir / "notes").glob("*")))
        assert counts["chats"] >= 1 and counts["drafts"] >= 1 and counts["plans"] >= 1
        assert counts["exports"] >= 1 and counts["notes"] >= 1
        print(json.dumps(counts, ensure_ascii=False))


if __name__ == "__main__":
    main()

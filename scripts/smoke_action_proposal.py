"""Real Ollama smoke test for proposal, preview and write approval boundary.

Usage: backend/.venv/Scripts/python.exe scripts/smoke_action_proposal.py
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

from fastapi.testclient import TestClient
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from jarvis.app import create_app
from jarvis.config import Settings
from jarvis.db import Database
from jarvis.engine import ToolDefinition


class LinkArgs(BaseModel):
    url: str = Field(pattern=r"^https://")
    title: str = Field(min_length=1)


class DraftArgs(BaseModel):
    recipient: str = Field(min_length=1)
    body: str = Field(min_length=1)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="qwen3:4b-instruct")
    parser.add_argument("--only", choices=["note", "link", "draft", "missing", "knowledge"])
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="jarvis-action-") as directory:
        data_dir = Path(directory)
        db = Database(data_dir / "jarvis.db")
        settings = Settings(JARVIS_DATA_DIR=data_dir, JARVIS_OLLAMA_MODEL=args.model)
        app = create_app(settings)
        effects: list[str] = []
        app.state.engine.register(ToolDefinition(
            "save_link", LinkArgs,
            lambda _args: effects.append("link") or {"saved": True},
            scope="local_module", effect="write",
            description="Einen vollständigen HTTPS-Link mit Titel in der lokalen Leseliste speichern.",
        ))
        app.state.engine.register(ToolDefinition(
            "create_draft", DraftArgs,
            lambda _args: effects.append("draft") or {"saved": True},
            scope="local_module", effect="write",
            description="Einen lokalen Nachrichtenentwurf mit Empfänger und Text erstellen, ohne zu senden.",
        ))
        with TestClient(app) as client:
            headers = {"x-jarvis-csrf": client.get("/session").json()["csrf"]}
            cases = [
                ("note", "Speichere eine Notiz mit dem Titel Einkauf und dem Inhalt Milch kaufen.",
                 "Notiz vorbereitet.", "save_note", {"title": "Einkauf", "content": "Milch kaufen"}, False),
                ("link", "Füge https://example.org mit dem Titel 'Beispiel' meiner Leseliste hinzu.",
                 "Ich kann den Link für später ablegen.", "save_link",
                 {"url": "https://example.org", "title": "Beispiel"}, False),
                ("draft", "Erstelle einen lokalen Nachrichtenentwurf an Ada mit dem Text Hallo.",
                 "Der Entwurf kann vorbereitet werden; nichts wird gesendet.", "create_draft",
                 {"recipient": "Ada", "body": "Hallo"}, False),
                ("missing", "Erstelle einen Nachrichtenentwurf mit dem Text Hallo. Den Empfänger habe ich noch nicht genannt.",
                 "Dafür brauche ich noch den Empfänger.", None, None, True),
                ("knowledge", "Was ist eine Notiz?", "Eine Notiz hält kurze Informationen fest.",
                 None, None, False),
            ]
            for name, question, answer, expected, expected_args, expect_question in cases:
                if args.only and name != args.only:
                    continue
                runs_before = len(client.get("/runs").json())
                chat = db.create_chat()
                db.add_message(chat["id"], "user", question)
                message = db.add_message(chat["id"], "assistant", answer)
                started = time.monotonic()
                response = client.post(f"/chats/{chat['id']}/action-proposal",
                                       headers=headers, json={"message_id": message["id"]})
                duration = round(time.monotonic() - started, 1)
                if response.status_code != 200:
                    print(f"FAIL {question}: HTTP {response.status_code} {response.text[:300]}")
                    return 1
                action = response.json()["action"]
                clarification = response.json().get("clarification")
                chosen = action["tool_name"] if action else None
                print(json.dumps({"question": question, "selected": chosen,
                                  "args": action["args"] if action else None,
                                  "clarification": clarification,
                                  "selection_s": duration,
                                  "new_runs_before_review": len(client.get("/runs").json()) - runs_before},
                                 ensure_ascii=False))
                if (chosen != expected or (action["args"] if action else None) != expected_args
                        or bool(clarification) != expect_question
                        or len(client.get("/runs").json()) != runs_before):
                    return 1
                if action:
                    run = client.post("/runs", headers=headers, json={
                        "tool_name": chosen, "args": action["args"]})
                    if run.status_code != 200 or run.json()["status"] != "waiting_approval":
                        print(f"FAIL approval gate: {run.status_code} {run.text[:300]}")
                        return 1
                    client.post(f"/runs/{run.json()['id']}/decision", headers=headers,
                                json={"args_hash": run.json()["args_hash"], "approved": False})
                    if list((data_dir / "notes").glob("*.txt")):
                        print("FAIL: note written despite denial")
                        return 1
                    if effects:
                        print("FAIL: module action ran despite denial")
                        return 1
    print(f"PASS: {args.only or 'all'} real local proposal cases; no preview side effect")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

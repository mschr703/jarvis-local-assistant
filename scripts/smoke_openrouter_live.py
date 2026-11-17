"""Small, isolated OpenRouter acceptance run; never stores the API key."""

import getpass
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient
from jarvis.app import create_app
from jarvis.config import Settings

MODEL = "openai/gpt-4o-mini"


def main() -> None:
    key = os.environ.get("JARVIS_OPENROUTER_API_KEY") or getpass.getpass("OpenRouter-Key: ")
    if not key:
        raise SystemExit("OpenRouter-Key fehlt")

    with TemporaryDirectory(prefix="jarvis-openrouter-live-") as directory:
        root = Path(directory)
        settings = Settings(
            JARVIS_DATA_DIR=root / "profile",
            JARVIS_PROVIDER="openrouter",
            JARVIS_OPENROUTER_API_KEY=key,
            JARVIS_OPENROUTER_MODEL=MODEL,
            JARVIS_DAILY_BUDGET_USD=0.02,
            JARVIS_MAX_OUTPUT_TOKENS=48,
        )
        with TestClient(create_app(settings)) as client:
            csrf = client.get("/session").json()["csrf"]
            headers = {"x-jarvis-csrf": csrf}
            health = client.get("/health").json()
            assert health["status"] == "ok" and health["provider"] == "openrouter"

            blocked_chat = client.post("/chats", headers=headers).json()["id"]
            blocked = client.post(
                f"/chats/{blocked_chat}/stream", headers=headers,
                json={"content": "Dieser Text darf nicht in die Cloud."},
            )
            assert blocked.status_code == 422
            assert client.get(f"/chats/{blocked_chat}").json()["messages"] == []

            source_dir = root / "sources"
            source_dir.mkdir()
            (source_dir / "private.txt").write_text("Vertrauliche Testzahl 42", encoding="utf-8")
            folder = client.post("/folders", headers=headers, json={
                "path": str(source_dir), "policy": "local_only",
            }).json()
            indexed = client.post(f"/folders/{folder['id']}/index", headers=headers)
            assert indexed.status_code == 200 and indexed.json()["indexed"] == 1
            passage = client.get("/search?q=Vertrauliche").json()[0]["passage_id"]
            private_draft = client.post("/drafts/generate", headers=headers, json={
                "kind": "answer", "brief": "Nenne die Testzahl",
                "passage_ids": [passage], "allow_cloud": True,
            })
            assert private_draft.status_code == 422

            chat_id = client.post("/chats", headers=headers).json()["id"]
            response = client.post(
                f"/chats/{chat_id}/stream", headers=headers,
                json={"content": "Antworte kurz auf Deutsch: Was ist zwei plus zwei?",
                      "allow_cloud": True},
            )
            events = [line.removeprefix("event: ") for line in response.text.splitlines()
                      if line.startswith("event: ")]
            messages = client.get(f"/chats/{chat_id}").json()["messages"]
            receipts = client.get("/usage").json()
            summary = {
                "http_status": response.status_code,
                "events": events,
                "message_status": messages[-1]["status"] if messages else None,
                "provider": messages[-1]["provider"] if messages else None,
                "model": messages[-1]["model"] if messages else None,
                "input_tokens": messages[-1]["input_tokens"] if messages else None,
                "output_tokens": messages[-1]["output_tokens"] if messages else None,
                "cost_usd": messages[-1]["cost_usd"] if messages else None,
                "cost_kind": messages[-1]["cost_kind"] if messages else None,
                "receipt_status": receipts[0]["status"] if receipts else None,
                "cloud_without_consent_http_status": blocked.status_code,
                "local_only_source_http_status": private_draft.status_code,
            }
            print(json.dumps(summary, ensure_ascii=False))
            assert response.status_code == 200
            assert "done" in events
            assert len(messages) == 2 and messages[-1]["content"].strip()
            assert messages[-1]["provider"] == "openrouter"
            assert messages[-1]["status"] == "complete"
            assert len(receipts) == 1 and receipts[0]["status"] == "complete"

        with TestClient(create_app(settings)) as restarted:
            restarted.get("/session")
            saved = restarted.get(f"/chats/{chat_id}").json()["messages"]
            assert len(saved) == 2 and saved[-1]["status"] == "complete"
            assert restarted.get("/usage").json()[0]["status"] == "complete"
            print("reload=ok")


if __name__ == "__main__":
    main()

"""Isolierte Ende-zu-Ende-Probe mit echtem Ollama und temporärem Profil."""

import json
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
from jarvis.app import create_app
from jarvis.backup import create_backup, restore_backup
from jarvis.config import Settings


def auth(client: TestClient) -> dict:
    return {"x-jarvis-csrf": client.get("/session").json()["csrf"]}


def main() -> None:
    with TemporaryDirectory(prefix="jarvis-e2e-") as directory:
        root = Path(directory)
        data_dir = root / "profile"
        source_dir = root / "sources"
        source_dir.mkdir()
        (source_dir / "atlas.md").write_text(
            "Projekt Atlas: Die Präsentation ist am 12. November. Maria ist verantwortlich.",
            encoding="utf-8",
        )
        settings = Settings(JARVIS_DATA_DIR=data_dir, JARVIS_PROVIDER="ollama",
                            JARVIS_OLLAMA_MODEL="qwen3:4b-instruct")
        with TestClient(create_app(settings)) as client:
            headers = auth(client)
            health = client.get("/health").json()
            assert health["configured"] and health["provider"] == "ollama"
            chat = client.post("/chats", headers=headers).json()
            chat_response = client.post(f"/chats/{chat['id']}/stream", headers=headers,
                                        json={"content": "Antworte kurz auf Deutsch: Was ist zwei plus zwei?"})
            assert chat_response.status_code == 200 and "event: done" in chat_response.text
            messages = client.get(f"/chats/{chat['id']}").json()["messages"]
            assert len(messages) == 2 and messages[-1]["content"].strip()
            assert messages[-1]["provider"] == "ollama"
            assert messages[-1]["cost_kind"] == "local_no_api_charge"
            folder = client.post("/folders", headers=headers,
                                 json={"path": str(source_dir), "policy": "local_only"}).json()
            assert client.post(f"/folders/{folder['id']}/index", headers=headers).json()["indexed"] == 1
            source = client.get("/search?q=Präsentation").json()[0]
            sample = client.post("/style/samples", headers=headers, json={
                "title": "Kurze Antwort", "content": "Guten Tag, vielen Dank. Viele Grüße",
                "policy": "local_only",
            }).json()
            client.put("/style/profile", headers=headers, json={
                "description": "Freundlich, klar und kurz", "policy": "local_only",
            }).raise_for_status()
            drafted = client.post("/drafts/generate", headers=headers, json={
                "kind": "email", "brief": "Beantworte die Frage nach Präsentation und Zuständigkeit",
                "input_text": "Wann ist die Präsentation und wer ist verantwortlich?",
                "passage_ids": [source["passage_id"]], "style_sample_ids": [sample["id"]],
            })
            drafted.raise_for_status()
            draft = drafted.json()
            assert draft["content"].strip() and draft["sources"][0]["path"] == source["path"]
            assert {item["purpose"] for item in client.get("/usage").json()} == {"chat", "draft"}
            exported = client.post(f"/drafts/{draft['id']}/export", headers=headers,
                                   json={"format": "eml", "expected_version": draft["version"]}).json()
            assert Path(exported["path"]).is_file()
            memory = client.post("/memories", headers=headers, json={
                "text": "Maria verantwortet Atlas", "source_ref": "Projektbeschluss",
                "policy": "local_only",
            }).json()
            future = datetime.now(ZoneInfo("Europe/Paris")) + timedelta(days=7)
            task = client.post("/tasks", headers=headers, json={
                "title": "Atlas-Präsentation vorbereiten", "notes": "Quelle: Projektbeschluss",
                "local_due": future.strftime("%Y-%m-%dT%H:%M"), "timezone": "Europe/Paris",
            }).json()
            assert task["due_utc"]
            browser = client.post("/runs", headers=headers, json={
                "tool_name": "browser_read", "args": {"url": "https://example.com/"},
            }).json()
            assert browser["status"] == "succeeded", browser

        with TestClient(create_app(settings)) as restarted:
            auth(restarted)
            assert restarted.get(f"/chats/{chat['id']}").json()["messages"][-1]["content"] == messages[-1]["content"]
            assert restarted.get("/memories?q=Maria").json()[0]["id"] == memory["id"]
            assert restarted.get(f"/drafts/{draft['id']}").json()["content"] == draft["content"]
            fired_at = (datetime.fromisoformat(task["due_utc"]) + timedelta(minutes=1)).isoformat()
            assert restarted.app.state.tasks.fire_due(fired_at) == 1
            assert restarted.app.state.tasks.fire_due(fired_at) == 0
            assert len(restarted.get("/notifications").json()) == 1
            assert restarted.get("/tasks").json()[0]["reminder_status"] == "fired"

        backup = root / "backup.zip"
        created = create_backup(data_dir, backup)
        restored_dir = root / "restored"
        restored = restore_backup(backup, restored_dir)
        with TestClient(create_app(Settings(JARVIS_DATA_DIR=restored_dir,
                                            JARVIS_PROVIDER="ollama"))) as restored_app:
            auth(restored_app)
            assert restored_app.get(f"/chats/{chat['id']}").json()["messages"][-1]["content"] == messages[-1]["content"]
            assert restored_app.get("/memories?q=Maria").json()[0]["id"] == memory["id"]
            assert restored_app.get("/tasks").json()[0]["id"] == task["id"]
            assert len(restored_app.get("/notifications").json()) == 1
            assert {item["purpose"] for item in restored_app.get("/usage").json()} == {"chat", "draft"}
            assert restored_app.get("/tasks").json()[0]["reminder_status"] == "fired"
            restored_exports = restored_app.get(f"/drafts/{draft['id']}").json()["exports"]
            assert restored_exports[0]["path"] == str(restored_dir / "outputs" / f"{draft['id']}.eml")
            assert Path(restored_exports[0]["path"]).is_file()
        print(json.dumps({"chat_id": chat["id"], "draft_id": draft["id"], "memory_id": memory["id"],
                          "task_id": task["id"], "browser_status": browser["status"],
                          "backup_schema": created["schema"], "restored_files": restored["file_count"]},
                         ensure_ascii=False))


if __name__ == "__main__":
    main()

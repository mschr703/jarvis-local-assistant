"""Chosen contact facts enter only local drafts and remain auditable."""

from fastapi.testclient import TestClient

from jarvis.app import create_app
from jarvis.backup import create_backup, restore_backup
from jarvis.config import Settings
from jarvis.contacts import ContactStore
from jarvis.db import Database
from jarvis.drafts import DraftStore
from jarvis.provider import Completion


def client_headers(client):
    return {"x-jarvis-csrf": client.get("/session").json()["csrf"]}


def test_local_draft_uses_only_selected_recipient_and_freezes_snapshot(tmp_path, monkeypatch):
    prompts = []

    async def fake_generate(_db, _config, purpose, messages):
        assert purpose == "draft"
        prompts.append(messages[-1]["content"])
        return Completion("Hallo Alex, Museumsbesuch klingt gut.", 10, 8, 0.0,
                          "local_no_api_charge")

    monkeypatch.setattr("jarvis.app.generate_text", fake_generate)
    root = tmp_path / "data"
    settings = Settings(JARVIS_DATA_DIR=root, JARVIS_OLLAMA_MODEL="fixture")
    with TestClient(create_app(settings)) as client:
        headers = client_headers(client)
        client.put("/style/named/Maxim", json={
            "description": "Kurze direkte Sätze in meinem Stil.",
            "policy": "local_only", "expected_revision": 0,
        }, headers=headers).raise_for_status()
        first = client.post("/contacts", json={"name": "Alex", "context": "München",
                                               "relationship": "Freund"}, headers=headers).json()
        second = client.post("/contacts", json={"name": "Alex", "context": "Arbeit",
                                                "relationship": "Kollege"}, headers=headers).json()
        chosen = client.post(f"/contacts/{first['id']}/notes",
                             json={"content": "Mag Museen"}, headers=headers).json()
        client.post(f"/contacts/{first['id']}/notes",
                    json={"content": "Privates Geheimnis"}, headers=headers).raise_for_status()
        client.post(f"/contacts/{second['id']}/notes",
                    json={"content": "Arbeitet im Labor"}, headers=headers).raise_for_status()
        first = client.get(f"/contacts/{first['id']}").json()
        response = client.post("/drafts/generate", json={
            "kind": "email", "brief": "Lade Alex ins Museum ein",
            "named_style_profile": "Maxim",
            "recipient_contact_id": first["id"],
            "recipient_expected_version": first["version"],
            "recipient_note_ids": [chosen["id"]],
        }, headers=headers)
        assert response.status_code == 200, response.text
        draft = response.json()
        assert "Mag Museen" in prompts[0]
        assert "Kurze direkte Sätze" in prompts[0]
        assert "München" in prompts[0] and "Freund" in prompts[0]
        assert "Privates Geheimnis" not in prompts[0]
        assert "Arbeitet im Labor" not in prompts[0]
        assert first["id"] not in prompts[0]
        snapshot = draft["recipient_snapshot"]
        assert draft["named_style_profile"]["revision"] == 1
        assert snapshot["id"] == first["id"] and snapshot["version"] == first["version"]
        assert [note["id"] for note in snapshot["notes"]] == [chosen["id"]]
        client.put(f"/contacts/{first['id']}/notes/{chosen['id']}",
                   json={"content": "Mag jetzt Konzerte", "expected_version": 1},
                   headers=headers).raise_for_status()
        assert client.get(f"/drafts/{draft['id']}").json()["recipient_snapshot"] == snapshot

    archive = tmp_path / "backup.zip"
    assert create_backup(root, archive)["schema"] == 59
    restored_root = tmp_path / "restored"
    assert restore_backup(archive, restored_root)["schema"] == 59
    restored_db = Database(restored_root / "jarvis.db")
    assert DraftStore(restored_db, restored_root).get_draft(draft["id"])[
        "recipient_snapshot"] == snapshot
    assert next(item for item in ContactStore(restored_db).get(first["id"])["notes"]
                if item["id"] == chosen["id"])["content"] == "Mag jetzt Konzerte"


def test_recipient_stale_foreign_notes_and_cloud_are_rejected_before_model(tmp_path, monkeypatch):
    calls = []

    async def fake_generate(*args):
        calls.append(args)
        return Completion("Text", 1, 1, 0.0, "local_no_api_charge")

    monkeypatch.setattr("jarvis.app.generate_text", fake_generate)
    local = Settings(JARVIS_DATA_DIR=tmp_path / "data", JARVIS_OLLAMA_MODEL="fixture")
    with TestClient(create_app(local)) as client:
        headers = client_headers(client)
        person = client.post("/contacts", json={"name": "Mira", "context": "Verein"},
                             headers=headers).json()
        other = client.post("/contacts", json={"name": "Mira", "context": "Arbeit"},
                            headers=headers).json()
        note = client.post(f"/contacts/{person['id']}/notes",
                           json={"content": "Geht gern wandern"}, headers=headers).json()
        foreign = client.post(f"/contacts/{other['id']}/notes",
                              json={"content": "Vertrauliche Arbeit"}, headers=headers).json()
        payload = {"kind": "text", "brief": "Kurze Einladung",
                   "recipient_contact_id": person["id"], "recipient_expected_version": 1,
                   "recipient_note_ids": [note["id"]]}
        assert client.post("/drafts/generate", json=payload,
                           headers=headers).status_code == 409
        payload["recipient_expected_version"] = 2
        payload["recipient_note_ids"] = [foreign["id"]]
        assert client.post("/drafts/generate", json=payload,
                           headers=headers).status_code == 422
        payload["recipient_note_ids"] = [note["id"], note["id"]]
        assert client.post("/drafts/generate", json=payload,
                           headers=headers).status_code == 422
        payload["recipient_note_ids"] = [note["id"]]
        payload["kind"] = "answer"
        assert client.post("/drafts/generate", json=payload,
                           headers=headers).status_code == 422
        long_note = client.post(f"/contacts/{person['id']}/notes",
                                json={"content": "x" * 3001}, headers=headers).json()
        payload["kind"] = "email"
        payload["recipient_expected_version"] = 3
        payload["recipient_note_ids"] = [long_note["id"]]
        assert client.post("/drafts/generate", json=payload,
                           headers=headers).status_code == 422
        payload["recipient_contact_id"] = ""
        assert client.post("/drafts/generate", json=payload,
                           headers=headers).status_code == 422
        payload["recipient_contact_id"] = person["id"]
        assert calls == []

    cloud = Settings(JARVIS_DATA_DIR=tmp_path / "data",
                     JARVIS_PROVIDER="openrouter", JARVIS_OPENROUTER_API_KEY="fixture",
                     JARVIS_OPENROUTER_MODEL="fixture")
    with TestClient(create_app(cloud)) as client:
        headers = client_headers(client)
        payload["allow_cloud"] = True
        assert client.post("/drafts/generate", json=payload,
                           headers=headers).status_code == 422
        assert calls == []


def test_schema_57_existing_draft_migrates_to_recipient_snapshots(tmp_path):
    path = tmp_path / "jarvis.db"
    db = Database(path)
    drafts = DraftStore(db, tmp_path)
    older = drafts.create_draft("email", "", "Alt", "Hallo", [], "", "")
    with db.connect() as connection:
        connection.execute("DROP TABLE draft_recipients")
        connection.execute("PRAGMA user_version=57")
    migrated = DraftStore(Database(path), tmp_path)
    assert migrated.get_draft(older["id"])["content"] == "Hallo"
    assert migrated.get_draft(older["id"])["recipient_snapshot"] is None

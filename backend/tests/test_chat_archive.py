"""Lokale Gesprächssuche, Archivstatus und gezieltes Löschen."""

import asyncio
import sqlite3
from collections.abc import AsyncIterator
from threading import Event, Thread

from fastapi.testclient import TestClient

from jarvis.app import create_app
from jarvis.backup import create_backup, restore_backup
from jarvis.config import Settings
from jarvis.db import Database
from jarvis.provider import Chunk, ProviderAdapter


def token(client: TestClient) -> dict:
    return {"x-jarvis-csrf": client.get("/session").json()["csrf"]}


def test_search_archive_restore_delete_and_backup(tmp_path):
    root = tmp_path / "data"
    settings = Settings(JARVIS_DATA_DIR=root, JARVIS_OLLAMA_MODEL="fixture")
    with TestClient(create_app(settings)) as client:
        headers = token(client)
        first = client.post("/chats", headers=headers).json()["id"]
        second = client.post("/chats", headers=headers).json()["id"]
        db = Database(root / "jarvis.db")
        db.add_message(first, "user", "Projekt Alpha besprechen")
        matched_message = db.add_message(first, "user", "Die Überraschung steht bei 80%.")
        usage_one = db.reserve_usage("chat", "ollama", "fixture", 0, 10, "2026-10-28")
        db.finish_usage(usage_one, 2, 3, 0, "local_no_api_charge", "complete")
        answer_one = db.add_message(first, "assistant", "Alpha ist geplant.", usage_id=usage_one)
        db.add_message(second, "user", "Projekt Beta besprechen")
        usage_two = db.reserve_usage("chat", "ollama", "fixture", 0, 10, "2026-10-28")
        db.finish_usage(usage_two, 4, 5, 0, "local_no_api_charge", "complete")
        answer_two = db.add_message(second, "assistant", "Beta bleibt separat.",
                                    usage_id=usage_two)
        evaluation = {"verdict": "achieved", "source_quality": "not_applicable",
                      "reason": "Projektzuordnung stimmt", "regression_case": False,
                      "expected_result": "", "version": 0}
        for chat_id, answer in ((first, answer_one), (second, answer_two)):
            result = client.put(f"/chats/{chat_id}/messages/{answer['id']}/evaluation",
                                headers=headers, json=evaluation)
            assert result.status_code == 200

        assert [item["id"] for item in client.get("/chats").json()] == [second, first]
        title = client.get("/chats?q=beta").json()
        assert len(title) == 1 and title[0]["id"] == second
        content = client.get("/chats?q=%C3%BCBERRASCHUNG").json()
        assert len(content) == 1 and content[0]["id"] == first
        assert "Überraschung" in content[0]["match_preview"]
        assert content[0]["match_message_id"] == matched_message["id"]
        assert [item["id"] for item in client.get("/chats?q=80%25").json()] == [first]
        assert client.get("/chats?q=" + "a" * 201).status_code == 422
        assert client.patch(f"/chats/{first}/archive", json={"archived": True}).status_code == 403
        archived = client.patch(f"/chats/{first}/archive", headers=headers,
                                json={"archived": True})
        assert archived.status_code == 200 and archived.json()["archived_at"]
        assert [item["id"] for item in client.get("/chats").json()] == [second]
        assert [item["id"] for item in client.get("/chats?archived=true").json()] == [first]
        assert client.get(f"/chats/{first}").json()["messages"][1]["content"] == (
            "Die Überraschung steht bei 80%.")
        assert client.post(f"/chats/{first}/stream", headers=headers,
                           json={"content": "Fortsetzen"}).status_code == 409
        assert client.patch(f"/chats/{first}/archive", headers=headers,
                            json={"archived": "invalid"}).status_code == 422

    archive = tmp_path / "chats.zip"
    assert create_backup(root, archive)["schema"] == 57
    restored = tmp_path / "restored"
    assert restore_backup(archive, restored)["schema"] == 57
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=restored,
                                        JARVIS_OLLAMA_MODEL="fixture"))) as client:
        headers = token(client)
        assert [item["id"] for item in client.get("/chats?archived=true&q=%C3%BCberraschung").json()] == [first]
        returned = client.patch(f"/chats/{first}/archive", headers=headers,
                                json={"archived": False})
        assert returned.status_code == 200 and returned.json()["archived_at"] is None
        assert client.delete(f"/chats/{first}").status_code == 403
        assert client.delete(f"/chats/{first}", headers=headers).status_code == 200
        assert client.delete(f"/chats/{first}", headers=headers).status_code == 404
        assert client.patch("/chats/missing/archive", headers=headers,
                            json={"archived": True}).status_code == 404
        assert client.get(f"/chats/{first}").status_code == 404
        assert [item["id"] for item in client.get("/chats").json()] == [second]
        assert client.get(f"/chats/{second}").json()["messages"][-1]["content"] == (
            "Beta bleibt separat.")
        assert client.get("/chat-evaluation-cases").json() == []
        assert client.get(f"/chats/{second}").json()["messages"][-1]["evaluation"][
            "reason"] == "Projektzuordnung stimmt"
        with sqlite3.connect(restored / "jarvis.db") as db:
            assert db.execute("SELECT COUNT(*) FROM messages WHERE chat_id=?", (first,)).fetchone()[0] == 0
            assert db.execute("SELECT COUNT(*) FROM model_usage").fetchone()[0] == 2
            assert db.execute("SELECT COUNT(*) FROM chat_answer_evaluations").fetchone()[0] == 1


def test_manual_chat_title_is_separate_persistent_and_searchable(tmp_path):
    root = tmp_path / "data"
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=root,
                                        JARVIS_OLLAMA_MODEL="fixture"))) as client:
        headers = token(client)
        first = client.post("/chats", headers=headers).json()["id"]
        second = client.post("/chats", headers=headers).json()["id"]
        assert client.patch(f"/chats/{first}/title", json={"title": "Studium"}).status_code == 403
        renamed = client.patch(f"/chats/{first}/title", headers=headers,
                               json={"title": "  Studium WiSe  "})
        assert renamed.status_code == 200
        assert renamed.json()["title"] == "Studium WiSe"
        db = Database(root / "jarvis.db")
        db.add_message(first, "user", "Erste Nachricht")
        assert db.get_chat(first)["title"] == "Studium WiSe"
        assert [item["id"] for item in client.get("/chats?q=wise").json()] == [first]
        assert client.get("/chats?q=wise").json()[0]["match_message_id"] is None
        assert db.get_chat(second)["title"] == "Neues Gespräch"
        assert client.patch(f"/chats/{first}/archive", headers=headers,
                            json={"archived": True}).status_code == 200
        assert client.patch(f"/chats/{first}/title", headers=headers,
                            json={"title": "Archiv Studium"}).status_code == 200
        assert client.get("/chats?archived=true&q=archiv").json()[0]["id"] == first
        for invalid in (" ", "Zeile\nZwei", "a" * 81):
            assert client.patch(f"/chats/{first}/title", headers=headers,
                                json={"title": invalid}).status_code == 422
        assert client.patch("/chats/missing/title", headers=headers,
                            json={"title": "Fehlt"}).status_code == 404

    backup = tmp_path / "renamed.jarvis"
    create_backup(root, backup)
    restored = tmp_path / "restored"
    restore_backup(backup, restored)
    assert Database(restored / "jarvis.db").get_chat(first)["title"] == "Archiv Studium"


def test_search_preview_handles_casefold_expansion(tmp_path):
    db = Database(tmp_path / "jarvis.db")
    chat = db.create_chat()
    message = db.add_message(chat["id"], "user", "ß" * 60 + " Treffer am Ende")
    found = db.list_chats("Treffer")
    assert found[0]["match_message_id"] == message["id"]
    assert "Treffer am Ende" in found[0]["match_preview"]


def test_schema37_migration_keeps_chat_messages(tmp_path):
    path = tmp_path / "jarvis.db"
    db = Database(path)
    chat = db.create_chat()
    db.add_message(chat["id"], "user", "Alte Unterhaltung")
    with db.connect() as connection:
        connection.execute("DROP INDEX chats_archive_updated")
        connection.execute("ALTER TABLE chats DROP COLUMN archived_at")
        connection.execute("DROP TABLE chat_compactions")
        connection.execute("DROP TABLE draft_named_style_usage")
        connection.execute("DROP TABLE named_style_profile_revisions")
        connection.execute("DROP TABLE named_style_profiles")
        connection.execute("DROP TABLE project_memory_links")
        connection.execute("DROP TABLE project_person_merges")
        connection.execute("DROP TABLE project_person_aliases")
        connection.execute("DROP TABLE project_person_tasks")
        connection.execute("DROP TABLE project_people")
        connection.execute("ALTER TABLE messages DROP COLUMN context_latest_omitted_id")
        connection.execute("ALTER TABLE messages DROP COLUMN context_omitted_count")
        connection.execute("ALTER TABLE messages DROP COLUMN context_token_budget")
        connection.execute("ALTER TABLE messages DROP COLUMN context_estimated_tokens")
        connection.execute("ALTER TABLE messages DROP COLUMN compaction_version")
        connection.execute("DROP TABLE learning_sessions")
        connection.execute("DROP TABLE memory_proposal_events")
        connection.execute("DROP TABLE memory_proposals")
        connection.execute("DROP TABLE memory_context_uses")
        connection.execute("PRAGMA user_version=37")
    migrated = Database(path)
    assert migrated.get_chat(chat["id"])["archived_at"] is None
    assert migrated.list_messages(chat["id"])[0]["content"] == "Alte Unterhaltung"
    assert migrated.list_chats("alte")[0]["id"] == chat["id"]
    with migrated.connect() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 57


def test_active_stream_blocks_archive_and_delete(tmp_path, monkeypatch):
    started, release = Event(), Event()

    class BlockingProvider(ProviderAdapter):
        async def stream(self, _messages: list[dict[str, str]]) -> AsyncIterator[Chunk]:
            started.set()
            await asyncio.to_thread(release.wait)
            yield Chunk("text", text="Die Antwort bleibt erhalten.")
            yield Chunk("usage", input_tokens=3, output_tokens=5, cost_usd=0,
                        cost_kind="local_no_api_charge")

    monkeypatch.setattr("jarvis.app.make_provider", lambda _settings: BlockingProvider())
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=tmp_path,
                                        JARVIS_OLLAMA_MODEL="fixture"))) as client:
        headers = token(client)
        chat_id = client.post("/chats", headers=headers).json()["id"]
        result = {}

        def stream() -> None:
            result["response"] = client.post(f"/chats/{chat_id}/stream", headers=headers,
                                             json={"content": "Hallo"})

        worker = Thread(target=stream, daemon=True)
        worker.start()
        try:
            assert started.wait(15)
            assert client.patch(f"/chats/{chat_id}/archive", headers=headers,
                                json={"archived": True}).status_code == 409
            assert client.delete(f"/chats/{chat_id}", headers=headers).status_code == 409
        finally:
            release.set()
            worker.join(timeout=15)
        assert not worker.is_alive()
        assert "event: done" in result["response"].text
        assert client.patch(f"/chats/{chat_id}/archive", headers=headers,
                            json={"archived": True}).status_code == 200

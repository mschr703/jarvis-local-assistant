"""Bewusst gewählte Skills sind versioniert und bleiben an Antworten belegbar."""

import sqlite3
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from jarvis.app import create_app
from jarvis.backup import create_backup, restore_backup
from jarvis.config import Settings
from jarvis.db import Database
from jarvis.provider import Chunk, ProviderAdapter
from jarvis.skills import SkillConflict, SkillStore


def test_skill_versions_conflict_disable_rollback_and_backup(tmp_path):
    data = tmp_path / "data"
    store = SkillStore(Database(data / "jarvis.db"))
    first = store.create("Projektantwort", "Nenne den nächsten Schritt.")
    skill_id = first["id"]
    assert first["active_version"] == 1
    assert len(first["versions"][0]["content_hash"]) == 64
    with pytest.raises(SkillConflict, match="bereits vergeben"):
        store.create("projektantwort", "Andere Regel")
    with ThreadPoolExecutor(max_workers=2) as pool:
        changes = [pool.submit(store.revise, skill_id, f"Fassung {i}", 1) for i in range(2)]
        assert sum(not isinstance(result, BaseException) for result in
                   [future.exception() for future in changes]) == 1
    assert store.list()[0]["revision"] == 2
    assert store.activate(skill_id, None, 2)["active_version"] is None
    with pytest.raises(SkillConflict, match="inaktiv"):
        store.selected(skill_id)
    assert store.activate(skill_id, 1, 3)["active_version"] == 1
    assert store.selected(skill_id)["content"] == "Nenne den nächsten Schritt."
    with pytest.raises(SkillConflict):
        store.activate(skill_id, 2, 3)
    with sqlite3.connect(data / "jarvis.db") as connection:
        connection.execute("UPDATE skill_versions SET content='Manipuliert' WHERE skill_id=? AND version=1",
                           (skill_id,))
    with pytest.raises(SkillConflict, match="Versionsbeleg"):
        store.selected(skill_id)
    with sqlite3.connect(data / "jarvis.db") as connection:
        connection.execute("UPDATE skill_versions SET content='Nenne den nächsten Schritt.' WHERE skill_id=? AND version=1",
                           (skill_id,))
    archive = tmp_path / "skills.zip"
    assert create_backup(data, archive)["schema"] == 58
    restored = tmp_path / "restored"
    restore_backup(archive, restored)
    restored_store = SkillStore(Database(restored / "jarvis.db"))
    assert restored_store.selected(skill_id)["content_hash"] == first["versions"][0]["content_hash"]
    assert len(restored_store.list()[0]["activations"]) == 4


def test_skill_api_selection_and_stream_snapshot(tmp_path, monkeypatch):
    data = tmp_path / "data"
    seen = []
    skill_id_holder = []

    class ChangingProvider(ProviderAdapter):
        async def stream(self, messages: list[dict[str, str]]) -> AsyncIterator[Chunk]:
            seen.append(messages[0]["content"])
            SkillStore(Database(data / "jarvis.db")).revise(
                skill_id_holder[0], "Spätere Fassung", 1)
            yield Chunk("text", text="Antwort mit Skill")
            yield Chunk("usage", input_tokens=10, output_tokens=4, cost_usd=0,
                        cost_kind="local_no_api_charge")

    monkeypatch.setattr("jarvis.app.make_provider", lambda _settings: ChangingProvider())
    settings = Settings(JARVIS_DATA_DIR=data, JARVIS_OLLAMA_MODEL="fixture")
    with TestClient(create_app(settings)) as client:
        assert client.post("/skills", json={"name": "Projekt", "content": "Kurz"}).status_code == 401
        csrf = client.get("/session").json()["csrf"]
        headers = {"x-jarvis-csrf": csrf}
        assert client.post("/skills", json={"name": "Projekt", "content": "Kurz"}).status_code == 403
        assert client.post("/skills", headers=headers,
                           json={"name": "Projekt", "content": "Kurz", "extra": 1}).status_code == 422
        created = client.post("/skills", headers=headers,
                              json={"name": "Projekt", "content": "Nenne nächste Schritte."})
        assert created.status_code == 200
        skill_id = created.json()["id"]
        skill_id_holder.append(skill_id)
        hash_v1 = created.json()["versions"][0]["content_hash"]
        chat_id = client.post("/chats", headers=headers).json()["id"]
        assert client.post(f"/chats/{chat_id}/stream", headers=headers,
                           json={"content": "Hallo", "skill_id": "fehlt"}).status_code == 404
        assert client.get(f"/chats/{chat_id}").json()["messages"] == []
        response = client.post(f"/chats/{chat_id}/stream", headers=headers,
                               json={"content": "Hallo", "skill_id": skill_id})
        assert response.status_code == 200 and "event: done" in response.text
        assert "Nenne nächste Schritte." in seen[0]
        answer = client.get(f"/chats/{chat_id}").json()["messages"][-1]
        assert (answer["skill_id"], answer["skill_version"], answer["skill_hash"]) == (skill_id, 1, hash_v1)
        assert answer["skill_name"] == "Projekt"
        evaluation = client.put(f"/chats/{chat_id}/messages/{answer['id']}/evaluation",
                                headers=headers, json={"verdict": "failed",
                                "source_quality": "not_applicable", "reason": "Testfall",
                                "regression_case": True, "expected_result": "Bessere Antwort",
                                "version": 0})
        assert evaluation.status_code == 200
        case = client.get("/chat-evaluation-cases").json()[0]
        assert (case["skill_name"], case["skill_version"], case["skill_hash"]) == (
            "Projekt", 1, hash_v1)
        assert client.get("/skills").json()[0]["active_version"] == 2
        disabled = client.put(f"/skills/{skill_id}/active", headers=headers,
                              json={"version": None, "expected_revision": 2})
        assert disabled.status_code == 200 and disabled.json()["active_version"] is None
        assert client.post(f"/chats/{chat_id}/stream", headers=headers,
                           json={"content": "Nochmal", "skill_id": skill_id}).status_code == 409
        rollback = client.put(f"/skills/{skill_id}/active", headers=headers,
                              json={"version": 1, "expected_revision": 3})
        assert rollback.status_code == 200 and rollback.json()["active_version"] == 1
        assert client.put(f"/skills/{skill_id}/active", headers=headers,
                          json={"version": 9, "expected_revision": 4}).status_code == 404


def test_skill_counts_toward_context_limit(tmp_path, monkeypatch):
    class EmptyProvider(ProviderAdapter):
        async def stream(self, _messages):
            yield Chunk("text", text="Nie erreicht")

    monkeypatch.setattr("jarvis.app.make_provider", lambda _settings: EmptyProvider())
    settings = Settings(JARVIS_DATA_DIR=tmp_path / "data", JARVIS_OLLAMA_MODEL="fixture",
                        JARVIS_MAX_CONTEXT_CHARS=4000)
    with TestClient(create_app(settings)) as client:
        csrf = client.get("/session").json()["csrf"]
        headers = {"x-jarvis-csrf": csrf}
        skill_id = client.post("/skills", headers=headers,
                               json={"name": "Lang", "content": "S" * 2000}).json()["id"]
        chat_id = client.post("/chats", headers=headers).json()["id"]
        response = client.post(f"/chats/{chat_id}/stream", headers=headers,
                               json={"content": "N" * 1900, "skill_id": skill_id})
        assert response.status_code == 422
        assert client.get(f"/chats/{chat_id}").json()["messages"] == []

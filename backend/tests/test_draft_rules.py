"""Entwurfsregeln sind versioniert und jeder erzeugte Text behält seinen Herkunftsbeleg."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from jarvis.app import create_app
from jarvis.backup import create_backup, restore_backup
from jarvis.chat_rules import ChatRuleConflict, DraftRuleStore, draft_system_prompt
from jarvis.config import Settings
from jarvis.db import Database
from jarvis.drafts import DraftStore
from jarvis.provider import Completion


def test_draft_rule_versions_conflict_rollback_and_backup(tmp_path):
    data = tmp_path / "data"
    store = DraftRuleStore(Database(data / "jarvis.db"))
    assert store.active()["version"] == 1 and store.active()["content"] == ""
    assert draft_system_prompt("Grundregel", "") == "Grundregel"
    assert "Quellen-" in draft_system_prompt("Grundregel", "Stil")
    with ThreadPoolExecutor(max_workers=2) as pool:
        changes = [pool.submit(store.create, f"Formuliere {i}.", 1) for i in range(2)]
        successes = 0
        for change in changes:
            try:
                change.result()
                successes += 1
            except ChatRuleConflict:
                pass
    assert successes == 1
    with pytest.raises(ChatRuleConflict):
        store.activate(1, 1)
    assert store.activate(1, 2)["active_version"] == 1
    assert store.history()["activations"][0]["from_version"] == 2
    archive = tmp_path / "backup.zip"
    assert create_backup(data, archive)["schema"] == 61
    restored = tmp_path / "restored"
    restore_backup(archive, restored)
    assert DraftRuleStore(Database(restored / "jarvis.db")).history()["revision"] == 3


def test_draft_api_uses_start_version_through_change_edit_export_and_restart(tmp_path, monkeypatch):
    data = tmp_path / "data"
    seen: list[list[dict[str, str]]] = []

    async def fake_generate(_db, _config, _purpose, messages):
        seen.append(messages)
        if len(seen) == 1:
            DraftRuleStore(Database(data / "jarvis.db")).create("Zweite Vorgabe", 2)
        return Completion("Erzeugter Entwurf", 8, 5, 0.0, "local_no_api_charge")

    monkeypatch.setattr("jarvis.app.generate_text", fake_generate)
    settings = Settings(JARVIS_DATA_DIR=data, JARVIS_OLLAMA_MODEL="fixture")
    with TestClient(create_app(settings)) as client:
        assert client.post("/draft-rules/versions", json={"content": "Kurz", "expected_revision": 1}).status_code == 401
        csrf = client.get("/session").json()["csrf"]
        headers = {"x-jarvis-csrf": csrf}
        assert client.post("/draft-rules/versions", json={"content": "Kurz", "expected_revision": 1}).status_code == 403
        assert client.post("/draft-rules/versions", headers=headers,
                           json={"content": "Kurz", "expected_revision": 1, "extra": 1}).status_code == 422
        created = client.post("/draft-rules/versions", headers=headers,
                              json={"content": "Erste Vorgabe", "expected_revision": 1})
        assert created.status_code == 200 and created.json()["active_version"] == 2
        assert client.post("/draft-rules/versions", headers=headers,
                           json={"content": "Veraltet", "expected_revision": 1}).status_code == 409
        first = client.post("/drafts/generate", headers=headers,
                            json={"kind": "text", "brief": "Kurzer Bericht"})
        assert first.status_code == 200, first.text
        draft = first.json()
        assert draft["rule_version"] == 2
        assert "Erste Vorgabe" in seen[0][0]["content"]
        assert "Zweite Vorgabe" not in seen[0][0]["content"]
        assert client.get("/draft-rules").json()["active_version"] == 3
        edited = client.patch(f"/drafts/{draft['id']}", headers=headers,
                              json={"content": "Bearbeitet", "expected_version": 1})
        assert edited.status_code == 200 and edited.json()["rule_version"] == 2
        exported = client.post(f"/drafts/{draft['id']}/export", headers=headers,
                               json={"format": "txt", "expected_version": 2})
        assert exported.status_code == 200
        assert client.get(f"/drafts/{draft['id']}").json()["rule_version"] == 2
        rollback = client.put("/draft-rules/active", headers=headers,
                              json={"version": 1, "expected_revision": 3})
        assert rollback.status_code == 200 and rollback.json()["active_version"] == 1
        assert client.put("/draft-rules/active", headers=headers,
                          json={"version": 99, "expected_revision": 4}).status_code == 404
        second = client.post("/drafts/generate", headers=headers,
                             json={"kind": "text", "brief": "Zweiter Bericht"})
        assert second.status_code == 200 and second.json()["rule_version"] == 1
        assert "Erste Vorgabe" not in seen[1][0]["content"]
    with TestClient(create_app(settings)) as restarted:
        restarted.get("/session")
        assert restarted.get("/draft-rules").json()["active_version"] == 1
        assert restarted.get(f"/drafts/{draft['id']}").json()["rule_version"] == 2


def test_legacy_draft_does_not_get_invented_rule_version(tmp_path):
    data = tmp_path / "data"
    db = Database(data / "jarvis.db")
    draft = DraftStore(db, data).create_draft("text", "", "Alt", "Text", [], "", "")
    assert draft["rule_version"] is None

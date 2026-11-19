"""Chat-Regelfassungen bleiben unveränderlich und Antworten tragen die aktive Version."""

from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from typing import ClassVar

import pytest
from fastapi.testclient import TestClient

from jarvis.app import create_app
from jarvis.backup import create_backup, restore_backup
from jarvis.chat_rules import ChatRuleConflict, ChatRuleStore, system_prompt
from jarvis.config import Settings
from jarvis.db import Database
from jarvis.provider import Chunk, ProviderAdapter, ProviderError


class RuleProvider(ProviderAdapter):
    seen: ClassVar[list[list[dict[str, str]]]] = []

    async def stream(self, messages: list[dict[str, str]]) -> AsyncIterator[Chunk]:
        self.seen.append(messages)
        yield Chunk("text", text="Regelantwort")
        yield Chunk("usage", input_tokens=9, output_tokens=2, cost_usd=0,
                    cost_kind="local_no_api_charge")


class FailingRuleProvider(ProviderAdapter):
    async def stream(self, messages: list[dict[str, str]]) -> AsyncIterator[Chunk]:
        yield Chunk("text", text="Teil")
        raise ProviderError("fixture", "Modellanfrage unterbrochen")


def test_rule_versions_activation_conflict_and_restore(tmp_path):
    data = tmp_path / "data"
    db = Database(data / "jarvis.db")
    store = ChatRuleStore(db)
    assert store.active()["version"] == 1
    assert store.active()["content"] == ""
    assert store.history()["revision"] == 1
    with pytest.raises(ValueError, match="1–2.000"):
        store.create(" ", 1)
    with ThreadPoolExecutor(max_workers=2) as pool:
        changes = [pool.submit(store.create, f"Nutze Stil {i}.", 1) for i in range(2)]
        success = 0
        for change in changes:
            try:
                change.result()
                success += 1
            except ChatRuleConflict:
                pass
    assert success == 1
    assert store.history()["revision"] == 2
    with pytest.raises(ChatRuleConflict):
        store.activate(1, 1)
    assert store.activate(1, 2)["active_version"] == 1
    assert store.history()["versions"][0]["version"] == 2
    assert store.history()["activations"][0]["to_version"] == 1
    assert system_prompt("Grundregel", "") == "Grundregel"
    assert "Stil" in system_prompt("Grundregel", "Stil")
    archive = tmp_path / "backup.zip"
    assert create_backup(data, archive)["schema"] == 56
    restored = tmp_path / "restored"
    restore_backup(archive, restored)
    assert ChatRuleStore(Database(restored / "jarvis.db")).history()["active_version"] == 1


def test_api_rule_change_rollback_and_chat_attribution(tmp_path, monkeypatch):
    RuleProvider.seen = []
    monkeypatch.setattr("jarvis.app.make_provider", lambda _settings: RuleProvider())
    settings = Settings(JARVIS_DATA_DIR=tmp_path / "data", JARVIS_OLLAMA_MODEL="fixture")
    with TestClient(create_app(settings)) as client:
        assert client.post("/chat-rules/versions", json={"content": "Kurz", "expected_revision": 1}).status_code == 401
        csrf = client.get("/session").json()["csrf"]
        headers = {"x-jarvis-csrf": csrf}
        assert client.post("/chat-rules/versions", json={"content": "Kurz", "expected_revision": 1}).status_code == 403
        assert client.post("/chat-rules/versions", headers=headers,
                           json={"content": "Kurz", "expected_revision": 1, "extra": 1}).status_code == 422
        created = client.post("/chat-rules/versions", headers=headers,
                              json={"content": "Antworte knapp.", "expected_revision": 1})
        assert created.status_code == 200 and created.json()["active_version"] == 2
        assert client.post("/chat-rules/versions", headers=headers,
                           json={"content": "Andere", "expected_revision": 1}).status_code == 409
        chat_id = client.post("/chats", headers=headers).json()["id"]
        first = client.post(f"/chats/{chat_id}/stream", headers=headers,
                            json={"content": "Hallo"})
        assert first.status_code == 200 and "event: done" in first.text
        assert "Antworte knapp." in RuleProvider.seen[-1][0]["content"]
        messages = client.get(f"/chats/{chat_id}").json()["messages"]
        assert messages[-1]["rule_version"] == 2
        rollback = client.put("/chat-rules/active", headers=headers,
                              json={"version": 1, "expected_revision": 2})
        assert rollback.status_code == 200 and rollback.json()["active_version"] == 1
        assert rollback.json()["activations"][0]["from_version"] == 2
        assert client.put("/chat-rules/active", headers=headers,
                          json={"version": 99, "expected_revision": 3}).status_code == 404
        second = client.post(f"/chats/{chat_id}/stream", headers=headers,
                             json={"content": "Noch mal"})
        assert second.status_code == 200 and "event: done" in second.text
        assert "Antworte knapp." not in RuleProvider.seen[-1][0]["content"]
        messages = client.get(f"/chats/{chat_id}").json()["messages"]
        assert [message["rule_version"] for message in messages if message["role"] == "assistant"] == [2, 1]
    with TestClient(create_app(settings)) as restarted:
        restarted.get("/session")
        assert restarted.get("/chat-rules").json()["active_version"] == 1
        assert restarted.get(f"/chats/{chat_id}").json()["messages"][1]["rule_version"] == 2


def test_failed_chat_keeps_rule_version(tmp_path, monkeypatch):
    monkeypatch.setattr("jarvis.app.make_provider", lambda _settings: FailingRuleProvider())
    data = tmp_path / "data"
    settings = Settings(JARVIS_DATA_DIR=data, JARVIS_OLLAMA_MODEL="fixture")
    with TestClient(create_app(settings)) as client:
        csrf = client.get("/session").json()["csrf"]
        headers = {"x-jarvis-csrf": csrf}
        client.post("/chat-rules/versions", headers=headers,
                    json={"content": "Prüfe Quelle.", "expected_revision": 1})
        chat_id = client.post("/chats", headers=headers).json()["id"]
        response = client.post(f"/chats/{chat_id}/stream", headers=headers,
                               json={"content": "Hallo"})
        assert "event: error" in response.text
        assistant = client.get(f"/chats/{chat_id}").json()["messages"][-1]
        assert assistant["status"] == "failed" and assistant["rule_version"] == 2


def test_rule_change_during_stream_does_not_relabel_answer(tmp_path, monkeypatch):
    data = tmp_path / "data"

    class ChangingRuleProvider(ProviderAdapter):
        async def stream(self, messages: list[dict[str, str]]) -> AsyncIterator[Chunk]:
            assert "Erste Regel" in messages[0]["content"]
            ChatRuleStore(Database(data / "jarvis.db")).create("Zweite Regel", 2)
            yield Chunk("text", text="Antwort unter erster Regel")
            yield Chunk("usage", input_tokens=10, output_tokens=5, cost_usd=0,
                        cost_kind="local_no_api_charge")

    monkeypatch.setattr("jarvis.app.make_provider", lambda _settings: ChangingRuleProvider())
    settings = Settings(JARVIS_DATA_DIR=data, JARVIS_OLLAMA_MODEL="fixture")
    with TestClient(create_app(settings)) as client:
        csrf = client.get("/session").json()["csrf"]
        headers = {"x-jarvis-csrf": csrf}
        client.post("/chat-rules/versions", headers=headers,
                    json={"content": "Erste Regel", "expected_revision": 1})
        chat_id = client.post("/chats", headers=headers).json()["id"]
        response = client.post(f"/chats/{chat_id}/stream", headers=headers,
                               json={"content": "Hallo"})
        assert "event: done" in response.text
        answer = client.get(f"/chats/{chat_id}").json()["messages"][-1]
        assert answer["rule_version"] == 2
        assert client.get("/chat-rules").json()["active_version"] == 3


def test_rule_content_counts_toward_context_limit(tmp_path, monkeypatch):
    monkeypatch.setattr("jarvis.app.make_provider", lambda _settings: RuleProvider())
    settings = Settings(JARVIS_DATA_DIR=tmp_path / "data", JARVIS_OLLAMA_MODEL="fixture",
                        JARVIS_MAX_CONTEXT_CHARS=4000)
    with TestClient(create_app(settings)) as client:
        csrf = client.get("/session").json()["csrf"]
        headers = {"x-jarvis-csrf": csrf}
        client.post("/chat-rules/versions", headers=headers,
                    json={"content": "R" * 2000, "expected_revision": 1})
        chat_id = client.post("/chats", headers=headers).json()["id"]
        response = client.post(f"/chats/{chat_id}/stream", headers=headers,
                               json={"content": "N" * 1900})
        assert response.status_code == 422
        assert client.get(f"/chats/{chat_id}").json()["messages"] == []

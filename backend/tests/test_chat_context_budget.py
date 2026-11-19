"""Der lokale Chat legt ein geschätztes Kontextbudget und Auslassungen offen."""

import sqlite3
from collections.abc import AsyncIterator

from fastapi.testclient import TestClient

from jarvis.app import create_app
from jarvis.backup import create_backup, restore_backup
from jarvis.chat_compaction import build_chat_context, estimate_message_tokens
from jarvis.config import Settings
from jarvis.db import Database
from jarvis.provider import Chunk, ProviderAdapter, ProviderError


def seeded(db: Database, count: int = 20) -> tuple[str, list[dict]]:
    chat_id = db.create_chat()["id"]
    for index in range(count):
        db.add_message(chat_id, "user" if index % 2 == 0 else "assistant",
                       f"Atlas {index}: " + "Käse 🍎 " * 35)
    return chat_id, db.list_messages(chat_id)


def test_token_estimate_and_omitted_boundary_with_snapshot_and_reset(tmp_path):
    db = Database(tmp_path / "jarvis.db")
    chat_id, history = seeded(db)
    assert estimate_message_tokens("🍎") > estimate_message_tokens("A")
    assert estimate_message_tokens("Hallo") > 0
    budget = 400
    raw = build_chat_context(history, None, "System", "Frage", 24_000, True, budget)
    assert raw.estimated_tokens <= budget
    assert raw.token_budget == budget
    assert raw.omitted_count == 20 - (len(raw.messages) - 2)
    assert raw.omitted_count > 0
    assert raw.latest_omitted_id == history[raw.omitted_count - 1]["id"]
    assert raw.messages[-2]["content"] == history[-1]["content"]
    assert [item["content"] for item in raw.messages[1:-1]] == [
        item["content"] for item in history[raw.omitted_count:]]
    covered = [item["id"] for item in history[:12]]
    snapshot = db.save_chat_compaction(chat_id, 0, history[-1]["id"], covered,
        [{"kind": "decision", "text": "Atlas läuft",
          "message_ids": [history[0]["id"]]}], "fixture")
    compact = build_chat_context(history, snapshot, "System", "Frage", 24_000,
                                 True, budget)
    assert compact.estimated_tokens <= budget
    assert compact.omitted_count < raw.omitted_count
    assert compact.latest_omitted_id == history[12 + compact.omitted_count - 1]["id"]
    assert "Atlas läuft" in compact.messages[1]["content"]
    reset = db.revise_chat_compaction(chat_id, 1, history[-1]["id"], None)
    assert reset["entries"] == []
    again = build_chat_context(history, None, "System", "Frage", 24_000,
                               True, budget)
    assert again.omitted_count == raw.omitted_count
    try:
        build_chat_context(history, snapshot, "System", "🍎" * 600,
                           24_000, True, budget)
    except ValueError as exc:
        assert "Tokenbudget" in str(exc)
    else:
        raise AssertionError("Zu langer Prompt wurde nicht abgewiesen")


def test_stream_persists_local_budget_and_omissions_but_not_cloud(tmp_path, monkeypatch):
    root = tmp_path / "data"
    db = Database(root / "jarvis.db")
    chat_id, history = seeded(db)
    seen = []

    class FakeProvider(ProviderAdapter):
        async def stream(self, messages: list[dict[str, str]]) -> AsyncIterator[Chunk]:
            seen.append(messages)
            yield Chunk("text", text="Atlas bleibt")
            yield Chunk("usage", input_tokens=123, output_tokens=3, cost_usd=0,
                        cost_kind="local_no_api_charge")

    monkeypatch.setattr("jarvis.app.make_provider", lambda _settings: FakeProvider())
    settings = Settings(JARVIS_DATA_DIR=root, JARVIS_OLLAMA_MODEL="fixture",
                        JARVIS_OLLAMA_CONTEXT_TOKENS=4096,
                        JARVIS_MAX_OUTPUT_TOKENS=1024)
    with TestClient(create_app(settings)) as client:
        csrf = {"x-jarvis-csrf": client.get("/session").json()["csrf"]}
        response = client.post(f"/chats/{chat_id}/stream", headers=csrf,
                               json={"content": "Was gilt?"})
        assert response.status_code == 200 and "event: done" in response.text
    answer = db.list_messages(chat_id)[-1]
    assert answer["context_token_budget"] == 2048
    assert answer["context_estimated_tokens"] <= 2048
    assert answer["context_omitted_count"] > 0
    assert answer["context_latest_omitted_id"] in {item["id"] for item in history}
    assert answer["input_tokens"] == 123  # echte Providerzahl bleibt getrennt
    assert len(seen[0]) - 2 == len(history) - answer["context_omitted_count"]

    cloud = Settings(JARVIS_DATA_DIR=root, JARVIS_PROVIDER="openrouter",
                     JARVIS_OPENROUTER_API_KEY="fixture",
                     JARVIS_OPENROUTER_MODEL="fixture")
    with TestClient(create_app(cloud)) as client:
        csrf = {"x-jarvis-csrf": client.get("/session").json()["csrf"]}
        response = client.post(f"/chats/{chat_id}/stream", headers=csrf,
                               json={"content": "Cloud?", "allow_cloud": True})
        assert response.status_code == 200 and "event: done" in response.text
    cloud_answer = db.list_messages(chat_id)[-1]
    assert cloud_answer["context_estimated_tokens"] is None
    assert cloud_answer["context_token_budget"] is None
    assert cloud_answer["context_omitted_count"] is None
    assert cloud_answer["context_latest_omitted_id"] is None
    assert not any("Belegter Gesprächszustand" in part["content"] for part in seen[-1])


def test_schema40_migration_and_budget_restore(tmp_path):
    root = tmp_path / "data"
    db = Database(root / "jarvis.db")
    chat_id = db.create_chat()["id"]
    db.add_message(chat_id, "assistant", "Antwort", provider="ollama",
                   context_estimated_tokens=250, context_token_budget=2048,
                   context_omitted_count=4, context_latest_omitted_id="old-id")
    archive = tmp_path / "profile.jarvis"
    assert create_backup(root, archive)["schema"] == 60
    restored_root = tmp_path / "restored"
    assert restore_backup(archive, restored_root)["schema"] == 60
    restored = Database(restored_root / "jarvis.db").list_messages(chat_id)[0]
    assert (restored["context_estimated_tokens"], restored["context_omitted_count"],
            restored["context_latest_omitted_id"]) == (250, 4, "old-id")
    with db.connect() as connection:
        connection.execute("DROP TABLE draft_named_style_usage")
        connection.execute("DROP TABLE named_style_profile_revisions")
        connection.execute("DROP TABLE named_style_profiles")
        connection.execute("DROP TABLE project_memory_links")
        connection.execute("DROP TABLE project_person_merges")
        connection.execute("DROP TABLE project_person_aliases")
        connection.execute("DROP TABLE project_person_tasks")
        connection.execute("DROP TABLE project_people")
        for column in ("context_latest_omitted_id", "context_omitted_count",
                       "context_token_budget", "context_estimated_tokens"):
            connection.execute(f"ALTER TABLE messages DROP COLUMN {column}")
        connection.execute("DROP TABLE learning_sessions")
        connection.execute("DROP TABLE memory_proposal_events")
        connection.execute("DROP TABLE memory_proposals")
        connection.execute("DROP TABLE memory_context_uses")
        connection.execute("PRAGMA user_version=40")
    migrated = Database(db.path)
    assert migrated.list_messages(chat_id)[0]["context_estimated_tokens"] is None
    with sqlite3.connect(db.path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 60


def test_failed_local_answer_keeps_context_receipt(tmp_path, monkeypatch):
    root = tmp_path / "data"
    db = Database(root / "jarvis.db")
    chat_id, _ = seeded(db)

    class FailingProvider(ProviderAdapter):
        async def stream(self, _messages: list[dict[str, str]]) -> AsyncIterator[Chunk]:
            yield Chunk("text", text="Teil")
            raise ProviderError("fixture", "Modell abgebrochen")

    monkeypatch.setattr("jarvis.app.make_provider", lambda _settings: FailingProvider())
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=root,
                                        JARVIS_OLLAMA_MODEL="fixture",
                                        JARVIS_OLLAMA_CONTEXT_TOKENS=4096))) as client:
        csrf = {"x-jarvis-csrf": client.get("/session").json()["csrf"]}
        response = client.post(f"/chats/{chat_id}/stream", headers=csrf,
                               json={"content": "Weiter?"})
        assert response.status_code == 200 and "event: error" in response.text
    answer = db.list_messages(chat_id)[-1]
    assert answer["status"] == "failed" and answer["content"] == "Teil"
    assert answer["context_estimated_tokens"] <= answer["context_token_budget"]
    assert answer["context_omitted_count"] > 0


def test_output_reservation_blocks_too_small_context_before_saving(tmp_path, monkeypatch):
    root = tmp_path / "data"
    db = Database(root / "jarvis.db")
    chat_id = db.create_chat()["id"]
    monkeypatch.setattr("jarvis.app.make_provider", lambda _settings: ProviderAdapter())
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=root,
                                        JARVIS_OLLAMA_MODEL="fixture",
                                        JARVIS_OLLAMA_CONTEXT_TOKENS=4096,
                                        JARVIS_MAX_OUTPUT_TOKENS=3000))) as client:
        csrf = {"x-jarvis-csrf": client.get("/session").json()["csrf"]}
        response = client.post(f"/chats/{chat_id}/stream", headers=csrf,
                               json={"content": "Hallo"})
        assert response.status_code == 422
        assert "Kontextfenster" in response.text
    assert db.list_messages(chat_id) == []

"""Versionierte, belegte Chat-Zustände bleiben lokal und verändern Rohbeiträge nicht."""

import asyncio
import json
import sqlite3
from collections.abc import AsyncIterator
from threading import Event, Thread

from fastapi.testclient import TestClient

from jarvis.app import create_app
from jarvis.backup import create_backup, restore_backup
from jarvis.chat_compaction import choose_batch, context_with_snapshot, parse_state
from jarvis.config import Settings
from jarvis.db import Database
from jarvis.provider import Chunk, Completion, ProviderAdapter


def csrf(client):
    return {"x-jarvis-csrf": client.get("/session").json()["csrf"]}


def seeded_chat(db: Database, count: int = 20):
    chat_id = db.create_chat()["id"]
    messages = [db.add_message(chat_id, "user" if index % 2 == 0 else "assistant",
                               f"Nachricht {index}: Entscheidung Atlas bleibt bestehen.")
                for index in range(count)]
    return chat_id, messages


def test_batch_parser_and_context_keep_sources_and_recent_raw(tmp_path):
    db = Database(tmp_path / "jarvis.db")
    chat_id, messages = seeded_chat(db)
    history = db.list_messages(chat_id)
    batch = choose_batch(history, [])
    assert [item["id"] for item in batch] == [item["id"] for item in history[:12]]
    valid = json.dumps({"entries": [{"kind": "decision", "text": "Atlas bleibt bestehen",
                                     "refs": [3]},
                                    {"kind": "goal", "text": "Falsche Quelle",
                                     "refs": [999]}]})
    entries = parse_state(valid, [item["id"] for item in batch])
    assert entries == [{"kind": "decision", "text": "Atlas bleibt bestehen",
                        "message_ids": [batch[3]["id"]]}]
    snapshot = {"covered_message_ids": [item["id"] for item in batch], "entries": entries}
    context = context_with_snapshot(history, snapshot, "System", "Was gilt?", 24_000, True)
    assert len(context) == 11  # zwei Systemteile, acht rohe Nachrichten, neue Frage
    assert all(item["content"] not in [part["content"] for part in context]
               for item in history[:12])
    assert [part["content"] for part in context[2:-1]] == [
        item["content"] for item in history[12:]]
    assert batch[3]["id"] in context[1]["content"]
    assert sum(len(item["content"]) for item in context) <= 24_000
    tight = context_with_snapshot(history, snapshot, "System", "Was gilt?", 600, True)
    assert sum(len(item["content"]) for item in tight) <= 600
    assert tight[-2]["content"] == history[-1]["content"]
    cloud = context_with_snapshot(history, snapshot, "System", "Was gilt?", 24_000, False)
    assert len(cloud) == len(history) + 2 and not any(
        "Gesprächszustand" in item["content"] for item in cloud)
    assert messages[-1]["id"] == history[-1]["id"]


def test_compaction_api_versions_stale_and_restore(tmp_path, monkeypatch):
    root = tmp_path / "data"
    db = Database(root / "jarvis.db")
    chat_id, messages = seeded_chat(db, 28)
    seen = []

    async def generate(_db, settings, purpose, prompt):
        seen.append((settings.provider, settings.max_output_tokens, purpose))
        material = json.loads(prompt[-1]["content"])
        assert material[1]["n"] == 1
        return Completion(json.dumps({"entries": [{"kind": "decision",
            "text": f"Atlas-Beschluss aus Fassung {len(seen)}",
            "refs": [1]}]}), 60, 60, 0, "local_no_api_charge")

    monkeypatch.setattr("jarvis.app.generate_text", generate)
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=root,
                                        JARVIS_OLLAMA_MODEL="fixture"))) as client:
        headers = csrf(client)
        url = f"/chats/{chat_id}/compactions"
        assert client.post(url, json={"expected_version": 0,
                                      "last_message_id": messages[-1]["id"]}).status_code == 403
        first = client.post(url, headers=headers, json={"expected_version": 0,
                            "last_message_id": messages[-1]["id"]})
        assert first.status_code == 200, first.text
        assert first.json()["version"] == 1
        assert len(first.json()["covered_message_ids"]) == 12
        assert seen == [("ollama", 192, "chat_compaction")]
        assert client.post(url, headers=headers, json={"expected_version": 0,
                           "last_message_id": messages[-1]["id"]}).status_code == 409
        second = client.post(url, headers=headers, json={"expected_version": 1,
                             "last_message_id": messages[-1]["id"]})
        assert second.status_code == 200, second.text
        assert second.json()["version"] == 2
        assert len(second.json()["covered_message_ids"]) == 20
        assert len(second.json()["entries"]) == 2
        assert [item["version"] for item in client.get(url).json()] == [2, 1]
        assert len(client.get(f"/chats/{chat_id}").json()["messages"]) == 28
        assert client.post(url, headers=headers, json={"expected_version": 2,
                           "last_message_id": messages[-1]["id"]}).status_code == 422
        db.add_message(chat_id, "user", "neuer Stand")
        assert client.post(url, headers=headers, json={"expected_version": 2,
                           "last_message_id": messages[-1]["id"]}).status_code == 409
        db.set_chat_archived(chat_id, True)
        assert client.post(url, headers=headers, json={"expected_version": 2,
                           "last_message_id": db.list_messages(chat_id)[-1]["id"]}).status_code == 409
    archive = tmp_path / "backup.jarvis"
    assert create_backup(root, archive)["schema"] == 55
    restored_root = tmp_path / "restored"
    assert restore_backup(archive, restored_root)["schema"] == 55
    restored = Database(restored_root / "jarvis.db")
    assert restored.latest_chat_compaction(chat_id)["version"] == 2
    assert restored.latest_chat_compaction(chat_id)["entries"] == second.json()["entries"]
    assert restored.latest_chat_compaction(chat_id)["created_by"] == "ollama"
    with sqlite3.connect(restored.path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 55
    assert restored.delete_chat(chat_id)
    assert restored.list_chat_compactions(chat_id) == []


def test_model_error_does_not_write_and_cloud_is_blocked(tmp_path, monkeypatch):
    root = tmp_path / "data"
    db = Database(root / "jarvis.db")
    chat_id, messages = seeded_chat(db, 16)

    async def invalid(*_args):
        output = json.dumps({"entries": [{"kind": "goal", "text": "Erfunden",
                                         "refs": [999]}]})
        return Completion(output, 10, 10, 0, "local_no_api_charge")

    monkeypatch.setattr("jarvis.app.generate_text", invalid)
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=root,
                                        JARVIS_OLLAMA_MODEL="fixture"))) as client:
        result = client.post(f"/chats/{chat_id}/compactions", headers=csrf(client),
                             json={"expected_version": 0,
                                   "last_message_id": messages[-1]["id"]})
        assert result.status_code == 502
        assert client.get(f"/chats/{chat_id}/compactions").json() == []
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=root, JARVIS_PROVIDER="openrouter",
                                        JARVIS_OPENROUTER_API_KEY="fixture",
                                        JARVIS_OPENROUTER_MODEL="fixture"))) as client:
        assert client.post(f"/chats/{chat_id}/compactions", headers=csrf(client),
                           json={"expected_version": 0,
                                 "last_message_id": messages[-1]["id"]}).status_code == 422


def test_changed_chat_during_model_does_not_save(tmp_path, monkeypatch):
    root = tmp_path / "data"
    db = Database(root / "jarvis.db")
    chat_id, messages = seeded_chat(db, 16)

    async def changing(_db, _settings, _purpose, prompt):
        assert json.loads(prompt[-1]["content"])[0]["n"] == 0
        db.add_message(chat_id, "user", "Nachträgliche Entscheidung")
        return Completion(json.dumps({"entries": [{"kind": "decision",
            "text": "Alter Beschluss", "refs": [0]}]}),
            10, 10, 0, "local_no_api_charge")

    monkeypatch.setattr("jarvis.app.generate_text", changing)
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=root,
                                        JARVIS_OLLAMA_MODEL="fixture"))) as client:
        result = client.post(f"/chats/{chat_id}/compactions", headers=csrf(client),
                             json={"expected_version": 0,
                                   "last_message_id": messages[-1]["id"]})
        assert result.status_code == 409
        assert client.get(f"/chats/{chat_id}/compactions").json() == []


def test_chat_stream_uses_local_snapshot_but_never_cloud_snapshot(tmp_path, monkeypatch):
    root = tmp_path / "data"
    db = Database(root / "jarvis.db")
    chat_id, _ = seeded_chat(db, 20)
    history = db.list_messages(chat_id)
    covered = [item["id"] for item in history[:12]]
    entry = {"kind": "decision", "text": "Kupferfenster wurde beschlossen",
             "message_ids": [history[1]["id"]]}
    db.save_chat_compaction(chat_id, 0, history[-1]["id"], covered, [entry], "fixture")
    seen = []

    class CaptureProvider(ProviderAdapter):
        async def stream(self, messages):
            seen.append(messages)
            yield Chunk("text", text="Antwort")
            yield Chunk("usage", input_tokens=10, output_tokens=2,
                        cost_usd=0, cost_kind="local_no_api_charge")

    monkeypatch.setattr("jarvis.app.make_provider", lambda _settings: CaptureProvider())
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=root,
                                        JARVIS_OLLAMA_MODEL="fixture"))) as client:
        result = client.post(f"/chats/{chat_id}/stream", headers=csrf(client),
                             json={"content": "Was wurde beschlossen?"})
        assert result.status_code == 200 and "event: done" in result.text
    local = seen.pop()
    assert len(local) == 11
    assert "Kupferfenster" in local[1]["content"]
    assert db.list_messages(chat_id)[-1]["compaction_version"] == 1
    assert [item["content"] for item in local[2:-1]] == [
        item["content"] for item in history[12:]]
    assert not any(item["content"] == history[0]["content"] for item in local[2:])

    with TestClient(create_app(Settings(JARVIS_DATA_DIR=root, JARVIS_PROVIDER="openrouter",
                                        JARVIS_OPENROUTER_API_KEY="fixture",
                                        JARVIS_OPENROUTER_MODEL="fixture"))) as client:
        result = client.post(f"/chats/{chat_id}/stream", headers=csrf(client),
                             json={"content": "Cloud-Frage", "allow_cloud": True})
        assert result.status_code == 200 and "event: done" in result.text
    cloud = seen.pop()
    assert not any("Belegter Gesprächszustand" in item["content"] for item in cloud)
    assert db.list_messages(chat_id)[-1]["compaction_version"] is None
    last_id = db.list_messages(chat_id)[-1]["id"]
    reset = db.revise_chat_compaction(chat_id, 1, last_id, None)
    assert reset["version"] == 2 and reset["entries"] == []
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=root,
                                        JARVIS_OLLAMA_MODEL="fixture"))) as client:
        result = client.post(f"/chats/{chat_id}/stream", headers=csrf(client),
                             json={"content": "Nach Reset?"})
        assert result.status_code == 200 and "event: done" in result.text
    assert not any("Belegter Gesprächszustand" in item["content"] for item in seen.pop())
    assert db.list_messages(chat_id)[-1]["compaction_version"] is None


def test_revise_remove_reset_and_recompact_are_versioned(tmp_path, monkeypatch):
    root = tmp_path / "data"
    db = Database(root / "jarvis.db")
    chat_id, _ = seeded_chat(db, 20)
    history = db.list_messages(chat_id)
    covered = [item["id"] for item in history[:12]]
    entries = [{"kind": "decision", "text": "Kupferfenster beschlossen",
                "message_ids": [history[1]["id"]]},
               {"kind": "goal", "text": "Falscher Punkt",
                "message_ids": [history[3]["id"]]}]
    db.save_chat_compaction(chat_id, 0, history[-1]["id"], covered, entries, "fixture")

    async def regenerate(_db, _settings, _purpose, _prompt):
        return Completion(json.dumps({"entries": [{"kind": "decision",
            "text": "Korrigierter Beschluss", "refs": [0]}]}),
            10, 10, 0, "local_no_api_charge")

    monkeypatch.setattr("jarvis.app.generate_text", regenerate)
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=root,
                                        JARVIS_OLLAMA_MODEL="fixture"))) as client:
        headers = csrf(client)
        url = f"/chats/{chat_id}/compactions/revise"
        remove = {"expected_version": 1, "last_message_id": history[-1]["id"],
                  "action": "remove", "entry_index": 1}
        assert client.post(url, json=remove).status_code == 403
        assert client.post(url, headers=headers,
                           json={**remove, "action": "reset"}).status_code == 422
        result = client.post(url, headers=headers, json=remove)
        assert result.status_code == 200, result.text
        assert result.json()["version"] == 2
        assert result.json()["entries"] == entries[:1]
        assert result.json()["created_by"] == "user_remove"
        corrected_context = context_with_snapshot(db.list_messages(chat_id),
            db.latest_chat_compaction(chat_id), "System", "Frage", 24_000, True)
        assert "Kupferfenster" in corrected_context[1]["content"]
        assert "Falscher Punkt" not in corrected_context[1]["content"]
        states = client.get(f"/chats/{chat_id}/compactions").json()
        assert states[1]["entries"] == entries
        assert client.post(url, headers=headers, json=remove).status_code == 409
        assert client.post(url, headers=headers, json={**remove,
            "expected_version": 2, "entry_index": 0}).status_code == 422
        reset = {"expected_version": 2, "last_message_id": history[-1]["id"],
                 "action": "reset"}
        result = client.post(url, headers=headers, json=reset)
        assert result.status_code == 200, result.text
        assert result.json()["version"] == 3
        assert result.json()["covered_message_ids"] == []
        assert result.json()["entries"] == []
        assert result.json()["created_by"] == "user_reset"
        assert client.post(url, headers=headers, json=reset).status_code == 409
        assert client.post(url, headers=headers, json={**reset,
            "expected_version": 3}).status_code == 422
        assert len(db.list_messages(chat_id)) == 20
        assert not any("Belegter Gesprächszustand" in part["content"] for part in
                       context_with_snapshot(db.list_messages(chat_id), None,
                                             "System", "Frage", 24_000, True))
        compact = client.post(f"/chats/{chat_id}/compactions", headers=headers,
                              json={"expected_version": 3,
                                    "last_message_id": history[-1]["id"]})
        assert compact.status_code == 200, compact.text
        assert compact.json()["version"] == 4
        assert compact.json()["created_by"] == "ollama"
        assert len(compact.json()["covered_message_ids"]) == 12
        db.add_message(chat_id, "user", "Neu")
        assert client.post(url, headers=headers, json={**reset,
            "expected_version": 4}).status_code == 409
    archive = tmp_path / "backup.jarvis"
    assert create_backup(root, archive)["schema"] == 55
    restored_root = tmp_path / "restored"
    assert restore_backup(archive, restored_root)["schema"] == 55
    assert Database(restored_root / "jarvis.db").latest_chat_compaction(chat_id)["version"] == 4


def test_schema39_migration_adds_answer_snapshot_reference(tmp_path):
    path = tmp_path / "jarvis.db"
    db = Database(path)
    chat_id, _ = seeded_chat(db, 12)
    history = db.list_messages(chat_id)
    db.save_chat_compaction(chat_id, 0, history[-1]["id"],
        [item["id"] for item in history[:4]],
        [{"kind": "decision", "text": "Altentscheidung",
          "message_ids": [history[0]["id"]]}], "fixture")
    with db.connect() as connection:
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
        connection.execute("ALTER TABLE chat_compactions DROP COLUMN created_by")
        connection.execute("DROP TABLE learning_sessions")
        connection.execute("DROP TABLE memory_proposal_events")
        connection.execute("DROP TABLE memory_proposals")
        connection.execute("DROP TABLE memory_context_uses")
        connection.execute("PRAGMA user_version=39")
    migrated = Database(path)
    assert migrated.list_messages(chat_id)[-1]["compaction_version"] is None
    assert migrated.latest_chat_compaction(chat_id)["created_by"] == "ollama"
    assert migrated.latest_chat_compaction(chat_id)["entries"][0]["text"] == "Altentscheidung"
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 55


def test_revision_waits_for_running_answer_and_rechecks_last_message(tmp_path, monkeypatch):
    root = tmp_path / "data"
    db = Database(root / "jarvis.db")
    chat_id, _ = seeded_chat(db, 20)
    history = db.list_messages(chat_id)
    db.save_chat_compaction(chat_id, 0, history[-1]["id"],
        [item["id"] for item in history[:12]], [
            {"kind": "decision", "text": "Erster Punkt", "message_ids": [history[0]["id"]]},
            {"kind": "goal", "text": "Zweiter Punkt", "message_ids": [history[2]["id"]]}],
        "fixture")
    started, release = Event(), Event()

    class BlockingProvider(ProviderAdapter):
        async def stream(self, _messages: list[dict[str, str]]) -> AsyncIterator[Chunk]:
            started.set()
            await asyncio.to_thread(release.wait)
            yield Chunk("text", text="Fertig")
            yield Chunk("usage", input_tokens=4, output_tokens=2, cost_usd=0,
                        cost_kind="local_no_api_charge")

    monkeypatch.setattr("jarvis.app.make_provider", lambda _settings: BlockingProvider())
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=root,
                                        JARVIS_OLLAMA_MODEL="fixture"))) as client:
        headers = csrf(client)
        result = {}

        def stream():
            result["response"] = client.post(f"/chats/{chat_id}/stream", headers=headers,
                                             json={"content": "Neue Frage"})

        worker = Thread(target=stream, daemon=True)
        worker.start()
        try:
            assert started.wait(15)
            change = {"expected_version": 1, "last_message_id": history[-1]["id"],
                      "action": "remove", "entry_index": 1}
            url = f"/chats/{chat_id}/compactions/revise"
            assert client.post(url, headers=headers, json=change).status_code == 409
        finally:
            release.set()
            worker.join(timeout=15)
        assert not worker.is_alive()
        assert "event: done" in result["response"].text
        assert client.post(url, headers=headers, json=change).status_code == 409
        change["last_message_id"] = db.list_messages(chat_id)[-1]["id"]
        assert client.post(url, headers=headers, json=change).status_code == 200

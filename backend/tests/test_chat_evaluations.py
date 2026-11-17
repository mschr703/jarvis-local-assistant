"""Antwortbewertungen verweisen auf gespeicherte Texte und echte Modellbelege."""

from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from jarvis.app import create_app
from jarvis.backup import create_backup, restore_backup
from jarvis.config import Settings
from jarvis.db import Database
from jarvis.provider import Chunk, ProviderAdapter


class AnswerProvider(ProviderAdapter):
    async def stream(self, messages: list[dict[str, str]]) -> AsyncIterator[Chunk]:
        yield Chunk("text", text="Die Antwort ist vier.")
        yield Chunk("usage", input_tokens=12, output_tokens=5, cost_usd=0,
                    cost_kind="local_no_api_charge")


def test_chat_evaluation_api_evidence_conflict_and_restore(tmp_path, monkeypatch):
    monkeypatch.setattr("jarvis.app.make_provider", lambda _settings: AnswerProvider())
    data = tmp_path / "data"
    settings = Settings(JARVIS_DATA_DIR=data, JARVIS_OLLAMA_MODEL="fixture")
    with TestClient(create_app(settings)) as client:
        csrf = client.get("/session").json()["csrf"]
        headers = {"x-jarvis-csrf": csrf}
        chat_id = client.post("/chats", headers=headers).json()["id"]
        result = client.post(f"/chats/{chat_id}/stream", headers=headers,
                             json={"content": "Wie viel ist zwei plus zwei?"})
        assert "event: done" in result.text
        messages = client.get(f"/chats/{chat_id}").json()["messages"]
        question, answer = messages
        assert answer["rule_version"] == 1
        assert answer["usage_id"] and answer["usage_duration_seconds"] is not None
        assert answer["cost_usd"] == 0 and answer["cost_kind"] == "local_no_api_charge"
        assert answer["evaluation"] is None
        url = f"/chats/{chat_id}/messages/{answer['id']}/evaluation"
        body = {"verdict": "partial", "source_quality": "not_applicable",
                "reason": "Rechnung richtig, Erläuterung fehlt", "regression_case": True,
                "expected_result": "Vier mit kurzem Rechenweg", "version": 0}
        assert client.put(url, json=body).status_code == 403
        assert client.put(url, json={**body, "extra": 1}, headers=headers).status_code == 422
        assert client.put(url.replace(answer["id"], question["id"]),
                          json=body, headers=headers).status_code == 422
        assert client.put(url, json={**body, "verdict": "achieved"}, headers=headers).status_code == 422
        assert client.put(url, json=body, headers=headers).status_code == 200
        assert client.put(url, json=body, headers=headers).status_code == 409
        assert client.put(url.replace(chat_id, "missing"), json=body, headers=headers).status_code == 404
        evaluated = client.get(f"/chats/{chat_id}").json()["messages"][-1]
        assert evaluated["evaluation"]["verdict"] == "partial"
        assert evaluated["content"] == answer["content"]
        cases = client.get("/chat-evaluation-cases").json()
        assert len(cases) == 1
        assert cases[0]["question"] == "Wie viel ist zwei plus zwei?"
        assert cases[0]["answer"] == "Die Antwort ist vier."
        assert cases[0]["rule_version"] == 1
        assert cases[0]["usage_id"] == answer["usage_id"]
        assert cases[0]["expected_result"] == "Vier mit kurzem Rechenweg"
        correction = client.put(url, headers=headers, json={**body, "version": 1,
            "verdict": "achieved", "regression_case": False, "expected_result": ""})
        assert correction.status_code == 200 and correction.json()["version"] == 2
        assert client.get("/chat-evaluation-cases").json() == []
        client.put(url, headers=headers, json={**body, "version": 2})
    archive = tmp_path / "backup.zip"
    assert create_backup(data, archive)["schema"] == 52
    restored = tmp_path / "restored"
    assert restore_backup(archive, restored)["schema"] == 52
    reopened = Database(restored / "jarvis.db")
    cases = reopened.list_chat_evaluation_cases()
    assert len(cases) == 1 and cases[0]["question"] == "Wie viel ist zwei plus zwei?"
    assert reopened.list_messages(chat_id)[-1]["evaluation"]["version"] == 3


def test_parallel_chat_evaluation_and_legacy_usage_are_honest(tmp_path):
    db = Database(tmp_path / "jarvis.db")
    chat_id = db.create_chat()["id"]
    db.add_message(chat_id, "user", "Alte Frage")
    answer = db.add_message(chat_id, "assistant", "Alte Antwort")
    listed = db.list_messages(chat_id)[-1]
    assert listed["usage_id"] is None and listed["usage_duration_seconds"] is None
    assert listed["rule_version"] is None
    with ThreadPoolExecutor(max_workers=2) as pool:
        changes = [pool.submit(db.evaluate_chat_answer, chat_id, answer["id"],
                               "failed", "unsupported", f"Fehler {index}", True,
                               "Belegte Antwort", 0) for index in range(2)]
        success = 0
        for change in changes:
            try:
                change.result()
                success += 1
            except RuntimeError:
                pass
    assert success == 1
    cases = db.list_chat_evaluation_cases()
    assert cases[0]["question"] == "Alte Frage"
    assert cases[0]["usage_id"] is None
    with pytest.raises(ValueError, match="Quellenbewertung"):
        db.evaluate_chat_answer(chat_id, answer["id"], "failed", "invented", "Nein",
                                False, "", 1)

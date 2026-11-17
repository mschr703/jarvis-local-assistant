import os
from concurrent.futures import ThreadPoolExecutor
from email import policy
from email.parser import BytesParser

import pytest
from fastapi.testclient import TestClient

from jarvis.app import create_app
from jarvis.config import Settings
from jarvis.db import Database
from jarvis.drafts import DraftStore
from jarvis.provider import Completion


def test_style_draft_edit_and_idempotent_export(tmp_path, monkeypatch):
    async def fake_generate(db, config, purpose, messages):
        assert purpose == "draft"
        assert "Schreibprobe 1" in messages[-1]["content"]
        return Completion("Hallo, hier ist der Entwurf.", 12, 8, 0.0, "local_no_api_charge")

    monkeypatch.setattr("jarvis.app.generate_text", fake_generate)
    settings = Settings(JARVIS_DATA_DIR=tmp_path, JARVIS_OLLAMA_MODEL="fixture")
    with TestClient(create_app(settings)) as client:
        token = client.get("/session").json()["csrf"]
        headers = {"x-jarvis-csrf": token}
        sample = client.post("/style/samples", json={
            "title": "Kurz", "content": "Guten Tag.", "policy": "local_only",
        }, headers=headers).json()
        assert client.put("/style/profile", json={
            "description": "Freundlich und kurz", "policy": "local_only",
        }, headers=headers).status_code == 200
        response = client.post("/drafts/generate", json={
            "kind": "email", "brief": "Termin bestätigen", "input_text": "Dienstag passt.",
            "style_sample_ids": [sample["id"]],
        }, headers=headers)
        assert response.status_code == 200, response.text
        draft = response.json()
        assert draft["version"] == 1
        edited = client.patch(f"/drafts/{draft['id']}", json={
            "content": "Hallo, Dienstag passt. Viele Grüße", "expected_version": 1,
        }, headers=headers)
        assert edited.status_code == 200
        assert edited.json()["version"] == 2
        assert client.patch(f"/drafts/{draft['id']}", json={
            "content": "stale", "expected_version": 1,
        }, headers=headers).status_code == 409
        assert client.post(f"/drafts/{draft['id']}/export", json={
            "format": "eml", "expected_version": 1,
        }, headers=headers).status_code == 409
        first = client.post(f"/drafts/{draft['id']}/export", json={
            "format": "eml", "expected_version": 2,
        }, headers=headers)
        assert first.status_code == 200, first.text
        second = client.post(f"/drafts/{draft['id']}/export", json={
            "format": "eml", "expected_version": 2,
        }, headers=headers)
        assert first.json() == second.json()
        assert "Dienstag passt" in (tmp_path / "outputs" / f"{draft['id']}.eml").read_text()
        assert client.patch(f"/drafts/{draft['id']}", json={
            "content": "nach Export", "expected_version": 2,
        }, headers=headers).status_code == 409
    store = DraftStore(create_app(settings).state.db, tmp_path)
    assert store.get_draft(draft["id"])["content"] == "Hallo, Dienstag passt. Viele Grüße"


def test_cloud_requires_consent_and_source_policy(tmp_path, monkeypatch):
    async def fake_generate(*args):
        return Completion("Antwort [1]", 10, 3, 0.0, "reported")

    monkeypatch.setattr("jarvis.app.generate_text", fake_generate)
    settings = Settings(JARVIS_DATA_DIR=tmp_path, JARVIS_PROVIDER="openrouter",
                        JARVIS_OPENROUTER_API_KEY="fixture", JARVIS_OPENROUTER_MODEL="fixture")
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    (source_dir / "note.txt").write_text("Interne Zahl 42", encoding="utf-8")
    with TestClient(create_app(settings)) as client:
        token = client.get("/session").json()["csrf"]
        headers = {"x-jarvis-csrf": token}
        folder = client.post("/folders", json={"path": str(source_dir), "policy": "local_only"}, headers=headers).json()
        client.post(f"/folders/{folder['id']}/index", headers=headers)
        passage = client.get("/search?q=Interne").json()[0]["passage_id"]
        payload = {"kind": "answer", "brief": "Welche Zahl?", "passage_ids": [passage]}
        assert client.post("/drafts/generate", json=payload, headers=headers).status_code == 422
        payload["allow_cloud"] = True
        response = client.post("/drafts/generate", json=payload, headers=headers)
        assert response.status_code == 422
        assert "Cloud" in response.json()["detail"]


def test_cloud_draft_sends_source_basename_but_keeps_local_provenance(tmp_path, monkeypatch):
    prompts = []

    async def fake_generate(_db, _config, _purpose, messages):
        prompts.append(messages[-1]["content"])
        return Completion("Die Zahl ist 42. [1]", 10, 3, 0.0, "reported")

    monkeypatch.setattr("jarvis.app.generate_text", fake_generate)
    settings = Settings(JARVIS_DATA_DIR=tmp_path / "data", JARVIS_PROVIDER="openrouter",
                        JARVIS_OPENROUTER_API_KEY="fixture", JARVIS_OPENROUTER_MODEL="fixture")
    source_dir = tmp_path / "private-parent" / "sources"
    source_dir.mkdir(parents=True)
    source = source_dir / "note.txt"
    source.write_text("Interne Zahl 42", encoding="utf-8")
    with TestClient(create_app(settings)) as client:
        token = client.get("/session").json()["csrf"]
        headers = {"x-jarvis-csrf": token}
        folder = client.post("/folders", json={"path": str(source_dir),
                                                "policy": "cloud_allowed"}, headers=headers).json()
        client.post(f"/folders/{folder['id']}/index", headers=headers).raise_for_status()
        passage = client.get("/search?q=Interne").json()[0]["passage_id"]
        response = client.post("/drafts/generate", headers=headers, json={
            "kind": "answer", "brief": "Welche Zahl?", "passage_ids": [passage],
            "allow_cloud": True,
        })
        response.raise_for_status()
        assert str(source_dir) not in prompts[0]
        assert "note.txt" in prompts[0] and "Interne Zahl 42" in prompts[0]
        assert response.json()["sources"][0]["path"] == str(source)


def test_parallel_export_creates_one_versioned_file(tmp_path):
    store = DraftStore(Database(tmp_path / "jarvis.db"), tmp_path)
    draft = store.create_draft("email", "", "Termin", "Hallo", [], "", "")
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: store.export_draft(draft["id"], "eml", 1), range(2)))
    assert results[0]["path"] == results[1]["path"]
    assert len(store.get_draft(draft["id"])["exports"]) == 1


def test_eml_subject_removes_control_characters(tmp_path):
    store = DraftStore(Database(tmp_path / "jarvis.db"), tmp_path)
    draft = store.create_draft("email", "", "Termin\x00\r\n  bestätigen", "Hallo", [], "", "")
    exported = store.export_draft(draft["id"], "eml", 1)
    data = (tmp_path / "outputs" / f"{draft['id']}.eml").read_bytes()
    message = BytesParser(policy=policy.default).parsebytes(data)
    assert str(message["Subject"]) == "Termin bestätigen"
    assert b"\x00" not in data
    assert b"\r\n" in data and b"\n" not in data.replace(b"\r\n", b"")
    assert exported["sha256"]


def test_failed_export_flush_removes_partial_file_and_can_retry(tmp_path, monkeypatch):
    store = DraftStore(Database(tmp_path / "jarvis.db"), tmp_path)
    draft = store.create_draft("text", "", "Kurz", "Inhalt", [], "", "")
    original_fsync = os.fsync
    failed = False

    def fail_once(descriptor):
        nonlocal failed
        if not failed:
            failed = True
            raise OSError("simulierter Schreibfehler")
        return original_fsync(descriptor)

    monkeypatch.setattr("jarvis.drafts.os.fsync", fail_once)
    with pytest.raises(OSError, match="Schreibfehler"):
        store.export_draft(draft["id"], "txt", 1)
    assert not (tmp_path / "outputs" / f"{draft['id']}.txt").exists()
    assert store.get_draft(draft["id"])["exports"] == []
    saved = store.export_draft(draft["id"], "txt", 1)
    assert (tmp_path / "outputs" / f"{draft['id']}.txt").read_text() == "Inhalt\n"
    assert saved["format"] == "txt"

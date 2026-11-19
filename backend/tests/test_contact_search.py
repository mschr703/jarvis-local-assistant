"""Local semantic contact search preserves identities and current manual evidence."""

import pytest
from fastapi.testclient import TestClient

from jarvis.app import create_app
from jarvis.config import Settings
from jarvis.contact_search import search
from jarvis.contacts import ContactStore
from jarvis.db import Database


def test_semantic_contacts_rank_confirmed_plans_and_separate_names(tmp_path,
                                                                   monkeypatch):
    db = Database(tmp_path / "data" / "jarvis.db")
    contacts = ContactStore(db)
    private = contacts.create("Alex", "München privat")
    work = contacts.create("Alex", "Arbeit")
    contacts.add_note(private["id"], "Wir lieben Kunstmuseen")
    contacts.add_plan(private["id"], "Museum besuchen", "Am Samstag")
    contacts.add_plan(work["id"], "Quartalsbericht", "Im Büro")
    observed = []

    def fake_embed(_url, texts, _cache_path=None):
        observed.extend(texts)
        return [[1.0, 0.0]] + [[1.0, 0.0] if "Museum besuchen" in body
                               else [0.0, 1.0] for body in texts[1:]]

    monkeypatch.setattr("jarvis.contact_search.embed_texts", fake_embed)
    found = search(db, "Mit wem wollte ich ins Kunstmuseum?",
                   "http://127.0.0.1:11434")
    assert found["checked_contacts"] == 2 and not found["limited"]
    assert found["results"][0]["id"] == private["id"]
    assert found["results"][0]["context"] == "München privat"
    assert "Museum besuchen" in found["results"][0]["excerpt"]
    assert "Quartalsbericht" not in found["results"][0]["excerpt"]
    assert found["results"][0]["source"] == "manuell bestätigt"
    assert any("Vorhaben (offen): Museum besuchen" in text for text in observed)
    with db.connect() as connection:
        assert connection.execute("SELECT count(*) FROM model_usage").fetchone()[0] == 0


def test_semantic_contact_changed_or_deleted_during_model_call_is_hidden(tmp_path,
                                                                          monkeypatch):
    db = Database(tmp_path / "data" / "jarvis.db")
    contacts = ContactStore(db)
    contact = contacts.create("Mira", "Köln")
    note = contacts.add_note(contact["id"], "Museum am Sonntag")

    def change(_url, texts, _cache_path=None):
        contacts.update_note(contact["id"], note["id"], note["version"],
                             "Museum am Montag")
        return [[1.0] for _ in texts]

    monkeypatch.setattr("jarvis.contact_search.embed_texts", change)
    found = search(db, "Wann Museum?", "http://127.0.0.1:11434")
    assert found["results"] == []
    assert found["changed_contacts"] == [contact["id"]]

    def delete(_url, texts, _cache_path=None):
        contacts.delete(contact["id"], contacts.get(contact["id"])["version"])
        return [[1.0] for _ in texts]

    monkeypatch.setattr("jarvis.contact_search.embed_texts", delete)
    found = search(db, "Wann Museum?", "http://127.0.0.1:11434")
    assert found["results"] == [] and found["changed_contacts"] == [contact["id"]]


def test_semantic_contact_bounds_and_api(tmp_path, monkeypatch):
    db = Database(tmp_path / "data" / "jarvis.db")
    contacts = ContactStore(db)
    first = contacts.create("Alex", "Privat")
    second = contacts.create("Mira", "Arbeit")
    contacts.add_note(first["id"], "Aktuelles Museum")
    monkeypatch.setattr("jarvis.contact_search.MAX_CONTACTS", 1)
    monkeypatch.setattr("jarvis.contact_search.embed_texts",
                        lambda _url, texts, _cache_path=None: [[1.0] for _ in texts])
    limited = search(db, "Museum", "http://127.0.0.1:11434")
    assert limited["checked_contacts"] == 1 and limited["limited"]
    assert limited["results"][0]["id"] == first["id"]
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=db.path.parent,
                                        JARVIS_OLLAMA_MODEL="fixture"))) as client:
        assert client.get("/contacts/semantic", params={"q": "Museum"}).status_code == 401
        client.get("/session")
        response = client.get("/contacts/semantic", params={"q": "Museum"})
        assert response.status_code == 200
        assert response.json()["results"][0]["id"] == first["id"]
        assert client.get("/contacts/semantic", params={"q": "x"}).status_code == 422
    assert contacts.get(second["id"])["name"] == "Mira"


def test_semantic_contact_empty_corpus_does_not_call_model(tmp_path, monkeypatch):
    db = Database(tmp_path / "jarvis.db")
    monkeypatch.setattr("jarvis.contact_search.embed_texts",
                        lambda *_args, **_kwargs: pytest.fail("empty corpus reached model"))
    found = search(db, "Wer kommt mit?", "http://127.0.0.1:11434")
    assert found["checked_contacts"] == 0 and found["results"] == []


def test_semantic_contact_limits_personal_details_sent_to_model(tmp_path, monkeypatch):
    db = Database(tmp_path / "jarvis.db")
    contacts = ContactStore(db)
    contact = contacts.create("Mira", "Privat")
    for number in range(4):
        contacts.add_note(contact["id"], f"Notiz Nummer {number}")
        contacts.add_plan(contact["id"], f"Vorhaben Nummer {number}")
    observed = []

    def fake_embed(_url, texts, _cache_path=None):
        observed.extend(texts)
        return [[1.0] for _ in texts]

    monkeypatch.setattr("jarvis.contact_search.embed_texts", fake_embed)
    search(db, "Was ist geplant?", "http://127.0.0.1:11434")
    assert "Notiz Nummer 0" not in observed[1]
    assert "Vorhaben Nummer 0" not in observed[1]
    assert all(f"Notiz Nummer {number}" in observed[1] for number in (1, 2, 3))
    assert all(f"Vorhaben Nummer {number}" in observed[1] for number in (1, 2, 3))

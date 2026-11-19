"""Semantic contact search uses only explicitly linked, current life events."""

from jarvis.contact_search import search
from jarvis.contacts import ContactStore
from jarvis.db import Database
from jarvis.memory_events import MemoryEventStore


def test_contact_search_finds_linked_event_without_mixing_same_name(tmp_path, monkeypatch):
    db = Database(tmp_path / "jarvis.db")
    contacts = ContactStore(db)
    private = contacts.create("Alex", "Privat")
    work = contacts.create("Alex", "Arbeit")
    events = MemoryEventStore(db)
    trip = events.create("2024-08-12", "Sommerwochenende", "Am Fluss",
                         "Lyon")
    events.link_contact(trip["id"], private["id"], 1)
    observed = []

    def fake_embed(_url, texts, _cache_path=None):
        observed.extend(texts)
        return [[1.0, 0.0]] + [[1.0, 0.0] if "Sommerwochenende" in body
                               else [0.0, 1.0] for body in texts[1:]]

    monkeypatch.setattr("jarvis.contact_search.embed_texts", fake_embed)
    found = search(db, "Mit wem war ich am Fluss in Lyon?",
                   "http://127.0.0.1:11434")
    assert found["results"][0]["id"] == private["id"]
    assert "Lyon: Sommerwochenende" in found["results"][0]["excerpt"]
    assert "Sommerwochenende" not in next(item for item in observed
                                         if "Alex (Arbeit)" in item)
    assert work["id"] in {item["id"] for item in found["results"]}


def test_event_changes_and_unlinks_during_model_call_hide_contact(tmp_path,
                                                                    monkeypatch):
    db = Database(tmp_path / "jarvis.db")
    contacts = ContactStore(db)
    person = contacts.create("Mira", "Köln")
    events = MemoryEventStore(db)
    trip = events.create("2024-08-12", "Ausflug", location="Lyon")
    events.link_contact(trip["id"], person["id"], 1)

    def change(_url, texts, _cache_path=None):
        events.update(trip["id"], 2, "2024-08-12", "Ausflug", location="Paris")
        return [[1.0] for _ in texts]

    monkeypatch.setattr("jarvis.contact_search.embed_texts", change)
    found = search(db, "Mit wem war ich in Lyon?", "http://127.0.0.1:11434")
    assert found["results"] == [] and found["changed_contacts"] == [person["id"]]

    def unlink(_url, texts, _cache_path=None):
        events.unlink_contact(trip["id"], person["id"], 3)
        return [[1.0] for _ in texts]

    monkeypatch.setattr("jarvis.contact_search.embed_texts", unlink)
    found = search(db, "Mit wem war ich in Paris?", "http://127.0.0.1:11434")
    assert found["results"] == [] and found["changed_contacts"] == [person["id"]]


def test_recent_event_link_brings_old_contact_into_bounded_candidates(tmp_path,
                                                                       monkeypatch):
    db = Database(tmp_path / "jarvis.db")
    contacts = ContactStore(db)
    old = contacts.create("Alex", "Privat")
    contacts.create("Mira", "Arbeit")
    events = MemoryEventStore(db)
    trip = events.create("2020-08-12", "Ausflug", location="Lyon")
    events.link_contact(trip["id"], old["id"], 1)
    monkeypatch.setattr("jarvis.contact_search.MAX_CONTACTS", 1)
    monkeypatch.setattr("jarvis.contact_search.embed_texts",
                        lambda _url, texts, _cache_path=None: [[1.0] for _ in texts])
    found = search(db, "Wer war in Lyon?", "http://127.0.0.1:11434")
    assert found["limited"] and found["checked_contacts"] == 1
    assert found["results"][0]["id"] == old["id"]


def test_contact_search_sends_only_three_recent_bounded_events(tmp_path,
                                                               monkeypatch):
    db = Database(tmp_path / "jarvis.db")
    contacts = ContactStore(db)
    person = contacts.create("Mira", "Privat")
    events = MemoryEventStore(db)
    for number in range(4):
        event = events.create(f"2024-08-{number + 10:02d}", f"Moment {number}",
                              "Beschreibung" * 40, "Lyon")
        events.link_contact(event["id"], person["id"], 1)
    observed = []

    def fake_embed(_url, texts, _cache_path=None):
        observed.extend(texts)
        return [[1.0] for _ in texts]

    monkeypatch.setattr("jarvis.contact_search.embed_texts", fake_embed)
    search(db, "Was war in Lyon?", "http://127.0.0.1:11434")
    assert "Moment 0" not in observed[1]
    assert all(f"Moment {number}" in observed[1] for number in (1, 2, 3))
    assert len(observed[1]) <= 1700

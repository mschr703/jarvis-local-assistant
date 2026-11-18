"""Explicit document preferences affect local semantic candidate selection safely."""

from jarvis.db import Database
from jarvis.documents import DocumentStore
from jarvis.semantic_search import search
from jarvis.source_preferences import SourcePreferences

QUESTION = "Wo liegt mein Mietvertrag?"
URL = "http://127.0.0.1:11434"


def fixture_sources(tmp_path):
    root = tmp_path / "allowed"
    root.mkdir()
    for number in range(36):
        (root / f"a_{number:02}.md").write_text(
            f"Vereinbarung zur Wohnung Nummer {number}.", encoding="utf-8")
    target = root / "z_preferred.md"
    target.write_text("Das Rezept für Zitronenkuchen.", encoding="utf-8")
    db = Database(tmp_path / "data" / "jarvis.db")
    store = DocumentStore(db)
    folder = store.add_folder(str(root), "local_only")
    assert store.index_folder(folder["id"])["indexed"] == 37
    target_id = next(item["id"] for item in store.documents()
                     if item["path"] == str(target))
    return db, store, folder, target, target_id


def fake_embed(_url, texts):
    return [[1.0, 0.0] if "Vereinbarung" in text or text == QUESTION else [0.0, 1.0]
            for text in texts]


def test_preferred_late_document_is_checked_and_shown_without_fake_similarity(tmp_path, monkeypatch):
    db, _, _, target, target_id = fixture_sources(tmp_path)
    monkeypatch.setattr("jarvis.semantic_search.embed_texts", fake_embed)
    ordinary = search(db, QUESTION, URL)
    assert target_id not in {item["id"] for item in ordinary["results"]}
    SourcePreferences(db).add(QUESTION, target_id)
    preferred = search(db, QUESTION, URL)
    assert preferred["results"][0]["id"] == target_id
    assert preferred["results"][0]["preferred"] is True
    assert preferred["results"][0]["similarity"] == 0.0
    assert preferred["results"][0]["source"] == str(target)
    assert preferred["checked_passages"] <= 100


def test_stale_preference_never_reaches_model_or_exposes_old_excerpt(tmp_path, monkeypatch):
    db, _, _, target, target_id = fixture_sources(tmp_path)
    SourcePreferences(db).add(QUESTION, target_id)
    target.write_text("Neuer Inhalt ohne Zitronenkuchen.", encoding="utf-8")
    observed = []

    def collect(_url, texts):
        observed.extend(texts)
        return fake_embed(_url, texts)

    monkeypatch.setattr("jarvis.semantic_search.embed_texts", collect)
    result = search(db, QUESTION, URL)
    assert result["preferences"][0]["status"] == "needs_reindex"
    assert target_id not in {item["id"] for item in result["results"]}
    assert not any("Zitronenkuchen" in text for text in observed)
    assert not any("excerpt" in item for item in result["preferences"])


def test_updated_preference_is_marked_but_current_source_is_used(tmp_path, monkeypatch):
    db, store, folder, target, target_id = fixture_sources(tmp_path)
    SourcePreferences(db).add(QUESTION, target_id)
    target.write_text("Aktuelle Rechnung zur Wohnung.", encoding="utf-8")
    assert store.index_folder(folder["id"])["indexed"] == 1
    monkeypatch.setattr("jarvis.semantic_search.embed_texts", fake_embed)
    result = search(db, QUESTION, URL)
    hit = result["results"][0]
    assert hit["id"] == target_id and hit["preferred"]
    assert hit["preference_status"] == "updated"
    assert hit["source_status"] == "current"
    assert "Aktuelle Rechnung" in hit["excerpt"]


def test_folder_filter_and_deep_scope_override_preference(tmp_path, monkeypatch):
    db, store, _, _target, target_id = fixture_sources(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    chosen = other / "chosen.md"
    chosen.write_text("Vertrag in einer zweiten Freigabe.", encoding="utf-8")
    folder = store.add_folder(str(other), "local_only")
    store.index_folder(folder["id"])
    chosen_id = next(item["id"] for item in store.documents()
                     if item["path"] == str(chosen))
    SourcePreferences(db).add(QUESTION, target_id)
    monkeypatch.setattr("jarvis.semantic_search.embed_texts", fake_embed)
    filtered = search(db, QUESTION, URL, folder["id"])
    assert [item["id"] for item in filtered["results"] if item["kind"] == "document"] == [chosen_id]
    deep = search(db, QUESTION, URL, folder["id"], chosen_id)
    assert deep["preferences"] == []
    assert [item["id"] for item in deep["results"]] == [chosen_id]

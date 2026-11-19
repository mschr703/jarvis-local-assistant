"""Semantic retrieval keeps memory and work-state references current and local."""

import json

import pytest
from fastapi.testclient import TestClient

from jarvis.app import create_app
from jarvis.config import Settings
from jarvis.db import Database
from jarvis.documents import DocumentStore
from jarvis.project_aliases import ProjectAliasStore
from jarvis.project_memories import ProjectMemoryStore
from jarvis.project_people import ProjectPeopleStore
from jarvis.semantic_search import search
from jarvis.tasks import TaskStore
from jarvis.work_states import WorkStateStore


def sample(tmp_path):
    root = tmp_path / "allowed"
    root.mkdir()
    source = root / "Wohnung.md"
    source.write_text("Die Vereinbarung zur Wohnung liegt im roten Ordner.", encoding="utf-8")
    db = Database(tmp_path / "data" / "jarvis.db")
    docs = DocumentStore(db)
    folder = docs.add_folder(str(root), "local_only")
    docs.index_folder(folder["id"])
    memory = docs.add_memory("Die Wohnungsunterlagen kommen am Freitag an.",
                             "Gespräch vom Montag", "local_only")
    states = WorkStateStore(db, docs, TaskStore(db))
    project = states.create("Umzug", "Zusagen liegen vor", "Kaution offen",
                            "Mietunterlagen sortieren", [], [], [])
    return db, docs, folder, memory, project, source


def test_semantic_search_combines_three_local_source_kinds(tmp_path, monkeypatch):
    db, docs, _, memory, project, source = sample(tmp_path)
    observed = []

    def fake_embed(_url, texts, _cache_path=None):
        observed.extend(texts)
        return [[1.0, 0.0] for _ in texts]

    monkeypatch.setattr("jarvis.semantic_search.embed_texts", fake_embed)
    found = search(db, "Was brauche ich für den Mietbeginn?", "http://127.0.0.1:11434")
    assert found["counts"] == {"document": 1, "memory": 1, "project": 1, "life_event": 0}
    assert found["checked_passages"] == 1
    assert found["checked_memories"] == 1 and found["checked_projects"] == 1
    assert len(observed) == 4
    by_kind = {item["kind"]: item for item in found["results"]}
    assert by_kind["document"]["source"] == str(source.resolve())
    assert by_kind["document"]["sha256"] == docs.documents()[0]["sha256"]
    assert by_kind["memory"]["id"] == memory["id"]
    assert by_kind["memory"]["source"] == "Gespräch vom Montag"
    assert by_kind["memory"]["policy"] == "local_only"
    assert by_kind["project"]["id"] == project["id"]
    assert by_kind["project"]["version"] == 1
    assert not found["limited"] and not found["changed_sources"]


def test_explicit_project_memory_reaches_local_semantic_corpus(tmp_path, monkeypatch):
    db, docs, _, memory, project, _ = sample(tmp_path)
    links = ProjectMemoryStore(db, WorkStateStore(db, docs, TaskStore(db)))
    links.add(project["id"], project["version"], memory["id"],
              "Absprache aus dem Erstgespräch")
    docs.update_memory(memory["id"], "Die Schlüssel wurden abgegeben.",
                       "Spätere Korrektur", "local_only")
    observed = []

    def fake_embed(_url, texts, _cache_path=None):
        observed.extend(texts)
        return [[1.0, 0.0] for _ in texts]

    monkeypatch.setattr("jarvis.semantic_search.embed_texts", fake_embed)
    found = search(db, "Welche Unterlage fehlt noch?", "http://127.0.0.1:11434",
                   source_kind="project")
    assert found["counts"]["project"] == 1
    assert len(observed) == 2
    assert "Projekterinnerung (gespeichert): Die Wohnungsunterlagen kommen am Freitag an." in observed[1]
    assert "Ursprüngliche Quelle: Gespräch vom Montag" in observed[1]
    assert "Herkunft: Absprache aus dem Erstgespräch" in observed[1]
    assert "Die Schlüssel wurden abgegeben" not in observed[1]
    assert "Projekterinnerung (gespeichert)" in found["results"][0]["excerpt"]


@pytest.mark.parametrize("change", ["link_removed", "link_updated", "original_updated",
                                    "original_deleted"])
def test_project_memory_change_during_embedding_is_rejected(tmp_path, monkeypatch, change):
    db, docs, _, memory, project, _ = sample(tmp_path)
    links = ProjectMemoryStore(db, WorkStateStore(db, docs, TaskStore(db)))
    link = links.add(project["id"], project["version"], memory["id"],
                     "Absprache aus dem Erstgespräch")

    def mutate(_url, texts, _cache_path=None):
        if change == "link_removed":
            links.delete(project["id"], link["id"], project["version"], link["version"])
        elif change == "link_updated":
            links.update(project["id"], link["id"], project["version"], link["version"],
                         "Korrigierte Herkunftsnotiz")
        elif change == "original_updated":
            docs.update_memory(memory["id"], "Korrigierter Inhalt", "Korrektur", "local_only")
        else:
            docs.delete_memory(memory["id"])
        return [[1.0, 0.0] for _ in texts]

    monkeypatch.setattr("jarvis.semantic_search.embed_texts", mutate)
    found = search(db, "Welche Unterlage fehlt noch?", "http://127.0.0.1:11434",
                   source_kind="project")
    assert found["counts"]["project"] == 0
    assert found["changed_sources"] == [{"kind": "project", "id": project["id"]}]


def test_project_semantic_memory_limit_is_reported(tmp_path, monkeypatch):
    db, docs, _, memory, project, _ = sample(tmp_path)
    second = docs.add_memory("Die Schlüssel kommen am Samstag.",
                             "Weitere Notiz", "local_only")
    links = ProjectMemoryStore(db, WorkStateStore(db, docs, TaskStore(db)))
    for item in (memory, second):
        links.add(project["id"], project["version"], item["id"],
                  "Manuell geprüfter Projektbeleg")
    monkeypatch.setattr("jarvis.semantic_search.MAX_PROJECT_RELATIONS_PER_KIND", 1)
    monkeypatch.setattr("jarvis.semantic_search.embed_texts",
                        lambda _url, texts, _path=None: [[1.0, 0.0] for _ in texts])
    found = search(db, "Welche Unterlage fehlt noch?", "http://127.0.0.1:11434",
                   source_kind="project")
    assert found["limited"] and found["counts"]["project"] == 1


def test_project_people_and_aliases_reach_local_semantic_corpus(tmp_path, monkeypatch):
    db, _, _, _, project, _ = sample(tmp_path)
    states = WorkStateStore(db, DocumentStore(db), TaskStore(db))
    people = ProjectPeopleStore(db, states)
    person = people.add(project["id"], project["version"], "Mira Weber", "reviewer",
                        "Prüft Heizkosten", None)
    ProjectAliasStore(db, states, people).add(
        project["id"], project["version"], person["id"], person["version"],
        "Mimi", "Rufname im Team", None)
    observed = []

    def fake_embed(_url, texts, _cache_path=None):
        observed.extend(texts)
        return [[1.0, 0.0] for _ in texts]

    monkeypatch.setattr("jarvis.semantic_search.embed_texts", fake_embed)
    found = search(db, "Wer prüft Nebenkosten?", "http://127.0.0.1:11434",
                   source_kind="project")
    assert len(observed) == 2 and "Mira Weber" in observed[1]
    assert "Mimi → Mira Weber" in observed[1]
    assert "Prüft Heizkosten" in observed[1]
    assert found["counts"]["project"] == 1
    assert "Projektperson: Mira Weber" in found["results"][0]["excerpt"]
    assert "Personenalias: Mimi → Mira Weber" in found["results"][0]["excerpt"]


@pytest.mark.parametrize("changed_table", ["project_people", "project_person_aliases"])
def test_project_relationship_changed_during_embedding_is_rejected(
        tmp_path, monkeypatch, changed_table):
    db, _, _, _, project, _ = sample(tmp_path)
    states = WorkStateStore(db, DocumentStore(db), TaskStore(db))
    people = ProjectPeopleStore(db, states)
    person = people.add(project["id"], project["version"], "Mira Weber", "reviewer",
                        "Prüft Heizkosten", None)
    alias = ProjectAliasStore(db, states, people).add(
        project["id"], project["version"], person["id"], person["version"],
        "Mimi", "Rufname im Team", None)

    def mutate(_url, texts, _cache_path=None):
        with db.connect() as connection:
            if changed_table == "project_people":
                connection.execute("UPDATE project_people SET name=? WHERE id=?",
                                   ("Nora Weber", person["id"]))
            else:
                connection.execute("UPDATE project_person_aliases SET alias=? WHERE id=?",
                                   ("Nini", alias["id"]))
        return [[1.0, 0.0] for _ in texts]

    monkeypatch.setattr("jarvis.semantic_search.embed_texts", mutate)
    found = search(db, "Wer prüft Nebenkosten?", "http://127.0.0.1:11434",
                   source_kind="project")
    assert found["counts"]["project"] == 0
    assert found["changed_sources"] == [{"kind": "project", "id": project["id"]}]


def test_project_semantic_relationship_limit_is_reported(tmp_path, monkeypatch):
    db, _, _, _, project, _ = sample(tmp_path)
    states = WorkStateStore(db, DocumentStore(db), TaskStore(db))
    people = ProjectPeopleStore(db, states)
    for index in range(3):
        people.add(project["id"], project["version"], f"Mira {index}", "contact",
                   "Eigene Projektangabe", None)
    monkeypatch.setattr("jarvis.semantic_search.MAX_PROJECT_RELATIONS_PER_KIND", 2)
    monkeypatch.setattr("jarvis.semantic_search.embed_texts",
                        lambda _url, texts, _path=None: [[1.0, 0.0] for _ in texts])
    found = search(db, "Mira", "http://127.0.0.1:11434", source_kind="project")
    assert found["limited"] and found["counts"]["project"] == 1


def test_linked_project_task_reaches_local_semantic_corpus(tmp_path, monkeypatch):
    db, _, _, _, project, _ = sample(tmp_path)
    tasks = TaskStore(db)
    task = tasks.create("Budgetplanung", "", None, "Europe/Paris")
    states = WorkStateStore(db, DocumentStore(db), tasks)
    project = states.update(project["id"], project["version"], project["project"],
                            project["confirmed"], project["open_questions"],
                            project["next_step"], [], [task["id"]], [])
    tasks.update(task["id"], "Kostenprüfung", "", None, "Europe/Paris")
    observed = []

    def fake_embed(_url, texts, _cache_path=None):
        observed.extend(texts)
        return [[1.0, 0.0] for _ in texts]

    monkeypatch.setattr("jarvis.semantic_search.embed_texts", fake_embed)
    found = search(db, "Was kostet der Umzug?", "http://127.0.0.1:11434",
                   source_kind="project")
    assert "Projektaufgabe: Kostenprüfung (damals: Budgetplanung) · offen" in observed[1]
    assert "Kostenprüfung (damals: Budgetplanung) · offen" in found["results"][0]["excerpt"]
    assert found["results"][0]["id"] == project["id"]


@pytest.mark.parametrize("change", ["rename", "complete", "delete"])
def test_linked_task_changed_during_embedding_is_rejected(tmp_path, monkeypatch, change):
    db, _, _, _, project, _ = sample(tmp_path)
    tasks = TaskStore(db)
    task = tasks.create("Budgetplanung", "", None, "Europe/Paris")
    states = WorkStateStore(db, DocumentStore(db), tasks)
    project = states.update(project["id"], project["version"], project["project"],
                            project["confirmed"], project["open_questions"],
                            project["next_step"], [], [task["id"]], [])

    def mutate(_url, texts, _cache_path=None):
        if change == "rename":
            tasks.update(task["id"], "Kostenprüfung", "", None, "Europe/Paris")
        elif change == "complete":
            tasks.complete(task["id"])
        else:
            tasks.delete(task["id"])
        return [[1.0, 0.0] for _ in texts]

    monkeypatch.setattr("jarvis.semantic_search.embed_texts", mutate)
    found = search(db, "Was kostet der Umzug?", "http://127.0.0.1:11434",
                   source_kind="project")
    assert found["counts"]["project"] == 0
    assert found["changed_sources"] == [{"kind": "project", "id": project["id"]}]


def test_project_semantic_task_limit_is_reported(tmp_path, monkeypatch):
    db, _, _, _, project, _ = sample(tmp_path)
    tasks = TaskStore(db)
    ids = [tasks.create(f"Budgetaufgabe {index}", "", None, "Europe/Paris")["id"]
           for index in range(3)]
    states = WorkStateStore(db, DocumentStore(db), tasks)
    states.update(project["id"], project["version"], project["project"],
                  project["confirmed"], project["open_questions"],
                  project["next_step"], [], ids, [])
    monkeypatch.setattr("jarvis.semantic_search.MAX_PROJECT_RELATIONS_PER_KIND", 2)
    monkeypatch.setattr("jarvis.semantic_search.embed_texts",
                        lambda _url, texts, _path=None: [[1.0, 0.0] for _ in texts])
    found = search(db, "Budgetaufgabe", "http://127.0.0.1:11434",
                   source_kind="project")
    assert found["limited"] and found["counts"]["project"] == 1


def test_linked_project_filename_reaches_ollama_without_absolute_path(tmp_path, monkeypatch):
    db, docs, _, _, project, source = sample(tmp_path)
    document = docs.documents()[0]
    states = WorkStateStore(db, docs, TaskStore(db))
    project = states.update(project["id"], project["version"], project["project"],
                            project["confirmed"], project["open_questions"],
                            project["next_step"], [document["id"]], [], [])
    # A restored backup may contain Windows-style paths on another OS.
    with db.connect() as connection:
        connection.execute("UPDATE work_states SET documents_json=? WHERE id=?",
                           (json.dumps([{"id": document["id"],
                                         "path": r"C:\Users\private\Wohnung.md",
                                         "sha256": document["sha256"]}]), project["id"]))
    observed = []

    def fake_embed(_url, texts, _cache_path=None):
        observed.extend(texts)
        return [[1.0, 0.0] for _ in texts]

    monkeypatch.setattr("jarvis.semantic_search.embed_texts", fake_embed)
    found = search(db, "Welche Unterlage?", "http://127.0.0.1:11434",
                   source_kind="project")
    assert "Projektdatei: Wohnung.md" in observed[1]
    assert str(source.parent) not in observed[1]
    assert r"C:\Users\private" not in observed[1]
    assert "Projektdatei: Wohnung.md" in found["results"][0]["excerpt"]
    assert str(source.parent) not in found["results"][0]["excerpt"]
    assert r"C:\Users\private" not in found["results"][0]["excerpt"]
    assert found["results"][0]["id"] == project["id"]


def test_linked_project_file_removed_during_embedding_is_rejected(tmp_path, monkeypatch):
    db, docs, _, _, project, _ = sample(tmp_path)
    document = docs.documents()[0]
    states = WorkStateStore(db, docs, TaskStore(db))
    project = states.update(project["id"], project["version"], project["project"],
                            project["confirmed"], project["open_questions"],
                            project["next_step"], [document["id"]], [], [])

    def remove_link(_url, texts, _cache_path=None):
        states.update(project["id"], project["version"], project["project"],
                      project["confirmed"], project["open_questions"],
                      project["next_step"], [], [], [])
        return [[1.0, 0.0] for _ in texts]

    monkeypatch.setattr("jarvis.semantic_search.embed_texts", remove_link)
    found = search(db, "Welche Unterlage?", "http://127.0.0.1:11434",
                   source_kind="project")
    assert found["counts"]["project"] == 0
    assert found["changed_sources"] == [{"kind": "project", "id": project["id"]}]


def test_project_semantic_filename_limit_is_reported(tmp_path, monkeypatch):
    db, docs, folder, _, project, source = sample(tmp_path)
    for index in range(2):
        source.with_name(f"Anlage_{index}.md").write_text("Eigene Datei.", encoding="utf-8")
    docs.index_folder(folder["id"])
    states = WorkStateStore(db, docs, TaskStore(db))
    ids = [item["id"] for item in docs.documents()]
    states.update(project["id"], project["version"], project["project"],
                  project["confirmed"], project["open_questions"],
                  project["next_step"], ids, [], [])
    monkeypatch.setattr("jarvis.semantic_search.MAX_PROJECT_RELATIONS_PER_KIND", 2)
    monkeypatch.setattr("jarvis.semantic_search.embed_texts",
                        lambda _url, texts, _path=None: [[1.0, 0.0] for _ in texts])
    found = search(db, "Unterlagen", "http://127.0.0.1:11434",
                   source_kind="project")
    assert found["limited"] and found["counts"]["project"] == 1


def test_semantic_source_kind_filters_corpus_before_local_embedding(tmp_path, monkeypatch):
    db, _, folder, memory, project, _ = sample(tmp_path)
    calls = []

    def fake_embed(_url, texts, _cache_path=None):
        calls.append(texts)
        return [[1.0, 0.0] for _ in texts]

    monkeypatch.setattr("jarvis.semantic_search.embed_texts", fake_embed)
    with monkeypatch.context() as patch:
        patch.setattr(DocumentStore, "source_is_current", staticmethod(
            lambda _row: pytest.fail("Ungewählte Dokumente wurden geprüft")))
        for kind, expected_id in (("memory", memory["id"]),
                                  ("project", project["id"])):
            found = search(db, "Was steht an?", "http://127.0.0.1:11434",
                           source_kind=kind)
            assert [(item["kind"], item["id"]) for item in found["results"]] == [
                (kind, expected_id)]
            assert len(calls[-1]) == 2
            assert found["checked_passages"] == 0
            assert found["preferences"] == []
    found = search(db, "Was steht an?", "http://127.0.0.1:11434",
                   source_kind="document")
    assert found["counts"] == {"document": 1, "memory": 0, "project": 0, "life_event": 0}
    assert found["checked_memories"] == found["checked_projects"] == 0
    assert len(calls[-1]) == 2
    with pytest.raises(ValueError, match="Quelltyp"):
        search(db, "Frage", "http://127.0.0.1:11434", source_kind="unknown")
    with pytest.raises(ValueError, match="passen nicht zusammen"):
        search(db, "Frage", "http://127.0.0.1:11434", folder["id"],
               source_kind="memory")


def test_changed_memory_and_project_are_not_displayed(tmp_path, monkeypatch):
    db, _, _, memory, project, _ = sample(tmp_path)

    def change_during_embed(_url, texts, _cache_path=None):
        with db.connect() as connection:
            connection.execute("UPDATE memories SET text=? WHERE id=?",
                               ("Neue Erinnerung", memory["id"]))
            connection.execute("UPDATE work_states SET next_step=? WHERE id=?",
                               ("Neuer Schritt", project["id"]))
        return [[1.0] for _ in texts]

    monkeypatch.setattr("jarvis.semantic_search.embed_texts", change_during_embed)
    found = search(db, "Was steht an?", "http://127.0.0.1:11434")
    assert found["counts"] == {"document": 1, "memory": 0, "project": 0, "life_event": 0}
    assert {(item["kind"], item["id"]) for item in found["changed_sources"]} == {
        ("memory", memory["id"]), ("project", project["id"])}
    assert all("Freitag" not in item["excerpt"] for item in found["results"])


def test_semantic_search_works_without_documents_and_bounds_saved_sources(tmp_path,
                                                                            monkeypatch):
    db = Database(tmp_path / "data" / "jarvis.db")
    docs = DocumentStore(db)
    states = WorkStateStore(db, docs, TaskStore(db))
    for index in range(4):
        docs.add_memory(f"Erinnerung {index}", "Eigene Notiz", "local_only")
        states.create(f"Projekt {index}", "Fertig", "", "Nächster Schritt", [], [], [])
    import jarvis.semantic_search as module
    monkeypatch.setattr(module, "MAX_MEMORIES", 2)
    monkeypatch.setattr(module, "MAX_PROJECTS", 2)
    observed = []

    def fake_embed(_url, texts, _cache_path=None):
        observed.extend(texts)
        return [[1.0] for _ in texts]

    monkeypatch.setattr(module, "embed_texts", fake_embed)
    found = search(db, "Welche Projekte?", "http://127.0.0.1:11434")
    assert found["counts"] == {"document": 0, "memory": 2, "project": 2, "life_event": 0}
    assert found["checked_passages"] == 0
    assert found["checked_memories"] == 2 and found["checked_projects"] == 2
    assert len(observed) == 5 and found["limited"]


def test_semantic_source_api_returns_evidence(tmp_path, monkeypatch):
    db, _, _, memory, project, _ = sample(tmp_path)
    monkeypatch.setattr("jarvis.semantic_search.embed_texts",
                        lambda _url, texts, _cache_path=None: [[1.0] for _ in texts])
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=db.path.parent,
                                        JARVIS_OLLAMA_MODEL="fixture"))) as client:
        client.get("/session")
        found = client.get("/search/semantic", params={"q": "Wohnung"})
        assert found.status_code == 200
        body = found.json()
        assert body["counts"] == {"document": 1, "memory": 1, "project": 1,
                                   "life_event": 0}
        assert {memory["id"], project["id"]}.issubset(
            {item["id"] for item in body["results"]})
        filtered = client.get("/search/semantic", params={"q": "Wohnung", "kind": "project"})
        assert filtered.status_code == 200
        assert filtered.json()["counts"] == {"document": 0, "memory": 0, "project": 1,
                                              "life_event": 0}
        assert client.get("/search/semantic", params={
            "q": "Wohnung", "kind": "project", "document_id": "wrong"}).status_code == 422
        assert client.get("/search/semantic", params={
            "q": "Wohnung", "kind": "invalid"}).status_code == 422

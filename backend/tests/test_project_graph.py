"""Begrenzte, lokale Projektpfade verwenden nur belegte Beziehungen."""

import pytest
from fastapi.testclient import TestClient
from test_project_people import setup_project

from jarvis.app import create_app
from jarvis.config import Settings
from jarvis.project_aliases import ProjectAliasStore
from jarvis.project_connections import ProjectConnections
from jarvis.project_graph import ProjectGraph
from jarvis.project_merges import ProjectMergeStore
from jarvis.project_people import ProjectPeopleStore
from jarvis.project_person_tasks import ProjectPersonTaskStore


def graph_setup(tmp_path):
    root, db, documents, states, source, folder, document, task, state = setup_project(tmp_path)
    people = ProjectPeopleStore(db, states)
    aliases = ProjectAliasStore(db, states, people)
    links = ProjectPersonTaskStore(db, states, people)
    graph = ProjectGraph(ProjectConnections(states, people, links, aliases))
    return root, db, documents, states, source, folder, document, task, state, people, aliases, links, graph


def test_graph_typed_edges_sources_neighbors_and_paths(tmp_path):
    _, _, documents, states, source_file, folder, document, task, state, people, aliases, links, graph = graph_setup(tmp_path)
    person = people.add(state["id"], 1, "Mira", "lead", "Protokoll nennt Mira",
                        document["id"], "confirmed")
    alias = aliases.add(state["id"], 1, person["id"], 1, "M. Weber",
                        "Im Protokoll abgekürzt", document["id"])
    link = links.add(state["id"], 1, person["id"], task["id"], "responsible",
                     "Mira prüft Atlas", document["id"], "plausible")
    snapshot = graph.snapshot(state["id"])
    assert {node["kind"] for node in snapshot["nodes"]} == {
        "project", "person", "task", "document", "alias"}
    assert {edge["relation"] for edge in snapshot["edges"]} == {
        "project_person", "project_task", "project_document", "person_task",
        "person_evidence", "person_alias", "alias_evidence", "task_evidence"}
    task_edge = next(edge for edge in snapshot["edges"] if edge["id"] == f"person_task:{link['id']}")
    assert task_edge["role"] == "responsible"
    assert task_edge["source_document_id"] == document["id"]
    assert task_edge["confidence"] == "plausible"
    assert task_edge["source_status"] == "unveraendert"
    assert task_edge["source_sha256"] == state["documents"][0]["sha256"]
    assert task_edge["source_path"] == str(source_file)
    person_key = f"person:{person['id']}"
    task_key = f"task:{task['id']}"
    document_key = f"document:{document['id']}"
    project_key = f"project:{state['id']}"
    assert graph.neighbors(state["id"], task_key, "person_task", "out")["neighbors"][0]["id"] == person_key
    assert graph.neighbors(state["id"], person_key, "person_task", "in")["neighbors"][0]["id"] == task_key
    assert graph.neighbors(state["id"], person_key, "person_alias", "out")["neighbors"][0]["id"] == f"alias:{alias['id']}"
    assert graph.neighbors(state["id"], person_key, "person_task", "out")["neighbors"] == []
    filtered = "project_task,person_task,person_evidence"
    assert graph.paths(state["id"], project_key, document_key, 2, filtered)["paths"] == []
    result = graph.paths(state["id"], project_key, document_key, 3, filtered)
    assert len(result["paths"]) == 1
    assert [edge["relation"] for edge in result["paths"][0]["edges"]] == [
        "project_task", "person_task", "person_evidence"]
    source_file.write_text("Protokoll geändert", encoding="utf-8")
    stale = graph.snapshot(state["id"])
    assert next(edge for edge in stale["edges"] if edge["relation"] == "person_evidence")[
        "source_status"] == "geaendert"
    documents.remove_folder(folder["id"])
    revoked = graph.snapshot(state["id"])
    person_edge = next(edge for edge in revoked["edges"] if edge["relation"] == "project_person")
    assert person_edge["source_path"] == str(source_file)
    assert person_edge["source_sha256"] == state["documents"][0]["sha256"]
    assert person_edge["source_status"] not in {"eigene_angabe", "unveraendert"}
    other = states.create("Beta", "", "", "Weiter", [], [], [])
    with pytest.raises(ValueError, match="gehört nicht"):
        graph.neighbors(other["id"], person_key)
    with pytest.raises(ValueError, match="gehört nicht"):
        graph.paths(other["id"], f"project:{other['id']}", document_key)


def test_graph_limits_filters_and_merge_refresh(tmp_path):
    _, db, _, _, _, _, document, task, state, people, aliases, links, graph = graph_setup(tmp_path)
    source = people.add(state["id"], 1, "Mira", "lead", "Eigene Angabe", None)
    target = people.add(state["id"], 1, "Miriam", "contact", "Eigene Angabe", None)
    links.add(state["id"], 1, source["id"], task["id"], "responsible",
              "Aufgabe laut Protokoll", document["id"])
    before = graph.snapshot(state["id"])
    assert any(edge["to"] == f"person:{source['id']}" for edge in before["edges"])
    ProjectMergeStore(db, graph.connections.work_states).merge(
        state["id"], 1, source["id"], 1, target["id"], 1)
    after = graph.snapshot(state["id"])
    assert not any(node["id"] == f"person:{source['id']}" for node in after["nodes"])
    assert any(node["kind"] == "alias" and node["label"] == "Mira" for node in after["nodes"])
    assert any(edge["relation"] == "person_task" and edge["to"] == f"person:{target['id']}"
               for edge in after["edges"])
    moved = next(edge for edge in after["edges"] if edge["relation"] == "person_task")
    assert moved["merged_from_person_name"] == "Mira"
    with pytest.raises(ValueError, match="Unbekannter"):
        graph.neighbors(state["id"], f"project:{state['id']}", "other")
    with pytest.raises(ValueError, match="Pfadtiefe"):
        graph.paths(state["id"], f"project:{state['id']}", f"task:{task['id']}", 4)
    with pytest.raises(ValueError, match="verschieden"):
        graph.paths(state["id"], f"project:{state['id']}", f"project:{state['id']}")
    # Viele ausdrücklich gepflegte Aliasbelege ergeben mehr als 20 mögliche Pfade.
    for index in range(5):
        person = people.add(state["id"], 1, f"Person {index}", "contact", "Eigene Angabe", None)
        for alias_index in range(5):
            aliases.add(state["id"], 1, person["id"], 1,
                        f"P{index}-{alias_index}", "Protokoll nennt Alias", document["id"])
    paths = graph.paths(state["id"], f"project:{state['id']}",
                        f"document:{document['id']}", 3,
                        "project_person,person_alias,alias_evidence")
    assert len(paths["paths"]) == 20
    assert paths["truncated"] is True


def test_graph_api_project_isolation_and_get_only(tmp_path):
    root, _, _, states, _, _, document, task, state, people, _, links, _ = graph_setup(tmp_path)
    person = people.add(state["id"], 1, "Mira", "lead", "Eigene Angabe", None)
    links.add(state["id"], 1, person["id"], task["id"], "reviewer",
              "Mira prüft", document["id"])
    other = states.create("Beta", "", "", "Weiter", [], [], [])
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=root))) as client:
        client.get("/session")
        base = f"/work-states/{state['id']}/graph"
        assert client.get(base).status_code == 200
        assert client.get(f"/work-states/{other['id']}/graph").json()["nodes"] == [
            {"id": f"project:{other['id']}", "kind": "project", "label": "Beta"}]
        assert client.get("/work-states/missing/graph").status_code == 404
        assert client.post(base).status_code in (403, 405)
        neighbors = client.get(f"{base}/neighbors", params={
            "node": f"task:{task['id']}", "relations": "person_task", "direction": "out"})
        assert neighbors.status_code == 200
        assert neighbors.json()["neighbors"][0]["id"] == f"person:{person['id']}"
        assert client.get(f"{base}/neighbors", params={
            "node": f"person:{person['id']}", "direction": "sideways"}).status_code == 422
        assert client.get(f"/work-states/{other['id']}/graph/neighbors", params={
            "node": f"person:{person['id']}"}).status_code == 422
        path = client.get(f"{base}/paths", params={
            "source": f"project:{state['id']}", "target": f"person:{person['id']}",
            "max_depth": 1, "relations": "project_person"})
        assert path.status_code == 200
        assert len(path.json()["paths"]) == 1
        assert client.get(f"{base}/paths", params={
            "source": f"project:{state['id']}", "target": f"person:{person['id']}",
            "max_depth": 4}).status_code == 422


def test_graph_rejects_oversized_legacy_projection():
    class OversizedConnections:
        def for_state(self, state_id):
            return {"project": "Atlas", "state_version": 1, "checked_at": "now",
                    "people": [], "tasks": [], "aliases": [], "person_tasks": [],
                    "documents": [{"id": str(index), "path": f"file-{index}",
                                   "status": "unveraendert", "sha256": "0" * 64}
                                  for index in range(100)]}

    with pytest.raises(ValueError, match="Sicherheitsgrenze"):
        ProjectGraph(OversizedConnections()).snapshot("state")

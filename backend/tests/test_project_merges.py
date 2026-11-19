"""Explizite Personen-Zusammenführung bleibt atomar und sicher trennbar."""

import sqlite3

import pytest
from fastapi.testclient import TestClient
from test_project_people import setup_project

from jarvis.app import create_app
from jarvis.backup import create_backup, restore_backup
from jarvis.config import Settings
from jarvis.db import Database
from jarvis.project_aliases import ProjectAliasStore
from jarvis.project_connections import ProjectConnections
from jarvis.project_merges import ProjectMergeConflict, ProjectMergeStore
from jarvis.project_people import ProjectPeopleStore, ProjectPersonConflict
from jarvis.project_person_tasks import ProjectPersonTaskStore


def test_merge_split_preserve_sources_ids_links_and_restore(tmp_path):
    root, db, _, states, _, _, document, task, state = setup_project(tmp_path)
    people = ProjectPeopleStore(db, states)
    aliases = ProjectAliasStore(db, states, people)
    links = ProjectPersonTaskStore(db, states, people)
    merges = ProjectMergeStore(db, states)
    source = people.add(state["id"], 1, "Mira Weber", "lead", "Protokoll nennt Mira",
                        document["id"], "confirmed", "2026-01-01", "2026-01-02")
    target = people.add(state["id"], 1, "Miriam", "contact", "Eigene Projektangabe", None)
    source_alias = aliases.add(state["id"], 1, source["id"], 1, "M. Weber",
                               "Im Protokoll abgekürzt", document["id"])
    link = links.add(state["id"], 1, source["id"], task["id"], "responsible",
                     "Mira prüft Atlas", document["id"])
    record = merges.merge(state["id"], 1, source["id"], 1, target["id"], 1)
    assert record["source_name"] == "Mira Weber"
    assert record["source_relation_type"] == "lead"
    assert record["moved_alias_count"] == record["moved_link_count"] == 1
    assert [item["id"] for item in people.list_for_state(state["id"])] == [target["id"]]
    current_aliases = aliases.list_for_state(state["id"])
    assert {item["alias"] for item in current_aliases} == {"M. Weber", "Mira Weber"}
    assert all(item["person_id"] == target["id"] for item in current_aliases)
    canonical = next(item for item in current_aliases if item["alias"] == "Mira Weber")
    assert canonical["source_sha256"] == source["source_sha256"]
    assert canonical["confidence"] == "confirmed" and canonical["valid_from"] == "2026-01-01"
    assert canonical["valid_until"] == "2026-01-02"
    assert links.list_for_state(state["id"])[0]["id"] == link["id"]
    assert links.list_for_state(state["id"])[0]["person_id"] == target["id"]
    assert links.list_for_state(state["id"])[0]["status"] == "zusammengefuehrte_person_nicht_gueltig"
    with pytest.raises(ProjectPersonConflict, match="zuerst trennen"):
        people.delete(state["id"], target["id"], 1, 2)
    archive = tmp_path / "profile.jarvis"
    assert create_backup(root, archive)["schema"] == 56
    restored = tmp_path / "restored"
    assert restore_backup(archive, restored)["schema"] == 56
    restored_db = Database(restored / "jarvis.db")
    with restored_db.connect() as connection:
        assert connection.execute("SELECT count(*) FROM project_person_merges").fetchone()[0] == 1
    split = merges.split(state["id"], record["id"], 1, 2)
    assert split["split_at"] is not None
    restored_source = next(item for item in people.list_for_state(state["id"])
                           if item["id"] == source["id"])
    assert restored_source["source_sha256"] == source["source_sha256"]
    assert restored_source["version"] == 2
    assert aliases.list_for_state(state["id"])[0]["id"] == source_alias["id"]
    assert aliases.list_for_state(state["id"])[0]["person_id"] == source["id"]
    assert links.list_for_state(state["id"])[0]["id"] == link["id"]
    assert links.list_for_state(state["id"])[0]["person_id"] == source["id"]
    with pytest.raises(ProjectMergeConflict):
        merges.split(state["id"], record["id"], 1, 3)


def test_merge_rejects_collisions_limits_and_later_edits_without_partial_change(tmp_path):
    _, db, _, states, _, _, _, task, state = setup_project(tmp_path)
    people = ProjectPeopleStore(db, states)
    aliases = ProjectAliasStore(db, states, people)
    links = ProjectPersonTaskStore(db, states, people)
    merges = ProjectMergeStore(db, states)
    source = people.add(state["id"], 1, "Mira", "lead", "Eigene Aussage", None)
    target = people.add(state["id"], 1, "Miriam", "contact", "Eigene Aussage", None)
    other = states.create("Beta", "", "", "Weiter", [], [], [])
    foreign = people.add(other["id"], 1, "Fremde", "contact", "Eigene Aussage", None)
    with pytest.raises(ValueError, match="demselben Projekt"):
        merges.merge(state["id"], 1, source["id"], 1, foreign["id"], 1)
    with pytest.raises(ProjectMergeConflict):
        merges.merge(state["id"], 1, source["id"], 9, target["id"], 1)
    first = links.add(state["id"], 1, source["id"], task["id"], "reviewer",
                      "Mira prüft", None)
    duplicate = links.add(state["id"], 1, target["id"], task["id"], "reviewer",
                          "Miriam prüft", None)
    with pytest.raises(ValueError, match="dieselbe Aufgabenbeziehung"):
        merges.merge(state["id"], 1, source["id"], 1, target["id"], 1)
    assert len(people.list_for_state(state["id"])) == 2
    assert aliases.list_for_state(state["id"]) == []
    links.delete(state["id"], duplicate["id"], 1, 1)
    for index in range(5):
        aliases.add(state["id"], 1, target["id"], 1, f"Ziel-{index}",
                    "Eigene Aliasangabe", None)
    with pytest.raises(ValueError, match="Aliasgrenze"):
        merges.merge(state["id"], 1, source["id"], 1, target["id"], 1)
    for item in aliases.list_for_state(state["id"]):
        aliases.delete(state["id"], item["id"], 1, item["version"])
    moved = aliases.add(state["id"], 1, source["id"], 1, "M.", "Eigene Abkürzung", None)
    record = merges.merge(state["id"], 1, source["id"], 1, target["id"], 1)
    third = people.add(state["id"], 1, "Mina", "contact", "Eigene Aussage", None)
    with pytest.raises(ProjectMergeConflict, match="aktiven Zusammenführung"):
        merges.merge(state["id"], 1, target["id"], 2, third["id"], 1)
    aliases.update(state["id"], moved["id"], 1, target["id"], 2, 2,
                   "Mira alt", "Korrigierte Angabe", None)
    with pytest.raises(ProjectMergeConflict, match="übertragene Beziehung"):
        merges.split(state["id"], record["id"], 1, 2)
    assert {person["id"] for person in people.list_for_state(state["id"])} == {target["id"], third["id"]}
    assert links.list_for_state(state["id"])[0]["id"] == first["id"]


def test_merge_api_csrf_and_schema45_migration(tmp_path):
    root, db, _, states, _, _, _, _, state = setup_project(tmp_path)
    people = ProjectPeopleStore(db, states)
    source = people.add(state["id"], 1, "Mira", "lead", "Eigene Aussage", None)
    target = people.add(state["id"], 1, "Miriam", "contact", "Eigene Aussage", None)
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=root))) as client:
        csrf = {"x-jarvis-csrf": client.get("/session").json()["csrf"]}
        url = f"/work-states/{state['id']}/person-merges"
        body = {"expected_state_version": 1, "source_person_id": source["id"],
                "expected_source_version": 1, "target_person_id": target["id"],
                "expected_target_version": 1}
        assert client.post(url, json=body).status_code == 403
        assert client.post(url, headers=csrf, json={**body,
            "expected_state_version": 9}).status_code == 409
        assert client.post(url, headers=csrf, json={**body,
            "target_person_id": source["id"]}).status_code == 422
        response = client.post(url, headers=csrf, json=body)
        assert response.status_code == 200, response.text
        record = response.json()
        assert len(client.get(url).json()) == 1
        assert client.post(url, headers=csrf, json=body).status_code in (409, 422)
        split_url = f"{url}/{record['id']}/split"
        assert client.post(split_url, json={"expected_state_version": 1,
            "expected_target_version": 2}).status_code == 403
        assert client.post(split_url, headers=csrf, json={"expected_state_version": 1,
            "expected_target_version": 1}).status_code == 409
        assert client.post(split_url, headers=csrf, json={"expected_state_version": 1,
            "expected_target_version": 2}).status_code == 200
        assert client.get("/work-states/missing/person-merges").status_code == 404
    with db.connect() as connection:
        connection.execute("DROP TABLE draft_named_style_usage")
        connection.execute("DROP TABLE named_style_profile_revisions")
        connection.execute("DROP TABLE named_style_profiles")
        connection.execute("DROP TABLE project_memory_links")
        connection.execute("DROP TABLE project_person_merges")
        connection.execute("DROP TABLE learning_sessions")
        connection.execute("DROP TABLE memory_proposal_events")
        connection.execute("DROP TABLE memory_proposals")
        connection.execute("DROP TABLE memory_context_uses")
        connection.execute("PRAGMA user_version=45")
    migrated = Database(db.path)
    with sqlite3.connect(migrated.path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 56
        assert connection.execute("SELECT count(*) FROM project_person_merges").fetchone()[0] == 0


def test_transferred_task_keeps_original_person_source_warning(tmp_path):
    _, db, _, states, source_file, _, document, task, state = setup_project(tmp_path)
    people = ProjectPeopleStore(db, states)
    links = ProjectPersonTaskStore(db, states, people)
    merges = ProjectMergeStore(db, states)
    source = people.add(state["id"], 1, "Mira", "lead", "Protokoll nennt Mira",
                        document["id"])
    target = people.add(state["id"], 1, "Miriam", "contact", "Eigene Aussage", None)
    link = links.add(state["id"], 1, source["id"], task["id"], "responsible",
                     "Eigene Aufgabenangabe", None)
    source_file.write_text("Protokoll hat sich geändert", encoding="utf-8")
    merges.merge(state["id"], 1, source["id"], 1, target["id"], 1)
    transferred = links.list_for_state(state["id"])[0]
    assert transferred["id"] == link["id"]
    assert transferred["person_id"] == target["id"]
    assert transferred["merged_from_person_name"] == "Mira"
    assert transferred["merged_from_source_status"] == "geaendert"
    assert transferred["status"] == "zusammengefuehrter_person_beleg_pruefen"
    query = ProjectConnections(states, people, links, ProjectAliasStore(db, states, people))
    assert query.for_state(state["id"])["person_tasks"][0]["status"] == (
        "zusammengefuehrter_person_beleg_pruefen")


def test_query_rejects_merge_between_its_read_steps(tmp_path, monkeypatch):
    _, db, _, states, _, _, _, _, state = setup_project(tmp_path)
    people = ProjectPeopleStore(db, states)
    aliases = ProjectAliasStore(db, states, people)
    links = ProjectPersonTaskStore(db, states, people)
    merges = ProjectMergeStore(db, states)
    source = people.add(state["id"], 1, "Mira", "lead", "Eigene Aussage", None)
    target = people.add(state["id"], 1, "Miriam", "contact", "Eigene Aussage", None)
    query = ProjectConnections(states, people, links, aliases)
    original = links.list_for_state

    def merge_while_reading(*args, **kwargs):
        merges.merge(state["id"], 1, source["id"], 1, target["id"], 1)
        return original(*args, **kwargs)

    monkeypatch.setattr(links, "list_for_state", merge_while_reading)
    with pytest.raises(RuntimeError, match="während der Abfrage"):
        query.for_state(state["id"])


def test_merge_history_is_bounded(tmp_path):
    _, db, _, states, _, _, _, _, state = setup_project(tmp_path)
    people = ProjectPeopleStore(db, states)
    source = people.add(state["id"], 1, "Mira", "lead", "Eigene Aussage", None)
    target = people.add(state["id"], 1, "Miriam", "contact", "Eigene Aussage", None)
    with db.connect() as connection:
        connection.executemany("""INSERT INTO project_person_merges
            (id,state_id,source_person_id,target_person_id,source_snapshot_json,
             moved_aliases_json,moved_links_json,canonical_alias_id,target_version_after,
             created_at,split_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""", [
                (f"old-{index}", state["id"], "former", target["id"],
                 '{"name":"Former"}', "[]", "[]", f"alias-{index}", 2,
                 "2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z")
                for index in range(100)])
    with pytest.raises(ValueError, match="Höchstens 100"):
        ProjectMergeStore(db, states).merge(state["id"], 1, source["id"], 1,
                                            target["id"], 1)
    assert len(people.list_for_state(state["id"])) == 2


def test_merge_rolls_back_if_legacy_duplicate_alias_would_be_created(tmp_path):
    _, db, _, states, _, _, _, task, state = setup_project(tmp_path)
    people = ProjectPeopleStore(db, states)
    links = ProjectPersonTaskStore(db, states, people)
    source = people.add(state["id"], 1, "Mira", "lead", "Eigene Aussage", None)
    target = people.add(state["id"], 1, "Miriam", "contact", "Eigene Aussage", None)
    links.add(state["id"], 1, source["id"], task["id"], "reviewer",
              "Eigene Aufgabenangabe", None)
    # Ein Altimport könnte die heute gültige Namensprüfung umgangen haben.
    with db.connect() as connection:
        connection.execute("""INSERT INTO project_person_aliases
            (id,state_id,person_id,alias,source_note,confidence,version,created_at,updated_at)
            VALUES(?,?,?,'Mira','Altimport','unverified',1,'2026-01-01','2026-01-01')""",
            ("legacy-alias", state["id"], target["id"]))
    with pytest.raises(ValueError, match="bereits"):
        ProjectMergeStore(db, states).merge(state["id"], 1, source["id"], 1,
                                            target["id"], 1)
    assert {row["id"] for row in people.list_for_state(state["id"])} == {source["id"], target["id"]}
    assert links.list_for_state(state["id"])[0]["person_id"] == source["id"]
    with db.connect() as connection:
        assert connection.execute("SELECT count(*) FROM project_person_merges").fetchone()[0] == 0

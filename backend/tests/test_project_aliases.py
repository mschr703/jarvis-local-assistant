"""Projekt-Aliase haben eigene Herkunft, Gültigkeit und Dublettenschutz."""

import sqlite3

from fastapi.testclient import TestClient
from test_project_people import setup_project

from jarvis.app import create_app
from jarvis.backup import create_backup, restore_backup
from jarvis.config import Settings
from jarvis.db import Database
from jarvis.documents import DocumentStore
from jarvis.project_aliases import ProjectAliasStore
from jarvis.project_people import ProjectPeopleStore
from jarvis.tasks import TaskStore
from jarvis.work_states import WorkStateStore


def test_alias_sources_duplicates_and_person_cascade(tmp_path):
    root, db, documents, states, source, folder, document, _, state = setup_project(tmp_path)
    people = ProjectPeopleStore(db, states)
    aliases = ProjectAliasStore(db, states, people)
    mira = people.add(state["id"], 1, "Mira Weber", "lead", "Protokoll nennt Mira",
                      document["id"])
    noah = people.add(state["id"], 1, "Noah", "contact", "Eigene Projektangabe", None)
    other = states.create("Beta", "", "", "Weiter", [], [], [])
    alias = aliases.add(state["id"], 1, mira["id"], 1, "M. Weber",
                        "Im Protokoll abgekürzt", document["id"],
                        "plausible", "2026-01-01", None)
    assert alias["person_name"] == "Mira Weber"
    assert alias["source_status"] == "unveraendert"
    assert alias["source_sha256"] == state["documents"][0]["sha256"]
    assert alias["confidence"] == "plausible"
    assert aliases.list_for_state(other["id"]) == []
    for owner, name in ((mira, "m. weber"), (noah, "M. WEBER"), (noah, "Mira Weber")):
        try:
            aliases.add(state["id"], 1, owner["id"], 1, name,
                        "Eigene Aliasangabe", None)
        except ValueError as exc:
            assert "bereits" in str(exc) or "Alias" in str(exc)
        else:
            raise AssertionError("Doppelter Alias wurde angenommen")
    try:
        people.add(state["id"], 1, "M. Weber", "contact", "Eigene Person", None)
    except ValueError as exc:
        assert "Alias" in str(exc)
    else:
        raise AssertionError("Alias wurde als neuer kanonischer Name angenommen")
    source.write_text("Mira wird jetzt anders benannt", encoding="utf-8")
    assert aliases.list_for_state(state["id"])[0]["source_status"] == "geaendert"
    documents.index_folder(folder["id"])
    current = states.confirm_document(state["id"], document["id"], 1)
    assert aliases.list_for_state(state["id"])[0]["source_status"] == "quelle_erneuert"
    revised = aliases.update(state["id"], alias["id"], current["version"], mira["id"],
                             mira["version"], 1, "Miriam", "Neu selbst bestätigt", None,
                             "confirmed", None, None)
    assert revised["version"] == 2 and revised["source_status"] == "eigene_angabe"
    archive = tmp_path / "profile.jarvis"
    assert create_backup(root, archive)["schema"] == 55
    restored = tmp_path / "restored"
    assert restore_backup(archive, restored)["schema"] == 55
    with Database(restored / "jarvis.db").connect() as connection:
        assert connection.execute("SELECT alias FROM project_person_aliases WHERE id=?",
                                  (alias["id"],)).fetchone()[0] == "Miriam"
    people.delete(state["id"], mira["id"], current["version"], mira["version"])
    assert aliases.list_for_state(state["id"]) == []
    with db.connect() as connection:
        assert connection.execute("SELECT count(*) FROM project_person_aliases").fetchone()[0] == 0


def test_alias_api_csrf_conflicts_query_and_limits(tmp_path):
    root, db, _, states, _, _, document, _, state = setup_project(tmp_path)
    people = ProjectPeopleStore(db, states)
    mira = people.add(state["id"], 1, "Mira", "lead", "Protokoll nennt Mira",
                      document["id"])
    noah = people.add(state["id"], 1, "Noah", "contact", "Eigene Projektangabe", None)
    other = states.create("Beta", "", "", "Weiter", [], [], [])
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=root))) as client:
        csrf = {"x-jarvis-csrf": client.get("/session").json()["csrf"]}
        url = f"/work-states/{state['id']}/aliases"
        body = {"expected_state_version": 1, "person_id": mira["id"],
                "expected_person_version": 1, "alias": "M. Weber",
                "source_note": "Protokoll nennt Alias", "source_document_id": document["id"],
                "confidence": "confirmed", "valid_from": None, "valid_until": None}
        assert client.post(url, json=body).status_code == 403
        assert client.post(url, headers=csrf, json={**body,
            "expected_person_version": 3}).status_code == 409
        assert client.post(url, headers=csrf, json={**body,
            "source_document_id": "fremd"}).status_code == 422
        assert client.post(f"/work-states/{other['id']}/aliases",
                           headers=csrf, json=body).status_code == 422
        response = client.post(url, headers=csrf, json=body)
        assert response.status_code == 200, response.text
        alias = response.json()
        assert alias["source_status"] == "unveraendert"
        assert client.get(f"/work-states/{state['id']}/connections").json()[
            "aliases"][0]["id"] == alias["id"]
        assert client.get(f"/work-states/{other['id']}/connections").json()["aliases"] == []
        assert client.get("/work-states/missing/aliases").status_code == 404
        detail = f"{url}/{alias['id']}"
        assert client.put(detail, headers=csrf, json={**body,
            "expected_version": 2}).status_code == 409
        assert client.put(detail, headers=csrf, json={**body,
            "expected_version": 1, "person_id": noah["id"]}).status_code == 422
        updated = client.put(detail, headers=csrf, json={**body,
            "expected_version": 1, "alias": "Mirjam"})
        assert updated.status_code == 200 and updated.json()["version"] == 2
        assert client.delete(detail, headers=csrf, params={
            "expected_state_version": 1, "expected_version": 1}).status_code == 409
        assert client.delete(detail, headers=csrf, params={
            "expected_state_version": 1, "expected_version": 2}).status_code == 200
        assert client.get(url).json() == []
    aliases = ProjectAliasStore(db, states, people)
    for index in range(5):
        aliases.add(state["id"], 1, mira["id"], 1, f"Mira {index}",
                    "Eigene Aliasangabe", None)
    try:
        aliases.add(state["id"], 1, mira["id"], 1, "Mira extra",
                    "Eigene Aliasangabe", None)
    except ValueError as exc:
        assert "Aliasgrenze" in str(exc)
    else:
        raise AssertionError("Mehr als fünf Aliase pro Person wurden angenommen")
    for owner_index in range(5):
        owner = people.add(state["id"], 1, f"Projektperson {owner_index}", "contact",
                           "Eigene Projektangabe", None)
        for alias_index in range(5):
            aliases.add(state["id"], 1, owner["id"], 1,
                        f"Kürzel {owner_index}-{alias_index}", "Eigene Aliasangabe", None)
    assert len(aliases.list_for_state(state["id"])) == 30
    try:
        aliases.add(state["id"], 1, noah["id"], 1, "Noahs Kürzel",
                    "Eigene Aliasangabe", None)
    except ValueError as exc:
        assert "Aliasgrenze" in str(exc)
    else:
        raise AssertionError("Mehr als 30 Aliase pro Projekt wurden angenommen")


def test_schema44_migrates_alias_table_without_changing_people(tmp_path):
    db = Database(tmp_path / "jarvis.db")
    states = WorkStateStore(db, DocumentStore(db), TaskStore(db))
    state = states.create("Atlas", "", "", "Weiter", [], [], [])
    person = ProjectPeopleStore(db, states).add(state["id"], 1, "Mira", "lead",
                                                "Eigene Angabe", None)
    with db.connect() as connection:
        connection.execute("DROP TABLE draft_named_style_usage")
        connection.execute("DROP TABLE named_style_profile_revisions")
        connection.execute("DROP TABLE named_style_profiles")
        connection.execute("DROP TABLE project_memory_links")
        connection.execute("DROP TABLE project_person_merges")
        connection.execute("DROP TABLE project_person_aliases")
        connection.execute("DROP TABLE learning_sessions")
        connection.execute("DROP TABLE memory_proposal_events")
        connection.execute("DROP TABLE memory_proposals")
        connection.execute("DROP TABLE memory_context_uses")
        connection.execute("PRAGMA user_version=44")
    migrated = Database(db.path)
    assert ProjectPeopleStore(migrated, WorkStateStore(
        migrated, DocumentStore(migrated), TaskStore(migrated))).list_for_state(state["id"])[0][
            "id"] == person["id"]
    with sqlite3.connect(migrated.path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 55
        assert connection.execute("SELECT count(*) FROM project_person_aliases").fetchone()[0] == 0

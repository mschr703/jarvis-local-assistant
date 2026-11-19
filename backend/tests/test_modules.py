import json
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from jarvis.app import create_app
from jarvis.backup import create_backup, restore_backup
from jarvis.config import Settings
from jarvis.db import Database

SAMPLE_MODULE = '''
from fastapi import FastAPI
from pydantic import BaseModel
from jarvis.engine import ToolDefinition

class Entry(BaseModel):
    value: str

class ListArgs(BaseModel):
    pass

def migrate(connection, from_version):
    if from_version == 0:
        connection.execute("CREATE TABLE fixture_module(value TEXT NOT NULL)")

def create_app(db):
    app = FastAPI()
    @app.get("/entries")
    def entries():
        with db.connect() as connection:
            return [row[0] for row in connection.execute("SELECT value FROM fixture_module")]
    @app.post("/entries")
    def add(entry: Entry):
        with db.connect() as connection:
            connection.execute("INSERT INTO fixture_module(value) VALUES(?)", (entry.value,))
        return {"value": entry.value}
    return app

def tool_definitions(db):
    def list_entries(_args):
        with db.connect() as connection:
            return {"entries": [row[0] for row in connection.execute(
                "SELECT value FROM fixture_module")]}
    return [ToolDefinition("list_entries", ListArgs, list_entries,
                           scope="local_module", effect="read")]
'''


def install_fixture(data_dir, module_id="sample_notes", dependencies=None):
    folder = data_dir / "modules" / module_id
    folder.mkdir(parents=True)
    (folder / "jarvis-module.json").write_text(json.dumps({
        "id": module_id, "name": "Sample", "version": "0.1.0", "api": 1,
        "data_version": 1,
        "navigation": "Sample", "view": "web_component_v1", "element": "jarvis-sample",
        "permissions": ["local_data"],
        "dependencies": dependencies or [],
    }), encoding="utf-8")
    (folder / "module.py").write_text(SAMPLE_MODULE, encoding="utf-8")
    (folder / "ui.js").write_text("customElements.define('jarvis-sample', class extends HTMLElement {})",
                                  encoding="utf-8")


def client_for(data_dir):
    return TestClient(create_app(Settings(JARVIS_DATA_DIR=data_dir,
                                          JARVIS_OLLAMA_MODEL="fixture")))


def csrf(client):
    return {"x-jarvis-csrf": client.get("/session").json()["csrf"]}


def test_module_lifecycle_and_backup(tmp_path):
    data_dir = tmp_path / "data"
    with client_for(data_dir) as client:
        headers = csrf(client)
        assert client.get("/modules").json() == []
        assert client.get("/modules/sample_notes/entries").status_code == 404
        install_fixture(data_dir)
        assert client.get("/modules").json()[0]["enabled"] is False
        assert all(tool["name"] != "sample_notes__list_entries" for tool in client.get("/tools").json())
        assert client.post("/modules/sample_notes/enable").status_code == 403
        assert client.post("/modules/sample_notes/enable", headers=headers).status_code == 200
        assert client.get("/modules").json()[0]["enabled"] is True
        assert any(tool["name"] == "sample_notes__list_entries" for tool in client.get("/tools").json())
        assert client.get("/modules/sample_notes/ui.js").status_code == 200
        assert client.post("/modules/sample_notes/entries", headers=headers,
                           json={"value": "first"}).status_code == 200
        assert client.get("/modules/sample_notes/entries").json() == ["first"]
        assert client.post("/runs", headers=headers,
                           json={"tool_name": "sample_notes__list_entries", "args": {}}).json()[
                               "status"] == "succeeded"
        assert client.post("/modules/sample_notes/disable", headers=headers).status_code == 200
        assert client.get("/modules/sample_notes/entries").status_code == 404
        assert client.get("/modules/sample_notes/ui.js").status_code == 404
        assert all(tool["name"] != "sample_notes__list_entries" for tool in client.get("/tools").json())
        assert client.post("/runs", headers=headers,
                           json={"tool_name": "sample_notes__list_entries", "args": {}}).status_code == 422
        assert client.post("/modules/sample_notes/entries", headers=headers,
                           json={"value": "blocked"}).status_code == 404
    archive = tmp_path / "backup.zip"
    create_backup(data_dir, archive)
    restored = tmp_path / "restored"
    restore_backup(archive, restored)
    assert not (restored / "modules").exists()  # Code is reinstalled from a trusted source.
    install_fixture(restored)
    with client_for(restored) as client:
        headers = csrf(client)
        assert client.get("/modules").json()[0]["enabled"] is False
        assert client.post("/modules/sample_notes/enable", headers=headers).status_code == 200
        assert client.get("/modules/sample_notes/entries").json() == ["first"]
    assert Database(restored / "jarvis.db").get_settings()["module.sample_notes.enabled"] == "1"
    with client_for(restored) as client:
        csrf(client)
        assert client.get("/modules").json()[0]["enabled"] is True
        assert client.get("/modules/sample_notes/entries").json() == ["first"]


def test_module_bad_package_does_not_break_core_and_dependency_is_enforced(tmp_path):
    data_dir = tmp_path / "data"
    install_fixture(data_dir, "requires_other", ["other_module"])
    bad = data_dir / "modules" / "bad_module"
    bad.mkdir()
    (bad / "jarvis-module.json").write_text('{"id":"wrong"}', encoding="utf-8")
    (bad / "module.py").write_text("raise RuntimeError('bad')", encoding="utf-8")
    with client_for(data_dir) as client:
        headers = csrf(client)
        items = client.get("/modules").json()
        assert [item["id"] for item in items] == ["requires_other", "bad_module"]
        assert items[1]["error"]
        assert client.post("/modules/requires_other/enable", headers=headers).status_code == 422
        assert client.get("/chats").status_code == 200
        assert client.get("/modules/bad_module/entries").status_code == 404


def test_broken_saved_module_does_not_break_core(tmp_path):
    data_dir = tmp_path / "data"
    install_fixture(data_dir)
    (data_dir / "modules" / "sample_notes" / "module.py").write_text(
        "raise RuntimeError('broken optional module')", encoding="utf-8")
    Database(data_dir / "jarvis.db").set_settings({"module.sample_notes.enabled": "1"})
    with client_for(data_dir) as client:
        csrf(client)
        item = client.get("/modules").json()[0]
        assert item["enabled"] is False and item["requested_enabled"] is True
        assert "RuntimeError" in item["error"]
        assert client.get("/chats").status_code == 200


def test_module_migration_rolls_back_and_data_downgrade_is_rejected(tmp_path):
    data_dir = tmp_path / "data"
    install_fixture(data_dir)
    source = data_dir / "modules" / "sample_notes" / "module.py"
    source.write_text("""
def migrate(connection, from_version):
    connection.execute('CREATE TABLE partial_module_data(value TEXT)')
    raise RuntimeError('migration failed')
def create_app(db):
    from fastapi import FastAPI
    return FastAPI()
""", encoding="utf-8")
    with client_for(data_dir) as client:
        headers = csrf(client)
        assert client.post("/modules/sample_notes/enable", headers=headers).status_code == 409
        assert client.get("/chats").status_code == 200
    db = Database(data_dir / "jarvis.db")
    assert "module.sample_notes.data_version" not in db.get_settings()
    with db.connect() as connection:
        assert connection.execute("SELECT name FROM sqlite_master WHERE name='partial_module_data'").fetchone() is None
    source.write_text(SAMPLE_MODULE, encoding="utf-8")
    db.set_settings({"module.sample_notes.data_version": "2"})
    with client_for(data_dir) as client:
        headers = csrf(client)
        assert client.post("/modules/sample_notes/enable", headers=headers).status_code == 409
        assert "älter" in client.get("/modules").json()[0]["error"]


def test_module_package_install_and_remove_keeps_database(tmp_path):
    source_root = tmp_path / "source"
    install_fixture(source_root)
    source = source_root / "modules" / "sample_notes"
    data_dir = tmp_path / "data"
    script = Path(__file__).resolve().parents[2] / "scripts" / "module_package.py"
    command = [sys.executable, str(script)]
    subprocess.run([*command, "install", str(source), "--data-dir", str(data_dir)], check=True,
                   capture_output=True, text=True)
    with client_for(data_dir) as client:
        headers = csrf(client)
        assert client.post("/modules/sample_notes/enable", headers=headers).status_code == 200
        assert client.post("/modules/sample_notes/entries", headers=headers,
                           json={"value": "keep"}).status_code == 200
        assert client.post("/modules/sample_notes/disable", headers=headers).status_code == 200
    subprocess.run([*command, "remove", "sample_notes", "--data-dir", str(data_dir)],
                   check=True, capture_output=True, text=True)
    assert not (data_dir / "modules" / "sample_notes").exists()
    with Database(data_dir / "jarvis.db").connect() as connection:
        assert connection.execute("SELECT value FROM fixture_module").fetchone()[0] == "keep"

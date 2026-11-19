import json
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from jarvis.app import create_app
from jarvis.backup import create_backup, restore_backup
from jarvis.config import Settings
from jarvis.db import Database
from jarvis.modules import package_digest

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
        connection.execute("CREATE TABLE IF NOT EXISTS fixture_module(value TEXT NOT NULL)")

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
    element = f"jarvis-{module_id.replace('__', '-x-').replace('_', '-')}"
    (folder / "jarvis-module.json").write_text(json.dumps({
        "id": module_id, "name": "Sample", "version": "0.1.0", "api": 1,
        "data_version": 1,
        "navigation": "Sample", "view": "web_component_v1", "element": element,
        "permissions": ["local_data"],
        "dependencies": dependencies or [],
    }), encoding="utf-8")
    (folder / "module.py").write_text(SAMPLE_MODULE, encoding="utf-8")
    (folder / "ui.js").write_text(f"customElements.define('{element}', class extends HTMLElement {{}})",
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


def test_missing_saved_package_is_visible_and_can_be_reinstalled(tmp_path):
    data_dir = tmp_path / "data"
    source_root = tmp_path / "source"
    install_fixture(source_root)
    source = source_root / "modules" / "sample_notes"
    Database(data_dir / "jarvis.db").set_settings({"module.sample_notes.enabled": "1"})
    with client_for(data_dir) as client:
        headers = csrf(client)
        missing = client.get("/modules").json()
        assert len(missing) == 1
        assert missing[0]["id"] == "sample_notes"
        assert missing[0]["requested_enabled"] is True
        assert missing[0]["enabled"] is False
        assert "Modulpaket fehlt" in missing[0]["error"]
        assert client.get("/chats").status_code == 200
        installed = client.post("/modules/install", headers=headers,
                                json={"path": str(source)})
        assert installed.status_code == 200, installed.text
        restored = client.get("/modules").json()[0]
        assert restored["api"] == 1 and restored["error"] is None
        assert restored["requested_enabled"] is True and restored["enabled"] is False
        assert client.post("/modules/sample_notes/enable", headers=headers).status_code == 200
        assert client.get("/modules/sample_notes/entries").status_code == 200


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
    db.set_settings({"module.sample_notes.package_sha256": package_digest(source.parent)})
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


def test_saved_dependencies_start_in_order_and_deactivate_when_missing(tmp_path):
    data_dir = tmp_path / "data"
    install_fixture(data_dir, "a_child", ["z_parent"])
    install_fixture(data_dir, "z_parent")
    Database(data_dir / "jarvis.db").set_settings({
        "module.a_child.enabled": "1", "module.z_parent.enabled": "1"})
    with client_for(data_dir) as client:
        headers = csrf(client)
        assert all(item["enabled"] for item in client.get("/modules").json())
        assert client.post("/modules/z_parent/disable", headers=headers).status_code == 422
        (data_dir / "modules" / "z_parent" / "jarvis-module.json").unlink()
        items = client.get("/modules").json()
        child = next(item for item in items if item["id"] == "a_child")
        assert child["enabled"] is False
        assert "nicht mehr aktiv" in child["error"]
        assert client.get("/modules/a_child/entries").status_code == 404


def test_cyclic_saved_dependencies_leave_core_available(tmp_path):
    data_dir = tmp_path / "data"
    install_fixture(data_dir, "cycle_one", ["cycle_two"])
    install_fixture(data_dir, "cycle_two", ["cycle_one"])
    Database(data_dir / "jarvis.db").set_settings({
        "module.cycle_one.enabled": "1", "module.cycle_two.enabled": "1"})
    with client_for(data_dir) as client:
        csrf(client)
        items = client.get("/modules").json()
        assert all(not item["enabled"] and "Zyklische" in item["error"] for item in items)
        assert client.get("/chats").status_code == 200


def test_modules_cannot_claim_the_same_browser_element(tmp_path):
    data_dir = tmp_path / "data"
    install_fixture(data_dir, "first_mod")
    install_fixture(data_dir, "second_mod")
    second_manifest = data_dir / "modules" / "second_mod" / "jarvis-module.json"
    parsed = json.loads(second_manifest.read_text(encoding="utf-8"))
    parsed["element"] = "jarvis-first-mod"
    second_manifest.write_text(json.dumps(parsed), encoding="utf-8")
    Database(data_dir / "jarvis.db").set_settings({
        "module.first_mod.enabled": "1", "module.second_mod.enabled": "1"})
    with client_for(data_dir) as client:
        headers = csrf(client)
        response = client.get("/modules")
        assert response.status_code == 200, response.text
        items = {item["id"]: item for item in response.json()}
        assert items["first_mod"]["enabled"] is True
        assert items["second_mod"]["enabled"] is False
        assert "first_mod" in items["second_mod"]["error"]
        assert client.get("/chats").status_code == 200
        assert client.post("/modules/second_mod/enable", headers=headers).status_code == 422


def test_module_tool_names_with_double_underscore_stay_separate(tmp_path):
    data_dir = tmp_path / "data"
    install_fixture(data_dir, "foo")
    install_fixture(data_dir, "foo__bar")
    with client_for(data_dir) as client:
        headers = csrf(client)
        assert client.post("/modules/foo/enable", headers=headers).status_code == 200
        assert client.post("/modules/foo__bar/enable", headers=headers).status_code == 200
        assert client.post("/modules/foo/disable", headers=headers).status_code == 200
        tools = {item["name"] for item in client.get("/tools").json()}
        assert "foo__list_entries" not in tools
        assert "foo__bar__list_entries" in tools
        result = client.post("/runs", headers=headers,
                             json={"tool_name": "foo__bar__list_entries", "args": {}})
        assert result.status_code == 200 and result.json()["status"] == "succeeded"


def test_invalid_module_tool_does_not_leave_partial_registration(tmp_path):
    source_root = tmp_path / "source"
    install_fixture(source_root)
    source = source_root / "modules" / "sample_notes"
    entry = source / "module.py"
    invalid = SAMPLE_MODULE.replace(
        'scope="local_module", effect="read")]',
        'scope="local_module", effect="read"),\n'
        '            ToolDefinition("bad_tool", ListArgs, list_entries,\n'
        '                           scope="local_module", effect="read", timeout_s=0)]')
    assert invalid != SAMPLE_MODULE
    entry.write_text(invalid, encoding="utf-8")
    data_dir = tmp_path / "data"
    with client_for(data_dir) as client:
        headers = csrf(client)
        assert client.post("/modules/install", headers=headers,
                           json={"path": str(source)}).status_code == 200
        assert client.post("/modules/sample_notes/enable", headers=headers).status_code == 409
        assert "sample_notes__list_entries" not in client.app.state.engine.definitions
        assert "sample_notes__bad_tool" not in client.app.state.engine.definitions
        assert client.get("/chats").status_code == 200
        assert client.post("/modules/sample_notes/remove", headers=headers).status_code == 200
        entry.write_text(SAMPLE_MODULE, encoding="utf-8")
        assert client.post("/modules/install", headers=headers,
                           json={"path": str(source)}).status_code == 200
        assert client.post("/modules/sample_notes/enable", headers=headers).status_code == 200
        assert "sample_notes__list_entries" in client.app.state.engine.definitions


def test_module_registration_rolls_back_if_a_later_tool_collides(tmp_path, monkeypatch):
    source_root = tmp_path / "source"
    install_fixture(source_root)
    source = source_root / "modules" / "sample_notes"
    entry = source / "module.py"
    two_tools = SAMPLE_MODULE.replace(
        'scope="local_module", effect="read")]',
        'scope="local_module", effect="read"),\n'
        '            ToolDefinition("other_tool", ListArgs, list_entries,\n'
        '                           scope="local_module", effect="read")]')
    assert two_tools != SAMPLE_MODULE
    entry.write_text(two_tools, encoding="utf-8")
    with client_for(tmp_path / "data") as client:
        headers = csrf(client)
        assert client.post("/modules/install", headers=headers,
                           json={"path": str(source)}).status_code == 200
        engine = client.app.state.engine
        register = engine.register

        def reject_second(tool):
            if tool.name == "sample_notes__other_tool":
                raise ValueError("Werkzeugname belegt")
            return register(tool)

        monkeypatch.setattr(engine, "register", reject_second)
        assert client.post("/modules/sample_notes/enable", headers=headers).status_code == 409
        assert "sample_notes__list_entries" not in engine.definitions
        monkeypatch.setattr(engine, "register", register)
        assert client.post("/modules/sample_notes/enable", headers=headers).status_code == 200
        assert "sample_notes__list_entries" in engine.definitions
        assert "sample_notes__other_tool" in engine.definitions


def test_module_can_be_installed_and_removed_through_local_api(tmp_path):
    source_root = tmp_path / "source"
    install_fixture(source_root)
    source = source_root / "modules" / "sample_notes"
    data_dir = tmp_path / "data"
    with client_for(data_dir) as client:
        headers = csrf(client)
        assert client.post("/modules/install", json={"path": str(source)}).status_code == 403
        assert client.post("/modules/install", headers=headers,
                           json={"path": "relative/path"}).status_code == 422
        installed = client.post("/modules/install", headers=headers,
                                json={"path": str(source)})
        assert installed.status_code == 200 and installed.json()["id"] == "sample_notes"
        assert client.post("/modules/sample_notes/enable", headers=headers).status_code == 200
        assert client.post("/modules/sample_notes/remove", headers=headers).status_code == 422
        assert client.post("/modules/sample_notes/disable", headers=headers).status_code == 200
        removed = client.post("/modules/sample_notes/remove", headers=headers)
        assert removed.status_code == 200 and removed.json()["data_retained"] is True
        assert client.get("/modules").json() == []


def test_saved_module_system_exit_does_not_stop_core(tmp_path):
    data_dir = tmp_path / "data"
    install_fixture(data_dir)
    (data_dir / "modules" / "sample_notes" / "module.py").write_text(
        "raise SystemExit('optional module stopped')", encoding="utf-8")
    Database(data_dir / "jarvis.db").set_settings({"module.sample_notes.enabled": "1"})
    with client_for(data_dir) as client:
        csrf(client)
        item = client.get("/modules").json()[0]
        assert item["enabled"] is False and "SystemExit" in item["error"]
        assert client.get("/chats").status_code == 200


def test_unreadable_module_directory_keeps_core_running(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    (data_dir / "modules").mkdir(parents=True)
    original = Path.iterdir

    def unreadable(path):
        if path == data_dir / "modules":
            raise PermissionError("unreadable fixture")
        return original(path)

    monkeypatch.setattr(Path, "iterdir", unreadable)
    with client_for(data_dir) as client:
        csrf(client)
        item = client.get("/modules").json()[0]
        assert item["id"] == "_directory" and "nicht lesbar" in item["error"]
        assert client.get("/chats").status_code == 200


def test_modified_installed_package_is_blocked_until_reinstalled(tmp_path):
    source_root = tmp_path / "source"
    install_fixture(source_root)
    source = source_root / "modules" / "sample_notes"
    data_dir = tmp_path / "data"
    with client_for(data_dir) as client:
        headers = csrf(client)
        assert client.post("/modules/install", headers=headers,
                           json={"path": str(source)}).status_code == 200
        assert client.post("/modules/sample_notes/enable", headers=headers).status_code == 200
        assert client.post("/modules/sample_notes/entries", headers=headers,
                           json={"value": "preserved"}).status_code == 200
        installed_ui = data_dir / "modules" / "sample_notes" / "ui.js"
        installed_ui.write_text("// modified after installation", encoding="utf-8")
        assert client.get("/modules/sample_notes/entries").status_code == 404
        assert client.get("/modules/sample_notes/ui.js").status_code == 404
        assert all(tool["name"] != "sample_notes__list_entries"
                   for tool in client.get("/tools").json())
        item = client.get("/modules").json()[0]
        assert item["enabled"] is False and item["requested_enabled"] is True
        assert "verändert" in item["error"]
        assert client.post("/modules/sample_notes/disable", headers=headers).status_code == 200
        assert client.post("/modules/sample_notes/remove", headers=headers).status_code == 200
        assert client.post("/modules/install", headers=headers,
                           json={"path": str(source)}).status_code == 200
        assert client.post("/modules/sample_notes/enable", headers=headers).status_code == 200
        assert client.get("/modules/sample_notes/entries").json() == ["preserved"]


def test_module_upgrade_failure_restores_previous_package(tmp_path, monkeypatch):
    source_root = tmp_path / "source"
    install_fixture(source_root)
    source = source_root / "modules" / "sample_notes"
    data_dir = tmp_path / "data"
    with client_for(data_dir) as client:
        headers = csrf(client)
        assert client.post("/modules/install", headers=headers,
                           json={"path": str(source)}).status_code == 200
        assert client.post("/modules/sample_notes/enable", headers=headers).status_code == 200
        assert client.post("/modules/sample_notes/upgrade", headers=headers,
                           json={"path": str(source)}).status_code == 422
        assert client.post("/modules/sample_notes/disable", headers=headers).status_code == 200
        target = data_dir / "modules" / "sample_notes"
        previous_digest = package_digest(target)
        unexpected = target / "personal.txt"
        unexpected.write_text("nicht löschen", encoding="utf-8")
        refused = client.post("/modules/sample_notes/upgrade", headers=headers,
                              json={"path": str(source)})
        assert refused.status_code == 422
        assert unexpected.read_text(encoding="utf-8") == "nicht löschen"
        unexpected.unlink()
        manifest = source / "jarvis-module.json"
        changed = json.loads(manifest.read_text(encoding="utf-8"))
        changed["version"] = "0.2.0"
        manifest.write_text(json.dumps(changed), encoding="utf-8")
        db = client.app.state.modules.db
        original = db.set_settings

        def fail_receipt(values):
            if "module.sample_notes.package_sha256" in values:
                raise OSError("injected receipt failure")
            return original(values)

        monkeypatch.setattr(db, "set_settings", fail_receipt)
        failed = client.post("/modules/sample_notes/upgrade", headers=headers,
                             json={"path": str(source)})
        assert failed.status_code == 409
        monkeypatch.setattr(db, "set_settings", original)
        assert package_digest(target) == previous_digest
        assert not list(data_dir.glob(".jarvis-module-*"))
        assert client.post("/modules/sample_notes/enable", headers=headers).status_code == 200
        assert client.get("/modules/sample_notes/entries").status_code == 200


def test_module_upgrade_migrates_existing_data_once(tmp_path):
    source_root = tmp_path / "source"
    install_fixture(source_root)
    source = source_root / "modules" / "sample_notes"
    data_dir = tmp_path / "data"
    with client_for(data_dir) as client:
        headers = csrf(client)
        assert client.post("/modules/install", headers=headers,
                           json={"path": str(source)}).status_code == 200
        assert client.post("/modules/sample_notes/enable", headers=headers).status_code == 200
        assert client.post("/modules/sample_notes/entries", headers=headers,
                           json={"value": "old row"}).status_code == 200
        assert client.post("/modules/sample_notes/disable", headers=headers).status_code == 200
        manifest = source / "jarvis-module.json"
        upgraded = json.loads(manifest.read_text(encoding="utf-8"))
        upgraded.update(version="0.2.0", data_version=2)
        manifest.write_text(json.dumps(upgraded), encoding="utf-8")
        (source / "module.py").write_text(SAMPLE_MODULE.replace(
            "def migrate(connection, from_version):\n    if from_version == 0:\n"
            "        connection.execute(\"CREATE TABLE IF NOT EXISTS fixture_module(value TEXT NOT NULL)\")",
            "def migrate(connection, from_version):\n    if from_version == 0:\n"
            "        connection.execute(\"CREATE TABLE IF NOT EXISTS fixture_module(value TEXT NOT NULL)\")\n"
            "    if from_version < 2:\n"
            "        connection.execute(\"ALTER TABLE fixture_module ADD COLUMN tag TEXT DEFAULT 'kept'\")"),
            encoding="utf-8")
        upgraded_response = client.post("/modules/sample_notes/upgrade", headers=headers,
                                        json={"path": str(source)})
        assert upgraded_response.status_code == 200, upgraded_response.text
        assert upgraded_response.json()["version"] == "0.2.0"
        assert client.post("/modules/sample_notes/enable", headers=headers).status_code == 200
        assert client.get("/modules/sample_notes/entries").json() == ["old row"]
        assert client.post("/modules/sample_notes/disable", headers=headers).status_code == 200
        assert client.post("/modules/sample_notes/enable", headers=headers).status_code == 200
    db = Database(data_dir / "jarvis.db")
    assert db.get_settings()["module.sample_notes.data_version"] == "2"
    with db.connect() as connection:
        assert connection.execute("SELECT value,tag FROM fixture_module").fetchone()[:] == (
            "old row", "kept")

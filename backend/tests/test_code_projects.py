"""A code project stays inside its local folder grant and cites real files."""

import ctypes
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from jarvis.app import create_app
from jarvis.code_projects import CodeProjectStore
from jarvis.config import Settings
from jarvis.db import Database
from jarvis.documents import DocumentStore


def project_at(tmp_path):
    root = tmp_path / "project"
    (root / ".git").mkdir(parents=True)
    (root / "src").mkdir()
    (root / "tests").mkdir()
    (root / "node_modules").mkdir()
    (root / "src" / "main.py").write_text("from util import run\nrun()\n", encoding="utf-8")
    (root / "src" / "util.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    (root / "tests" / "test_main.py").write_text("def test_run():\n    assert True\n",
                                                   encoding="utf-8")
    (root / "pyproject.toml").write_text("[project]\nname='sample'\n", encoding="utf-8")
    (root / "README.md").write_text("# Sample\n", encoding="utf-8")
    (root / "node_modules" / "server.js").write_text("SECRET", encoding="utf-8")
    (root / ".env").write_text("SECRET", encoding="utf-8")
    data = tmp_path / "data"
    documents = DocumentStore(Database(data / "jarvis.db"))
    folder = documents.add_folder(str(root), "local_only")
    return root, data, documents, folder


def test_inventory_file_preview_grant_and_limits(tmp_path, monkeypatch):
    root, _, documents, folder = project_at(tmp_path)
    store = CodeProjectStore(documents)
    outside = tmp_path / "outside.py"
    outside.write_text("SECRET", encoding="utf-8")
    link_created = False
    try:
        (root / "src" / "outside.py").symlink_to(outside)
        link_created = True
    except OSError:
        pass

    result = store.overview(folder["id"])
    assert result["git_checkout"] and not result["limited"]
    assert result["languages"] == {"Python": 3}
    assert result["modules"] == [{"path": "src", "code_files": 2},
                                  {"path": "tests", "code_files": 1}]
    assert [item["path"] for item in result["files"]["entrypoints"]] == ["src/main.py"]
    assert result["files"]["tests"][0]["path"] == "tests/test_main.py"
    assert result["files"]["config"][0]["path"] == "pyproject.toml"
    assert result["files"]["docs"][0]["path"] == "README.md"
    assert all("outside" not in str(items) and "SECRET" not in str(items)
               for items in result["files"].values())
    assert result["scanned_files"] == 5
    if link_created:
        assert result["skipped"] >= 1

    preview = store.read_file(folder["id"], "src/main.py")
    assert preview["lines"] == [{"number": 1, "text": "from util import run"},
                                 {"number": 2, "text": "run()"}]
    assert preview["path"] == "src/main.py"
    for path in ("../outside.py", "src/outside.py", "node_modules/server.js", ".env",
                 "C:\\Windows\\win.ini", "missing.py"):
        with pytest.raises((ValueError, OSError)):
            store.read_file(folder["id"], path)
    (root / "src" / "large.py").write_bytes(b"a" * (256 * 1024 + 1))
    with pytest.raises(ValueError, match="zu groß"):
        store.read_file(folder["id"], "src/large.py")
    monkeypatch.setattr("jarvis.code_projects.MAX_FILES", 2)
    limited = store.overview(folder["id"])
    assert limited["limited"] and limited["scanned_files"] == 2
    monkeypatch.setattr("jarvis.code_projects.MAX_FILES", 3000)
    monkeypatch.setattr("jarvis.code_projects.MAX_DIRECTORIES", 1)
    assert store.overview(folder["id"])["limited"]
    documents.remove_folder(folder["id"])
    with pytest.raises(LookupError):
        store.overview(folder["id"])


def test_code_project_api_uses_local_session(tmp_path):
    _, data, _, folder = project_at(tmp_path)
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=data,
                                        JARVIS_OLLAMA_MODEL="fixture"))) as client:
        assert client.get(f"/code-projects/{folder['id']}/overview").status_code == 401
        client.get("/session")
        summary = client.get(f"/code-projects/{folder['id']}/overview")
        assert summary.status_code == 200
        assert summary.json()["files"]["entrypoints"][0]["path"] == "src/main.py"
        preview = client.get(f"/code-projects/{folder['id']}/file",
                             params={"path": "src/main.py"})
        assert preview.status_code == 200 and preview.json()["lines"][1]["number"] == 2
        assert client.get(f"/code-projects/{folder['id']}/file",
                          params={"path": "../outside.py"}).status_code == 422
        assert client.get("/code-projects/missing/overview").status_code == 404


@pytest.mark.skipif(sys.platform != "win32", reason="Windows hidden attribute")
def test_windows_hidden_entries_are_excluded(tmp_path):
    root, _, documents, folder = project_at(tmp_path)
    hidden_dir = root / "private"
    hidden_dir.mkdir()
    (hidden_dir / "main.py").write_text("secret", encoding="utf-8")
    hidden_file = root / "private.py"
    hidden_file.write_text("secret", encoding="utf-8")
    for path in (hidden_dir, hidden_file):
        attributes = ctypes.windll.kernel32.GetFileAttributesW(str(path))
        assert attributes != 0xFFFFFFFF
        assert ctypes.windll.kernel32.SetFileAttributesW(str(path), attributes | 2)
    store = CodeProjectStore(documents)
    result = store.overview(folder["id"])
    assert result["scanned_files"] == 5
    assert result["skipped"] >= 2
    for path in ("private/main.py", "private.py"):
        with pytest.raises(ValueError, match="Versteckte Pfade"):
            store.read_file(folder["id"], path)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows junction")
def test_windows_junction_cannot_escape_grant(tmp_path):
    root, _, documents, folder = project_at(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "main.py").write_text("secret", encoding="utf-8")
    junction = root / "alias"
    created = subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(outside)],
                             capture_output=True, check=False)
    if created.returncode:
        pytest.skip("Junction creation unavailable")
    store = CodeProjectStore(documents)
    result = store.overview(folder["id"])
    assert result["scanned_files"] == 5 and result["skipped"] >= 1
    with pytest.raises(ValueError, match="Verknüpfungen"):
        store.read_file(folder["id"], "alias/main.py")

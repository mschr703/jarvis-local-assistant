"""Real local Graphify AST, Markdown evidence, and collision-safe Obsidian rollback."""

import json

import pytest
from fastapi.testclient import TestClient

from jarvis.app import create_app
from jarvis.config import Settings
from jarvis.db import Database
from jarvis.documents import DocumentStore
from jarvis.file_graph import FileGraph


def _setup(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "a.py").write_text(
        "from b import helper\n\ndef main():\n    return helper()\n", encoding="utf-8"
    )
    (source / "b.py").write_text(
        "def helper():\n    return 42\n", encoding="utf-8"
    )
    (source / "start.md").write_text(
        "# Start\nSiehe [[next]].\n", encoding="utf-8"
    )
    (source / "next.md").write_text("# Nächster Schritt\n", encoding="utf-8")
    db = Database(tmp_path / "data" / "jarvis.db")
    documents = DocumentStore(db)
    folder = documents.add_folder(str(source), "local_only")
    graph = FileGraph(documents, tmp_path / "data")
    vault = tmp_path / "vault"
    (vault / ".obsidian").mkdir(parents=True)
    (vault / ".obsidian" / "app.json").write_bytes(b'{"safe":true}')
    (vault / "Eigene Notiz.md").write_bytes(b"Eigene Notiz bleibt")
    return source, documents, folder, graph, vault


def test_code_and_markdown_graph_with_source_evidence(tmp_path):
    source, documents, folder, graph, _ = _setup(tmp_path)
    preview = graph.preview(folder["id"])
    assert preview["graphify_version"] == "0.9.101"
    assert len(preview["files"]) == 4
    assert any(edge["relation"] == "imports_from" and edge["source_file"] == "a.py"
               and edge["confidence"] == "EXTRACTED" for edge in preview["edges"])
    assert any(edge["relation"] == "links_to" and edge["source_file"] == "start.md"
               and edge["source_location"] == "L2" and edge["confidence"] == "EXTRACTED"
               for edge in preview["edges"])
    assert all(edge["source_file"] in preview["files"] for edge in preview["edges"])
    (source / "a.py").write_text("from b import helper\n", encoding="utf-8")
    assert graph.preview(folder["id"])["fingerprint"] != preview["fingerprint"]
    documents.remove_folder(folder["id"])
    with pytest.raises(ValueError, match="nicht gefunden"):
        graph.preview(folder["id"])


def test_obsidian_export_preserves_collisions_and_rollback_manifest(tmp_path):
    source, documents, folder, graph, vault = _setup(tmp_path)
    before_settings = (vault / ".obsidian" / "app.json").read_bytes()
    before_note = (vault / "Eigene Notiz.md").read_bytes()
    preview = graph.export_preview(folder["id"], str(vault))
    assert "Graph.md" in preview["files"]
    assert len([name for name in preview["files"] if name.startswith("nodes/")]) >= 4
    with pytest.raises(ValueError, match="geändert"):
        graph.export(folder["id"], str(vault), "0" * 64)
    assert not (vault / "Jarvis Graphify").exists()
    exported = graph.export(folder["id"], str(vault), preview["fingerprint"])
    target = vault / "Jarvis Graphify" / ("Graph-" + folder["id"][:12])
    assert target == source.__class__(exported["target"])
    assert "EXTRACTED" in (target / "Graph.md").read_text(encoding="utf-8")
    assert "(nodes/" in (target / "Graph.md").read_text(encoding="utf-8")
    assert len(list((target / "nodes").glob("*.md"))) >= 4
    assert json.loads((target / "manifest.json").read_text(encoding="utf-8"))["id"] == exported["id"]
    assert graph.list_exports()[0]["id"] == exported["id"]
    graph = FileGraph(documents, tmp_path / "data")  # Jarvis-Dienst nach Neustart
    assert graph.list_exports()[0]["id"] == exported["id"]
    with pytest.raises(ValueError, match="existiert"):
        graph.export_preview(folder["id"], str(vault))
    saved = (target / "Graph.md").read_bytes()
    (target / "Graph.md").write_bytes(b"Eigene Aenderung")
    with pytest.raises(ValueError, match="verändert"):
        graph.rollback(exported["id"])
    assert (target / "Graph.md").read_bytes() == b"Eigene Aenderung"
    (target / "Graph.md").write_bytes(saved)
    assert graph.rollback(exported["id"])["removed"]
    assert not target.exists()
    assert not graph.list_exports()
    assert (vault / ".obsidian" / "app.json").read_bytes() == before_settings
    assert (vault / "Eigene Notiz.md").read_bytes() == before_note


def test_source_link_outside_grant_is_not_staged(tmp_path):
    source, _, folder, graph, _ = _setup(tmp_path)
    outside = tmp_path / "secret.py"
    outside.write_text("SECRET = 1", encoding="utf-8")
    try:
        (source / "outside.py").symlink_to(outside)
    except OSError:
        pytest.skip("Symlink-Recht fehlt")
    preview = graph.preview(folder["id"])
    assert "outside.py" not in preview["files"]
    assert all("SECRET" not in node["label"] for node in preview["nodes"])


def test_file_graph_api_uses_folder_grant_and_local_session(tmp_path):
    source, _, folder, _, _ = _setup(tmp_path)
    app = create_app(Settings(JARVIS_DATA_DIR=tmp_path / "data",
                              JARVIS_PROVIDER="ollama", JARVIS_OLLAMA_MODEL="fixture"))
    with TestClient(app) as client:
        csrf = client.get("/session").json()["csrf"]
        headers = {"x-jarvis-csrf": csrf}
        assert any(item["id"] == folder["id"] for item in client.get(
            "/folders", headers=headers).json())
        exports = client.get("/file-graphs/exports", headers=headers)
        assert exports.status_code == 200, exports.text
        assert exports.json() == []
        preview = client.post(f"/file-graphs/{folder['id']}/preview", headers=headers)
        assert preview.status_code == 200, preview.text
        assert preview.json()["source_root"] == str(source)

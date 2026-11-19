"""The user can check bounded original freshness without mutating indexed text."""

import pytest
from fastapi.testclient import TestClient

from jarvis.app import create_app
from jarvis.config import Settings
from jarvis.db import Database
from jarvis.documents import DocumentStore


def sample(tmp_path):
    root = tmp_path / "allowed"
    root.mkdir()
    one = root / "one.md"
    two = root / "two.md"
    one.write_text("Eins: Wohnung und Kaution", encoding="utf-8")
    two.write_text("Zwei: Stadt und Termin", encoding="utf-8")
    db = Database(tmp_path / "data" / "jarvis.db")
    store = DocumentStore(db)
    folder = store.add_folder(str(root), "local_only")
    assert store.index_folder(folder["id"])["indexed"] == 2
    return db, store, folder, one, two


def test_freshness_reports_changes_and_recovers_after_reindex(tmp_path):
    _, store, folder, one, two = sample(tmp_path)
    first = store.folder_freshness(folder["id"])
    assert (first["checked"], first["current"], first["stale"]) == (2, 2, 0)
    one.write_text("Eins: Wohnung und Vertrag", encoding="utf-8")
    two.unlink()
    report = store.folder_freshness(folder["id"])
    assert (report["checked"], report["current"], report["stale"]) == (2, 0, 2)
    assert {item["path"] for item in report["stale_sources"]} == {str(one), str(two)}
    assert all("text" not in item for item in report["stale_sources"])
    assert len(store.documents()) == 2  # read-only check does not mutate the index
    store.index_folder(folder["id"])
    assert (store.folder_freshness(folder["id"])["checked"],
            store.folder_freshness(folder["id"])["stale"]) == (1, 0)


def test_freshness_skips_excluded_and_limits_candidates(tmp_path):
    db, store, folder, _, _ = sample(tmp_path)
    excluded = store.documents()[0]
    store.set_excluded(excluded["id"], True)
    assert store.folder_freshness(folder["id"])["checked"] == 1
    with db.connect() as connection:
        other = connection.execute("SELECT * FROM documents WHERE excluded=0").fetchone()
        for number in range(101):
            connection.execute("""INSERT INTO documents
                (id,folder_id,path,sha256,mtime_ns,size,policy,status,indexed_at)
                VALUES(?,?,?,?,?,?,?,?,?)""",
                (f"extra-{number}", folder["id"], str(tmp_path / f"missing-{number}.md"),
                 other["sha256"], 0, other["size"], "local_only", "indexed", other["indexed_at"]))
    report = store.folder_freshness(folder["id"])
    assert report["checked"] == 100 and report["limited"] is True
    assert report["stale"] == 99 and report["current"] == 1
    assert excluded["id"] not in {item["document_id"] for item in report["stale_sources"]}


def test_freshness_rejects_removed_grant_and_missing_root(tmp_path):
    _, store, folder, _, _ = sample(tmp_path)
    with pytest.raises(LookupError):
        store.folder_freshness("missing")
    store.remove_folder(folder["id"])
    with pytest.raises(LookupError):
        store.folder_freshness(folder["id"])

    root = tmp_path / "other"
    root.mkdir()
    source = root / "source.md"
    source.write_text("Eine weitere Quelle", encoding="utf-8")
    new_folder = store.add_folder(str(root), "local_only")
    store.index_folder(new_folder["id"])
    source.unlink()
    root.rmdir()
    with pytest.raises(OSError):
        store.folder_freshness(new_folder["id"])


def test_freshness_rejects_exclusion_during_check(tmp_path, monkeypatch):
    _, store, folder, _, _ = sample(tmp_path)
    target = store.documents()[0]
    original = store.source_is_current

    def revoke(row):
        result = original(row)
        if row["id"] == target["id"]:
            store.set_excluded(target["id"], True)
        return result

    monkeypatch.setattr(store, "source_is_current", revoke)
    with pytest.raises(ValueError, match="Index während"):
        store.folder_freshness(folder["id"])


def test_freshness_api_requires_session_and_returns_bounded_report(tmp_path):
    db, _, folder, one, _ = sample(tmp_path)
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=db.path.parent,
                                        JARVIS_OLLAMA_MODEL="fixture"))) as client:
        url = f"/folders/{folder['id']}/freshness"
        assert client.get(url).status_code == 401
        client.get("/session")
        one.write_text("Eins: Änderung und Vertrag", encoding="utf-8")
        response = client.get(url)
        assert response.status_code == 200
        assert response.json()["stale"] == 1
        assert response.json()["stale_sources"][0]["path"] == str(one)
        assert client.get("/folders/missing/freshness").status_code == 404


def test_single_document_open_check_tracks_changed_original_and_removal(tmp_path):
    db, store, folder, one, _ = sample(tmp_path)
    document = next(item for item in store.documents() if item["path"] == str(one))
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=db.path.parent,
                                        JARVIS_OLLAMA_MODEL="fixture"))) as client:
        url = f"/documents/{document['id']}/freshness"
        assert client.get(url).status_code == 401
        client.get("/session")
        current = client.get(url)
        assert current.status_code == 200 and current.json()["status"] == "current"
        assert "Wohnung und Kaution" not in current.text
        one.write_text("Eins: Wohnung und Vertrag", encoding="utf-8")
        stale = client.get(url)
        assert stale.status_code == 200 and stale.json()["status"] == "needs_reindex"
        store.index_folder(folder["id"])
        assert client.get(url).json()["status"] == "current"
        store.remove_folder(folder["id"])
        assert client.get(url).status_code == 404

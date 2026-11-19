"""G07: immutable local holder sources and approval-gated exports."""

import hashlib
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from jarvis.app import create_app
from jarvis.backup import create_backup, restore_backup
from jarvis.cad import (
    CadJob,
    CadTools,
    HolderBuildArgs,
    HolderExportArgs,
    render_holder,
    validate_dimension_proposal,
)
from jarvis.config import Settings
from jarvis.engine import ToolUserError

VALUES = {"description": "Halter mit zwei Bohrungen", "width_mm": 60,
          "depth_mm": 30, "thickness_mm": 6, "hole_diameter_mm": 5,
          "hole_spacing_mm": 40}


def test_holder_accepts_only_finite_valid_dimensions_and_fixed_template():
    script = render_holder(HolderBuildArgs(**VALUES))
    assert "WIDTH_MM = 60.0" in script
    assert "HOLE_SPACING_MM = 40.0" in script
    assert "@model" in script
    for changes in ({"hole_spacing_mm": 59}, {"hole_diameter_mm": 30},
                    {"width_mm": float("nan")}, {"description": "   "},
                    {"description": "x\nimport os\nos.system('bad')", "extra": 1}):
        with pytest.raises(ValidationError):
            HolderBuildArgs(**(VALUES | changes))


def test_dimension_proposal_never_invents_a_measure():
    description = "Halter 60 mm breit, 30 mm tief, 6 mm dick, Loch 5 mm, Abstand 40 mm"
    proposal = {key: VALUES[key] for key in VALUES if key.endswith("_mm")}
    assert validate_dimension_proposal(description, proposal).width_mm == 60
    with pytest.raises(ToolUserError, match="nicht genanntes"):
        validate_dimension_proposal(description, proposal | {"width_mm": 61})
    with pytest.raises(ToolUserError, match="nicht genanntes"):
        validate_dimension_proposal(description + ", 2 Bohrungen", proposal | {"width_mm": 2})
    with pytest.raises(ToolUserError, match="nicht genanntes"):
        validate_dimension_proposal(description.replace("60 mm", "-60 mm"), proposal)


def test_ollama_dimension_proposal_is_local_and_only_a_suggestion(tmp_path, monkeypatch):
    description = "Halter 60 mm breit, 30 mm tief, 6 mm dick, Loch 5 mm, Abstand 40 mm"
    seen = []

    async def fake_generate(_db, settings, purpose, _messages):
        seen.append((settings.provider, purpose))
        return SimpleNamespace(text=json.dumps({key: VALUES[key] for key in VALUES
                                                if key.endswith("_mm")}), usage_id="local-test")

    monkeypatch.setattr("jarvis.app.generate_text", fake_generate)
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=tmp_path,
                                        JARVIS_PROVIDER="openrouter"))) as client:
        headers = {"x-jarvis-csrf": client.get("/session").json()["csrf"]}
        response = client.post("/cad/holder-proposal", headers=headers,
                               json={"description": description})
        assert response.status_code == 200, response.text
        assert response.json()["dimensions"]["hole_spacing_mm"] == 40
        assert seen == [("ollama", "cad_holder_proposal")]
        assert not (tmp_path / "cad-models").exists()


def fake_cad_run(self, *args):
    if args[0] == "run":
        return "id: fake-document\n"
    if args[0] == "snapshot":
        Path(args[2]).write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 100)
    elif args[0] == "export":
        Path(args[2]).write_bytes(b"ISO-10303-21;\n" + b"x" * 200)
    return "ok"


def test_cad_preview_download_and_export_approval(tmp_path, monkeypatch):
    monkeypatch.setattr(CadJob, "run", fake_cad_run)
    monkeypatch.setattr(CadJob, "_engine_pids", lambda self: [])
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=tmp_path))) as client:
        headers = {"x-jarvis-csrf": client.get("/session").json()["csrf"]}
        build = client.post("/runs", headers=headers, json={
            "tool_name": "cad_holder_build", "args": VALUES,
            "client_request_id": "cad-build-test",
        }).json()
        assert build["status"] == "succeeded"
        result = json.loads(build["result_json"])
        assert client.get(f"/runs/{build['id']}/cad-preview").status_code == 200
        assert client.get(f"/runs/{build['id']}/cad-script").status_code == 200
        assert client.get(f"/runs/{build['id']}/cad-file").status_code == 404
        model = tmp_path / "cad-models" / result["model_id"]
        assert hashlib.sha256((model / "holder.py").read_bytes()).hexdigest() == result["source_sha256"]
        export = client.post("/runs", headers=headers, json={
            "tool_name": "cad_holder_export", "args": {"model_id": result["model_id"],
                "expected_sha256": result["source_sha256"], "format": "step"},
            "client_request_id": "cad-export-test",
        }).json()
        assert export["status"] == "waiting_approval"
        assert not (model / "holder.step").exists()
        assert client.get(f"/runs/{export['id']}/cad-file").status_code == 404
        approved = client.post(f"/runs/{export['id']}/decision", headers=headers,
                               json={"args_hash": export["args_hash"], "approved": True}).json()
        assert approved["status"] == "succeeded"
        assert client.get(f"/runs/{export['id']}/cad-file").status_code == 200
        (model / "holder.py").write_text("tampered", encoding="utf-8")
        assert client.get(f"/runs/{build['id']}/cad-script").status_code == 404
        with pytest.raises(ToolUserError, match="verändert"):
            CadTools(tmp_path).export(HolderExportArgs(model_id=result["model_id"],
                                     expected_sha256=result["source_sha256"],
                                     format="stl"), threading.Event())
        assert client.get(f"/runs/{export['id']}/cad-file").status_code == 200


def test_forged_manifest_cannot_execute_arbitrary_python(tmp_path):
    model_id = "12345678-1234-4234-8234-123456789abc"
    model = tmp_path / "cad-models" / model_id
    model.mkdir(parents=True)
    script = model / "holder.py"
    script.write_text("import os\nos.system('bad')\n", encoding="utf-8")
    digest = hashlib.sha256(script.read_bytes()).hexdigest()
    (model / "manifest.json").write_text(json.dumps({
        "model_id": model_id, "template": "g07-holder-v1",
        "description": VALUES["description"],
        "dimensions_mm": {"width": 60, "depth": 30, "thickness": 6,
                          "hole_diameter": 5, "hole_spacing": 40},
        "source_sha256": digest,
    }), encoding="utf-8")
    with pytest.raises(ToolUserError, match="geprüften Vorlage"):
        CadTools(tmp_path).export(HolderExportArgs(model_id=model_id,
                                                  expected_sha256=digest,
                                                  format="step"), threading.Event())


def test_cad_backup_keeps_run_bound_preview_and_download(tmp_path, monkeypatch):
    monkeypatch.setattr(CadJob, "run", fake_cad_run)
    monkeypatch.setattr(CadJob, "_engine_pids", lambda self: [])
    source = tmp_path / "source"
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=source))) as client:
        headers = {"x-jarvis-csrf": client.get("/session").json()["csrf"]}
        build = client.post("/runs", headers=headers, json={
            "tool_name": "cad_holder_build", "args": VALUES,
            "client_request_id": "cad-backup-build"}).json()
        details = json.loads(build["result_json"])
        export = client.post("/runs", headers=headers, json={
            "tool_name": "cad_holder_export", "args": {"model_id": details["model_id"],
                "expected_sha256": details["source_sha256"], "format": "step"},
            "client_request_id": "cad-backup-export"}).json()
        assert client.post(f"/runs/{export['id']}/decision", headers=headers,
                           json={"args_hash": export["args_hash"],
                                 "approved": True}).json()["status"] == "succeeded"
    archive = tmp_path / "cad.zip"
    create_backup(source, archive)
    restored = tmp_path / "restored"
    restore_backup(archive, restored)
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=restored))) as client:
        client.get("/session")
        assert client.get(f"/runs/{build['id']}/cad-preview").status_code == 200
        assert client.get(f"/runs/{build['id']}/cad-script").status_code == 200
        assert client.get(f"/runs/{export['id']}/cad-file").status_code == 200


def test_invalid_model_leaves_prior_file_unchanged(tmp_path, monkeypatch):
    monkeypatch.setattr(CadJob, "run", fake_cad_run)
    monkeypatch.setattr(CadJob, "_engine_pids", lambda self: [])
    cad = CadTools(tmp_path)
    result = cad.build(HolderBuildArgs(**VALUES), threading.Event())
    directory = tmp_path / "cad-models" / result["model_id"]
    old_preview = (directory / "preview.png").read_bytes()
    with pytest.raises(ValidationError):
        cad.build(HolderBuildArgs(**(VALUES | {"hole_spacing_mm": 56})), threading.Event())
    assert (directory / "preview.png").read_bytes() == old_preview

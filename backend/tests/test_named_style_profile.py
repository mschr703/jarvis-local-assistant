"""The selected Maxim profile is explicit, versioned and local by default."""

from fastapi.testclient import TestClient

from jarvis.app import create_app
from jarvis.backup import create_backup, restore_backup
from jarvis.config import Settings
from jarvis.db import Database
from jarvis.drafts import DraftStore
from jarvis.provider import Completion


def csrf(client):
    return {"x-jarvis-csrf": client.get("/session").json()["csrf"]}


def test_maxim_choice_applies_to_four_kinds_and_survives_profile_change(tmp_path, monkeypatch):
    prompts = []

    async def generate(_db, _config, _purpose, messages):
        prompts.append(messages[-1]["content"])
        return Completion("Mein Entwurf mit [1].", 15, 8, 0, "local_no_api_charge")

    monkeypatch.setattr("jarvis.app.generate_text", generate)
    settings = Settings(JARVIS_DATA_DIR=tmp_path, JARVIS_OLLAMA_MODEL="fixture")
    with TestClient(create_app(settings)) as client:
        token = csrf(client)
        assert client.get("/style/named/Maxim").json() is None
        missing = client.post("/drafts/generate", headers=token, json={
            "kind": "text", "brief": "Schreibe.", "named_style_profile": "Maxim"})
        assert missing.status_code == 422 and not prompts
        created = client.put("/style/named/Maxim", headers=token, json={
            "description": "Kurze direkte Sätze. Ich schreibe ohne Floskeln.",
            "policy": "local_only", "expected_revision": 0})
        assert created.status_code == 200 and created.json()["revision"] == 1
        sample = client.post("/style/samples", headers=token, json={
            "title": "Eigene Mail", "content": "Hallo, ich fasse mich kurz. Bis bald, Maxim.",
            "policy": "local_only"}).json()
        drafts = []
        for kind in ("email", "text", "answer", "essay"):
            body = {"kind": kind, "brief": "Schreibe einen Entwurf.",
                    "named_style_profile": "Maxim", "style_sample_ids": [sample["id"]]}
            if kind == "answer":
                body["input_text"] = "Was ist bekannt?"
            if kind == "essay":
                body.update(word_target=300, outline=["Einleitung", "Schluss"])
            response = client.post("/drafts/generate", headers=token, json=body)
            assert response.status_code == 200, response.text
            draft = response.json()
            assert draft["style_snapshot"] == created.json()["description"]
            assert draft["named_style_profile"] == {
                "name": "Maxim", "revision": 1, "description": created.json()["description"]}
            drafts.append(draft)
        assert len(prompts) >= 4
        assert all("Kurze direkte Sätze" in prompt for prompt in prompts)
        assert all("Hallo, ich fasse mich kurz" in prompt for prompt in prompts)
        changed = client.put("/style/named/Maxim", headers=token, json={
            "description": "Neue Wortwahl.", "policy": "local_only", "expected_revision": 1})
        assert changed.status_code == 200 and changed.json()["revision"] == 2
        assert client.put("/style/named/Maxim", headers=token, json={
            "description": "Veraltet", "policy": "local_only",
            "expected_revision": 1}).status_code == 409
        assert client.get(f"/drafts/{drafts[0]['id']}").json()["named_style_profile"]["revision"] == 1
        new = client.post("/drafts/generate", headers=token, json={
            "kind": "text", "brief": "Noch ein Text.", "named_style_profile": "Maxim"}).json()
        assert new["named_style_profile"]["revision"] == 2
        assert "Neue Wortwahl" in prompts[-1]
        ordinary = client.post("/drafts/generate", headers=token, json={
            "kind": "text", "brief": "Ohne Profil."}).json()
        assert ordinary["named_style_profile"] is None

    archive = tmp_path.parent / "maxim.zip"
    assert create_backup(tmp_path, archive)["schema"] == 64
    restored = tmp_path.parent / "restored-maxim"
    assert restore_backup(archive, restored)["schema"] == 64
    store = DraftStore(Database(restored / "jarvis.db"), restored)
    assert store.named_profile()["revision"] == 2
    assert store.get_draft(drafts[0]["id"])["named_style_profile"]["revision"] == 1


def test_maxim_cloud_policy_blocks_before_provider_call(tmp_path, monkeypatch):
    calls = []

    async def generate(_db, _config, _purpose, messages):
        calls.append(messages)
        return Completion("Text", 10, 2, 0, "reported")

    monkeypatch.setattr("jarvis.app.generate_text", generate)
    settings = Settings(JARVIS_DATA_DIR=tmp_path, JARVIS_PROVIDER="openrouter",
                        JARVIS_OPENROUTER_API_KEY="fixture", JARVIS_OPENROUTER_MODEL="fixture")
    with TestClient(create_app(settings)) as client:
        token = csrf(client)
        assert client.put("/style/named/Maxim", headers=token, json={
            "description": "Mein persönlicher Stil.", "policy": "local_only",
            "expected_revision": 0}).status_code == 200
        blocked = client.post("/drafts/generate", headers=token, json={
            "kind": "text", "brief": "Schreibe.", "named_style_profile": "Maxim",
            "allow_cloud": True})
        assert blocked.status_code == 422 and not calls
        assert client.put("/style/named/Maxim", headers=token, json={
            "description": "Mein persönlicher Stil.", "policy": "cloud_allowed",
            "expected_revision": 1}).status_code == 200
        allowed = client.post("/drafts/generate", headers=token, json={
            "kind": "text", "brief": "Schreibe.", "named_style_profile": "Maxim",
            "allow_cloud": True})
        assert allowed.status_code == 200 and len(calls) == 1
        assert allowed.json()["named_style_profile"]["revision"] == 2

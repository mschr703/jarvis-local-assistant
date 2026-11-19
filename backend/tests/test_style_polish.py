"""Optional local polish stays a reviewable proposal and guards literal facts."""

from fastapi.testclient import TestClient

from jarvis.app import create_app
from jarvis.config import Settings
from jarvis.db import Database
from jarvis.drafts import DraftStore
from jarvis.provider import Completion
from jarvis.style_polish import protected_changes, writing_signals


def csrf(client):
    return {"x-jarvis-csrf": client.get("/session").json()["csrf"]}


def test_protected_details_detect_additions_and_removals():
    before = 'Am 25.10.2026 steht [1] „genau das“. Link: https://example.com/a. Mail an a@b.de.'
    assert protected_changes(before, before) == []
    assert protected_changes(before, before.replace("[1]", "[2]")) == ["Quellenmarke", "Zahl/Datum"]
    changed = before.replace("https://example.com/a", "https://example.com/b")
    assert protected_changes(before, changed) == ["URL"]
    assert protected_changes(before, before + " Neu: 42.") == ["Zahl/Datum"]
    assert protected_changes(before, before.replace("„genau das“", "„ungefähr das“")) == ["Zitat"]


def test_writing_signals_are_specific_bounded_and_ignore_quotes():
    content = ('„In der heutigen digitalen Welt“ ist ein Zitat. '
               'In der heutigen schnelllebigen Welt. Es ist wichtig zu beachten, dass '
               'wir am Montag starten. Zusammenfassend lässt sich sagen, dass es passt.')
    signals = writing_signals(content)
    assert [signal["id"] for signal in signals] == [
        "generic_context", "filler_notice", "generic_conclusion",
    ]
    assert all(content[signal["start"]:signal["end"]] == signal["excerpt"]
               for signal in signals)
    assert writing_signals("Mir ist wichtig, dass wir am Montag starten.") == []
    assert len(writing_signals("Es ist wichtig zu beachten, dass " * 30)) == 20


def test_writing_signals_api_checks_unsaved_text_without_provider(tmp_path, monkeypatch):
    async def forbidden(*_args):
        raise AssertionError("Musterprüfung darf kein Modell aufrufen")

    monkeypatch.setattr("jarvis.app.generate_text", forbidden)
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=tmp_path))) as client:
        response = client.post("/style/writing-signals", headers=csrf(client),
                               json={"content": "Zusammenfassend lässt sich sagen, dass es passt."})
        assert response.status_code == 200
        assert response.json()["signals"][0]["id"] == "generic_conclusion"
        assert "keine KI-Erkennung" in response.json()["notice"]
        assert client.post("/style/writing-signals", headers=csrf(client),
                           json={"content": "x" * 40_001}).status_code == 422


def test_polish_preview_does_not_modify_draft_and_checks_stale_version(tmp_path, monkeypatch):
    calls = []
    source = 'Hallo! Am 25.10.2026 gilt [1] „Termin bestätigt“. Siehe https://example.com/a.'
    proposals = [
        'Hallo, am 25.10.2026 gilt [1] „Termin bestätigt“. Siehe https://example.com/a.',
        'Hallo, am 26.10.2026 gilt [1] „Termin bestätigt“. Siehe https://example.com/a.',
    ]

    async def generate(_db, _config, purpose, messages):
        calls.append(messages)
        assert purpose == "draft"
        return Completion(proposals.pop(0), 20, 15, 0, "local_no_api_charge")

    monkeypatch.setattr("jarvis.app.generate_text", generate)
    settings = Settings(JARVIS_DATA_DIR=tmp_path, JARVIS_OLLAMA_MODEL="fixture")
    with TestClient(create_app(settings)) as client:
        token = csrf(client)
        store = DraftStore(Database(tmp_path / "jarvis.db"), tmp_path)
        profile = store.set_named_profile("Maxim", "Kurz und direkt.", "local_only", 0)
        draft = store.create_draft("text", "", "Kurz schreiben", source, [], "", profile["description"],
                                   named_profile=profile)
        result = client.post(f"/drafts/{draft['id']}/style-polish", headers=token,
                             json={"expected_version": 1})
        assert result.status_code == 200, result.text
        assert result.json()["profile_revision"] == 1
        assert result.json()["proposal"] != source
        assert store.get_draft(draft["id"])["content"] == source
        assert "Kurz und direkt." in calls[0][-1]["content"]
        assert "25.10.2026" in calls[0][-1]["content"]
        rejected = client.post(f"/drafts/{draft['id']}/style-polish", headers=token,
                               json={"expected_version": 1})
        assert rejected.status_code == 422 and "Zahl/Datum" in rejected.text
        assert store.get_draft(draft["id"])["content"] == source
        store.update_draft(draft["id"], "Geänderte Fassung", 1)
        stale = client.post(f"/drafts/{draft['id']}/style-polish", headers=token,
                            json={"expected_version": 1})
        assert stale.status_code == 409 and len(calls) == 2
        ordinary = store.create_draft("text", "", "Ohne Maxim", "Text", [], "", "")
        assert client.post(f"/drafts/{ordinary['id']}/style-polish", headers=token,
                           json={"expected_version": 1}).status_code == 422


def test_polish_rejects_cloud_provider_before_call(tmp_path, monkeypatch):
    calls = []

    async def generate(*args):
        calls.append(args)
        return Completion("Text", 1, 1, 0, "reported")

    monkeypatch.setattr("jarvis.app.generate_text", generate)
    settings = Settings(JARVIS_DATA_DIR=tmp_path, JARVIS_PROVIDER="openrouter",
                        JARVIS_OPENROUTER_API_KEY="fixture", JARVIS_OPENROUTER_MODEL="fixture")
    with TestClient(create_app(settings)) as client:
        token = csrf(client)
        store = DraftStore(Database(tmp_path / "jarvis.db"), tmp_path)
        profile = store.set_named_profile("Maxim", "Kurz.", "cloud_allowed", 0)
        draft = store.create_draft("text", "", "Text", "Hallo", [], "", "Kurz.",
                                   named_profile=profile)
        result = client.post(f"/drafts/{draft['id']}/style-polish", headers=token,
                             json={"expected_version": 1})
        assert result.status_code == 422 and not calls

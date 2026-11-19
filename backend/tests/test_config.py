import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from jarvis.app import create_app
from jarvis.config import Settings, default_data_dir
from jarvis.db import Database


def test_empty_data_home_environment_uses_home_fallback(monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", "")
    monkeypatch.setenv("XDG_DATA_HOME", "")
    expected = (Path.home() / "AppData" / "Local" / "JarvisLocal" if sys.platform == "win32"
                else Path.home() / ".local" / "share" / "jarvis-local")
    assert default_data_dir() == expected


def test_openrouter_key_is_hidden_from_settings_repr(tmp_path):
    marker = "fixture-secret-that-must-not-be-logged"
    config = Settings(JARVIS_DATA_DIR=tmp_path, JARVIS_OPENROUTER_API_KEY=marker)
    assert config.openrouter_api_key == marker
    assert marker not in repr(config)


def test_personal_style_label_is_generic_and_validated(tmp_path):
    assert Settings(_env_file=None, JARVIS_DATA_DIR=tmp_path).personal_style_label == "Mein Stil"
    for invalid in ("", " Zu breit ", "Mit\nUmbruch", "x" * 61):
        with pytest.raises(ValidationError):
            Settings(_env_file=None, JARVIS_DATA_DIR=tmp_path, JARVIS_PERSONAL_STYLE_LABEL=invalid)


@pytest.mark.parametrize("value", ["", ".", "relative/data"])
def test_data_directory_must_be_absolute(value):
    with pytest.raises(ValidationError, match="absoluter Pfad"):
        Settings(JARVIS_DATA_DIR=value)


@pytest.mark.parametrize(("key", "value"), [
    ("timezone", "Mars/Colony"),
    ("daily_budget_usd", "nan"),
    ("provider", "unbekannt"),
    ("openrouter_model", "beispiel:online"),
    ("max_output_tokens", "0"),
])
def test_invalid_persisted_settings_fail_at_startup(tmp_path, key, value):
    Database(tmp_path / "jarvis.db").set_settings({key: value})
    with pytest.raises(RuntimeError, match="Gespeicherte Jarvis-Einstellungen"):
        create_app(Settings(JARVIS_DATA_DIR=tmp_path))


def test_valid_persisted_numeric_settings_are_typed(tmp_path):
    Database(tmp_path / "jarvis.db").set_settings({
        "daily_budget_usd": "1.25", "max_output_tokens": "256", "timezone": "UTC",
    })
    app = create_app(Settings(JARVIS_DATA_DIR=tmp_path))
    with TestClient(app) as client:
        client.get("/session")
        values = client.get("/settings").json()
        assert values["daily_budget_usd"] == 1.25
        assert values["max_output_tokens"] == 256
        assert values["timezone"] == "UTC"

"""Task-specific models retain the chosen provider, approvals and usage evidence."""

from collections.abc import AsyncIterator

import pytest
from fastapi.testclient import TestClient

from jarvis.app import create_app
from jarvis.config import Settings
from jarvis.db import Database
from jarvis.generation import generate_text
from jarvis.model_routing import choice, settings_for, validate_model_routes
from jarvis.provider import Chunk, Completion, OpenRouterAdapter, ProviderAdapter


class Reply(ProviderAdapter):
    async def stream(self, messages: list[dict[str, str]]) -> AsyncIterator[Chunk]:
        yield Chunk("text", text="Antwort")
        yield Chunk("usage", input_tokens=12, output_tokens=2, cost_usd=0,
                    cost_kind="local_no_api_charge")


def test_routes_validate_and_choose_without_provider_change(tmp_path):
    settings = Settings(JARVIS_DATA_DIR=tmp_path, JARVIS_OLLAMA_MODEL="default",
                        JARVIS_OPENROUTER_MODEL="vendor/default",
                        JARVIS_MODEL_ROUTES={"ollama": {"chat": "chat-local",
                                                         "research": "research-local"},
                                             "openrouter": {"chat": "vendor/chat"}})
    assert choice(settings, "chat") == ("chat-local", "Aufgabenregel")
    assert choice(settings, "research_queries") == ("research-local", "Aufgabenregel")
    assert choice(settings, "plan") == ("default", "Standardmodell")
    assert choice(settings, "chat_suggestion") == ("default", "Standardmodell")
    selected = settings_for(settings, "chat")
    assert selected.provider == "ollama" and selected.ollama_model == "chat-local"
    assert settings.ollama_model == "default"
    settings.provider = "openrouter"
    assert choice(settings, "chat") == ("vendor/chat", "Aufgabenregel")
    assert settings_for(settings, "draft").openrouter_model == "vendor/default"
    for invalid in ({"foreign": {"chat": "a"}}, {"ollama": {"unknown": "a"}},
                    {"ollama": {"chat": " a"}},
                    {"openrouter": {"chat": "vendor/model:online"}}):
        with pytest.raises(ValueError):
            validate_model_routes(invalid)


def test_route_settings_persist_and_reject_invalid_values(tmp_path):
    config = Settings(JARVIS_DATA_DIR=tmp_path, JARVIS_OLLAMA_MODEL="default",
                      JARVIS_OPENROUTER_MODEL="vendor/default")
    with TestClient(create_app(config)) as client:
        csrf = client.get("/session").json()["csrf"]
        headers = {"x-jarvis-csrf": csrf}
        routes = {"ollama": {"chat": "local-chat", "plan": "local-plan"},
                  "openrouter": {"draft": "vendor/draft"}}
        changed = client.patch("/settings", headers=headers, json={"model_routes": routes})
        assert changed.status_code == 200
        assert changed.json()["effective_models"]["chat"] == {
            "model": "local-chat", "reason": "Aufgabenregel"}
        assert changed.json()["model"] == "local-chat"
        assert changed.json()["effective_models"]["research"] == {
            "model": "default", "reason": "Standardmodell"}
        assert client.get("/health").json()["model"] == "local-chat"
        for invalid in ({"openrouter": {"chat": "vendor/model:online"}},
                        {"ollama": {"unknown": "a"}},
                        {"ollama": {"chat": " trailing "}}):
            assert client.patch("/settings", headers=headers,
                                json={"model_routes": invalid}).status_code == 422
        assert client.get("/settings").json()["model_routes"] == routes
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=tmp_path,
                                        JARVIS_OLLAMA_MODEL="default"))) as restarted:
        restarted.get("/session")
        assert restarted.get("/settings").json()["model_routes"] == routes


@pytest.mark.asyncio
async def test_generation_receipt_uses_selected_model(tmp_path, monkeypatch):
    seen = []

    def provider(config):
        seen.append((config.provider, config.ollama_model))
        return Reply()

    monkeypatch.setattr("jarvis.generation.make_provider", provider)
    db = Database(tmp_path / "jarvis.db")
    settings = Settings(JARVIS_DATA_DIR=tmp_path, JARVIS_OLLAMA_MODEL="default",
                        JARVIS_MODEL_ROUTES={"ollama": {"draft": "writer",
                                                         "research": "reader"}})
    for purpose in ("draft", "plan", "research_queries", "research"):
        await generate_text(db, settings, purpose, [{"role": "user", "content": "Hallo"}])
    assert seen == [("ollama", "writer"), ("ollama", "default"),
                    ("ollama", "reader"), ("ollama", "reader")]
    with db.connect() as connection:
        rows = connection.execute("SELECT purpose,model FROM model_usage ORDER BY created_at,id").fetchall()
    assert [(row["purpose"], row["model"]) for row in rows] == [
        ("draft", "writer"), ("plan", "default"),
        ("research_queries", "reader"), ("research", "reader")]


def test_chat_stream_receipt_uses_route(tmp_path, monkeypatch):
    seen = []

    def provider(config):
        seen.append((config.provider, config.ollama_model))
        return Reply()

    monkeypatch.setattr("jarvis.app.make_provider", provider)
    config = Settings(JARVIS_DATA_DIR=tmp_path, JARVIS_OLLAMA_MODEL="default",
                      JARVIS_MODEL_ROUTES={"ollama": {"chat": "local-chat"}})
    with TestClient(create_app(config)) as client:
        csrf = client.get("/session").json()["csrf"]
        headers = {"x-jarvis-csrf": csrf}
        chat = client.post("/chats", headers=headers).json()
        response = client.post(f"/chats/{chat['id']}/stream", headers=headers,
                               json={"content": "Hallo"})
        assert response.status_code == 200 and "Antwort" in response.text
        assert seen == [("ollama", "local-chat")]
        usage = client.get("/usage").json()
        assert usage[0]["provider"] == "ollama" and usage[0]["model"] == "local-chat"


@pytest.mark.asyncio
async def test_openrouter_route_checks_price_and_records_actual_model(tmp_path, monkeypatch):
    called = []

    class PricedReply(OpenRouterAdapter):
        async def load_pricing(self):
            called.append(("price", self.model))

        def upper_bound_cost(self, messages):
            called.append(("reserve", self.model))
            return 0.05

        async def complete(self, messages):
            called.append(("complete", self.model))
            return Completion("Antwort", 5, 2, 0.01, "reported")

    monkeypatch.setattr("jarvis.generation.make_provider",
                        lambda config: PricedReply("fixture", config.openrouter_model))
    settings = Settings(JARVIS_DATA_DIR=tmp_path, JARVIS_PROVIDER="openrouter",
                        JARVIS_OPENROUTER_MODEL="vendor/default",
                        JARVIS_MODEL_ROUTES={"openrouter": {"draft": "vendor/writer"}})
    db = Database(tmp_path / "jarvis.db")
    await generate_text(db, settings, "draft", [{"role": "user", "content": "Text"}])
    assert called == [(step, "vendor/writer") for step in ("price", "reserve", "complete")]
    with db.connect() as connection:
        row = connection.execute("SELECT provider,model,cost_usd,cost_kind FROM model_usage").fetchone()
    assert (row["provider"], row["model"], row["cost_usd"], row["cost_kind"]) == (
        "openrouter", "vendor/writer", 0.01, "reported")

import asyncio
from collections.abc import AsyncIterator

from fastapi.testclient import TestClient

from jarvis.app import create_app
from jarvis.config import Settings
from jarvis.provider import Chunk, OllamaAdapter, ProviderAdapter, ProviderError


class FakeProvider(ProviderAdapter):
    async def stream(self, messages: list[dict[str, str]]) -> AsyncIterator[Chunk]:
        assert messages[0]["role"] == "system"
        assert "Deutsch" in messages[0]["content"]
        assert messages[-1] == {"role": "user", "content": "Hallo"}
        yield Chunk("text", text="Guten ")
        yield Chunk("text", text="Tag")
        yield Chunk("usage", input_tokens=12, output_tokens=3, cost_usd=0,
                    cost_kind="local_no_api_charge")


class BrokenProvider(ProviderAdapter):
    async def stream(self, messages: list[dict[str, str]]) -> AsyncIterator[Chunk]:
        yield Chunk("text", text="Teil")
        raise ProviderError("rate_limited", "Rate-Limit erreicht")


class UnexpectedFailureProvider(ProviderAdapter):
    async def stream(self, messages: list[dict[str, str]]) -> AsyncIterator[Chunk]:
        yield Chunk("text", text="Teil")
        raise RuntimeError("geheimer interner Fehler")


def settings(tmp_path, provider="ollama"):
    return Settings(JARVIS_DATA_DIR=tmp_path, JARVIS_PROVIDER=provider,
                    JARVIS_OLLAMA_MODEL="fixture", JARVIS_OPENROUTER_MODEL="fixture")


def session(client):
    token = client.get("/session").json()["csrf"]
    return {"x-jarvis-csrf": token}


def test_local_auth_and_origin(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/chats").status_code == 401
        headers = session(client)
        assert client.post("/chats").status_code == 403
        assert client.post("/chats", headers=headers).status_code == 200
        assert client.get("/chats", headers={"origin": "https://attacker.example"}).status_code == 403
        assert client.get("/health", headers={"host": "attacker.example"}).status_code == 403
        preflight = client.options("/style/profile", headers={
            "origin": "http://127.0.0.1:5173", "access-control-request-method": "PUT",
        })
        assert preflight.status_code == 200
        assert "PUT" in preflight.headers["access-control-allow-methods"]


def test_settings_rejects_online_model_variant_before_any_request(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        headers = session(client)
        response = client.patch("/settings", headers=headers,
                                json={"openrouter_model": "example/model:online"})
        assert response.status_code == 422
        assert client.get("/settings").json()["openrouter_model"] == "fixture"


def test_health_explains_online_variant_from_environment(tmp_path):
    config = Settings(JARVIS_DATA_DIR=tmp_path, JARVIS_PROVIDER="openrouter",
                      JARVIS_OPENROUTER_API_KEY="fixture",
                      JARVIS_OPENROUTER_MODEL="example/model:online")
    with TestClient(create_app(config)) as client:
        health = client.get("/health").json()
        assert health["status"] == "setup_required"
        assert health["configured"] is False
        assert "Suchkosten" in health["message"]


def test_health_keeps_provider_snapshot_during_availability_check(tmp_path, monkeypatch):
    config = settings(tmp_path)

    async def switch_during_availability(self):
        config.provider = "openrouter"
        config.openrouter_model = "anderes-modell"
        return True

    monkeypatch.setattr(OllamaAdapter, "available", switch_during_availability)
    with TestClient(create_app(config)) as client:
        result = client.get("/health").json()
        assert result["provider"] == "ollama"
        assert result["model"] == "fixture"
        assert result["configured"] is True


def test_stream_and_reload_preserve_history(tmp_path, monkeypatch):
    monkeypatch.setattr("jarvis.app.make_provider", lambda config: FakeProvider())
    with TestClient(create_app(settings(tmp_path))) as client:
        headers = session(client)
        chat_id = client.post("/chats", headers=headers).json()["id"]
        response = client.post(f"/chats/{chat_id}/stream", json={"content": "Hallo"}, headers=headers)
        assert response.status_code == 200
        assert "event: delta" in response.text
        assert "event: done" in response.text
    with TestClient(create_app(settings(tmp_path))) as restarted:
        session(restarted)
        messages = restarted.get(f"/chats/{chat_id}").json()["messages"]
        assert [item["content"] for item in messages] == ["Hallo", "Guten Tag"]
        assert messages[1]["input_tokens"] == 12
        assert messages[1]["cost_kind"] == "local_no_api_charge"
        receipts = restarted.get("/usage").json()
        assert len(receipts) == 1
        assert receipts[0]["purpose"] == "chat"
        assert receipts[0]["provider"] == "ollama"
        assert receipts[0]["input_tokens"] == 12
        assert receipts[0]["cost_usd"] == 0


def test_chat_usage_keeps_original_provider_if_settings_change_mid_request(tmp_path, monkeypatch):
    config = settings(tmp_path)

    async def switch_during_availability(self):
        config.provider = "openrouter"
        config.openrouter_model = "anderes-modell"
        return True

    async def fixture_stream(self, messages):
        yield Chunk("text", text="Hallo")
        yield Chunk("usage", input_tokens=1, output_tokens=1, cost_usd=0,
                    cost_kind="local_no_api_charge")

    monkeypatch.setattr(OllamaAdapter, "available", switch_during_availability)
    monkeypatch.setattr(OllamaAdapter, "stream", fixture_stream)
    with TestClient(create_app(config)) as client:
        headers = session(client)
        chat_id = client.post("/chats", headers=headers).json()["id"]
        response = client.post(f"/chats/{chat_id}/stream", json={"content": "Hallo"}, headers=headers)
        assert response.status_code == 200
        message = client.get(f"/chats/{chat_id}").json()["messages"][-1]
        assert message["provider"] == "ollama" and message["model"] == "fixture"
        with client.app.state.db.connect() as db:
            usage = db.execute("SELECT provider,model FROM model_usage").fetchone()
            assert usage["provider"] == "ollama" and usage["model"] == "fixture"


def test_provider_failure_keeps_partial_status(tmp_path, monkeypatch):
    monkeypatch.setattr("jarvis.app.make_provider", lambda config: BrokenProvider())
    with TestClient(create_app(settings(tmp_path))) as client:
        headers = session(client)
        chat_id = client.post("/chats", headers=headers).json()["id"]
        response = client.post(f"/chats/{chat_id}/stream", json={"content": "Hallo"}, headers=headers)
        assert "event: error" in response.text
        assert "rate_limited" in response.text
        messages = client.get(f"/chats/{chat_id}").json()["messages"]
        assert messages[-1]["status"] == "failed"
        assert messages[-1]["content"] == "Teil"


def test_unexpected_provider_failure_is_sanitized_and_saved(tmp_path, monkeypatch):
    monkeypatch.setattr("jarvis.app.make_provider", lambda config: UnexpectedFailureProvider())
    with TestClient(create_app(settings(tmp_path))) as client:
        headers = session(client)
        chat_id = client.post("/chats", headers=headers).json()["id"]
        response = client.post(f"/chats/{chat_id}/stream", json={"content": "Hallo"}, headers=headers)
        assert response.status_code == 200
        assert "provider_error" in response.text
        assert "geheimer interner Fehler" not in response.text
        messages = client.get(f"/chats/{chat_id}").json()["messages"]
        assert len(messages) == 2
        assert messages[-1]["status"] == "failed"
        assert messages[-1]["content"] == "Teil"


def test_cancel_after_completed_answer_does_not_duplicate_message(tmp_path, monkeypatch):
    from jarvis.app import event as original_event

    monkeypatch.setattr("jarvis.app.make_provider", lambda config: FakeProvider())

    def cancel_on_done(name: str, payload: dict) -> str:
        if name == "done":
            raise asyncio.CancelledError
        return original_event(name, payload)

    monkeypatch.setattr("jarvis.app.event", cancel_on_done)
    with TestClient(create_app(settings(tmp_path)), raise_server_exceptions=False) as client:
        headers = session(client)
        chat_id = client.post("/chats", headers=headers).json()["id"]
        client.post(f"/chats/{chat_id}/stream", json={"content": "Hallo"}, headers=headers)
        messages = client.get(f"/chats/{chat_id}").json()["messages"]
        assert [item["content"] for item in messages] == ["Hallo", "Guten Tag"]
        assert messages[-1]["status"] == "complete"


def test_openrouter_missing_key_is_setup_error(tmp_path):
    with TestClient(create_app(settings(tmp_path, provider="openrouter"))) as client:
        headers = session(client)
        chat_id = client.post("/chats", headers=headers).json()["id"]
        response = client.post(f"/chats/{chat_id}/stream", json={"content": "Hallo", "allow_cloud": True}, headers=headers)
        assert response.status_code == 503
        assert client.get(f"/chats/{chat_id}").json()["messages"] == []


def test_cloud_chat_requires_explicit_history_consent(tmp_path):
    with TestClient(create_app(settings(tmp_path, provider="openrouter"))) as client:
        headers = session(client)
        chat_id = client.post("/chats", headers=headers).json()["id"]
        response = client.post(f"/chats/{chat_id}/stream", json={"content": "Privat"}, headers=headers)
        assert response.status_code == 422
        assert client.get(f"/chats/{chat_id}").json()["messages"] == []


def test_settings_persist_without_exposing_secret(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        headers = session(client)
        response = client.patch("/settings", json={"daily_budget_usd": 2.5, "timezone": "Europe/Paris",
                                                   "personal_style_label": "Eigener Stil"}, headers=headers)
        assert response.status_code == 200
        assert response.json()["daily_budget_usd"] == 2.5
        assert "api_key" not in response.text
        assert response.json()["personal_style_label"] == "Eigener Stil"
        assert client.patch("/settings", json={"timezone": "Not/AZone"}, headers=headers).status_code == 422
        assert client.patch("/settings", json={"personal_style_label": " Ungültig "},
                            headers=headers).status_code == 422
    with TestClient(create_app(settings(tmp_path))) as restarted:
        session(restarted)
        view = restarted.get("/settings").json()
        assert view["daily_budget_usd"] == 2.5
        assert view["timezone"] == "Europe/Paris"
        assert view["personal_style_label"] == "Eigener Stil"

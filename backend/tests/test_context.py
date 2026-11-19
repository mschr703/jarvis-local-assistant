from collections.abc import AsyncIterator

from fastapi.testclient import TestClient

from jarvis.app import create_app
from jarvis.config import Settings
from jarvis.provider import Chunk, ProviderAdapter


class CaptureProvider(ProviderAdapter):
    def __init__(self):
        self.messages = None

    async def stream(self, messages: list[dict[str, str]]) -> AsyncIterator[Chunk]:
        self.messages = messages
        yield Chunk("text", text="OK")
        yield Chunk("usage", input_tokens=2, output_tokens=1, cost_usd=0,
                    cost_kind="local_no_api_charge")


def test_chat_context_trims_old_history_and_rejects_oversize_input(tmp_path, monkeypatch):
    provider = CaptureProvider()
    monkeypatch.setattr("jarvis.app.make_provider", lambda config: provider)
    settings = Settings(JARVIS_DATA_DIR=tmp_path, JARVIS_MAX_CONTEXT_CHARS=4000)
    app = create_app(settings)
    with TestClient(app) as client:
        token = client.get("/session").json()["csrf"]
        headers = {"x-jarvis-csrf": token}
        chat = client.post("/chats", headers=headers).json()
        app.state.db.add_message(chat["id"], "user", "A" * 2000)
        app.state.db.add_message(chat["id"], "assistant", "B" * 2000)
        response = client.post(f"/chats/{chat['id']}/stream", headers=headers,
                               json={"content": "C" * 1800})
        assert response.status_code == 200
        assert provider.messages[0]["role"] == "system"
        assert provider.messages[1:] == [{"role": "user", "content": "C" * 1800}]
        rejected = client.post(f"/chats/{chat['id']}/stream", headers=headers,
                               json={"content": "D" * 3600})
        assert rejected.status_code == 422
        assert len(client.get(f"/chats/{chat['id']}").json()["messages"]) == 4


def test_draft_context_limit_fails_before_model_call(tmp_path):
    settings = Settings(JARVIS_DATA_DIR=tmp_path, JARVIS_MAX_CONTEXT_CHARS=4000)
    with TestClient(create_app(settings)) as client:
        token = client.get("/session").json()["csrf"]
        response = client.post("/drafts/generate", headers={"x-jarvis-csrf": token}, json={
            "kind": "text", "brief": "Schreibe", "input_text": "X" * 5000,
        })
        assert response.status_code == 422
        assert "Kontextlimit" in response.json()["detail"]

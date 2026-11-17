"""Echter lokaler Chat-Smoke-Test gegen einen laufenden Jarvis-Backendprozess."""

import json

import httpx


def main() -> None:
    with httpx.Client(base_url="http://127.0.0.1:8765", timeout=180) as client:
        health = client.get("/health").json()
        if health["provider"] != "ollama" or not health["configured"]:
            raise RuntimeError(f"Ollama ist nicht bereit: {health['message']}")
        token = client.get("/session").json()["csrf"]
        headers = {"X-Jarvis-CSRF": token}
        chat_id = client.post("/chats", headers=headers).json()["id"]
        chunks: list[str] = []
        done: dict | None = None
        with client.stream("POST", f"/chats/{chat_id}/stream", headers=headers,
                           json={"content": "Antworte in einem kurzen deutschen Satz: Was ist zwei plus zwei?"}) as response:
            response.raise_for_status()
            current_event = ""
            for line in response.iter_lines():
                if line.startswith("event: "):
                    current_event = line[7:]
                elif line.startswith("data: "):
                    payload = json.loads(line[6:])
                    if current_event == "delta":
                        chunks.append(payload["text"])
                    elif current_event == "done":
                        done = payload["message"]
                    elif current_event == "error":
                        raise RuntimeError(payload)
        if not chunks or done is None:
            raise RuntimeError("Leere oder unvollständige Modellantwort")
        stored = client.get(f"/chats/{chat_id}").json()["messages"]
        assert stored[-1]["content"] == "".join(chunks)
        assert stored[-1]["status"] == "complete"
        print(json.dumps({"chat_id": chat_id, "text": "".join(chunks),
                          "input_tokens": done["input_tokens"],
                          "output_tokens": done["output_tokens"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()

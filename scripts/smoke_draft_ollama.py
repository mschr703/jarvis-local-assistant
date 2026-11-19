"""Prüft Dokumentensuche, belegte Antwort und lokalen Export mit echtem Ollama."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx


def main() -> None:
    with TemporaryDirectory(prefix="jarvis-source-") as directory:
        source = Path(directory) / "entscheidung.txt"
        source.write_text("Projekt Atlas: Die Präsentation findet am 12. November statt."
                          " Verantwortlich ist Maria.", encoding="utf-8")
        with httpx.Client(base_url="http://127.0.0.1:8765", timeout=180) as client:
            health = client.get("/health").json()
            assert health["configured"] and health["provider"] == "ollama", health
            token = client.get("/session").json()["csrf"]
            headers = {"X-Jarvis-CSRF": token}
            folder = client.post("/folders", headers=headers,
                                 json={"path": directory, "policy": "local_only"})
            folder.raise_for_status()
            indexed = client.post(f"/folders/{folder.json()['id']}/index", headers=headers)
            indexed.raise_for_status()
            assert indexed.json()["indexed"] == 1
            hits = client.get("/search", params={"q": "Präsentation"}).json()
            assert hits
            response = client.post("/drafts/generate", headers=headers, json={
                "kind": "answer", "brief": "Wann ist die Präsentation und wer ist verantwortlich?",
                "passage_ids": [hits[0]["passage_id"]],
            })
            response.raise_for_status()
            draft = response.json()
            assert "12" in draft["content"] and "Maria" in draft["content"], draft["content"]
            assert "[1]" in draft["content"], draft["content"]
            assert draft["sources"][0]["path"] == hits[0]["path"]
            assert draft["sources"][0]["sha256"] == hits[0]["sha256"]
            exported = client.post(f"/drafts/{draft['id']}/export", headers=headers,
                                   json={"format": "md", "expected_version": draft["version"]})
            exported.raise_for_status()
            target = Path(exported.json()["path"])
            assert target.is_file() and target.read_text(encoding="utf-8").strip() == draft["content"].strip()
            client.delete(f"/folders/{folder.json()['id']}", headers=headers).raise_for_status()
            print(json.dumps({"draft_id": draft["id"], "answer": draft["content"],
                              "source": hits[0]["path"], "export": str(target)}, ensure_ascii=False))


if __name__ == "__main__":
    main()

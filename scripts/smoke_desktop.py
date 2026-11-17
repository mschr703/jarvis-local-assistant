"""Echte API-Proben für öffentliche Browserseite und Windows-Anwendung."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import httpx


def main() -> None:
    with httpx.Client(base_url="http://127.0.0.1:8765", timeout=90) as client:
        token = client.get("/session").json()["csrf"]
        headers = {"X-Jarvis-CSRF": token}
        capabilities = {item["name"] for item in client.get("/tools").json()}
        assert {"open_app", "open_file", "browser_read"} <= capabilities
        rejected = client.post("/runs", headers=headers, json={
            "tool_name": "browser_read", "args": {"url": "http://127.0.0.1:8765/health"},
            "client_request_id": str(uuid4()),
        })
        rejected.raise_for_status()
        assert rejected.json()["status"] == "failed", rejected.json()
        response = client.post("/runs", headers=headers, json={
            "tool_name": "browser_read", "args": {"url": "https://example.com/"},
            "client_request_id": str(uuid4()),
        })
        response.raise_for_status()
        browser = response.json()
        assert browser["status"] == "succeeded", browser
        content = json.loads(browser["result_json"])
        assert content["title"] == "Example Domain" and "documentation examples" in content["text"], content
        redirect = client.post("/runs", headers=headers, json={
            "tool_name": "browser_read",
            "args": {"url": "https://httpbin.org/redirect-to?url=http%3A%2F%2F127.0.0.1%2F"},
            "client_request_id": str(uuid4()),
        })
        redirect.raise_for_status()
        assert redirect.json()["status"] == "failed", redirect.json()
        app = client.post("/runs", headers=headers, json={
            "tool_name": "open_app", "args": {"app_id": "calculator"},
            "client_request_id": str(uuid4()),
        })
        app.raise_for_status()
        assert app.json()["status"] == "succeeded", app.json()
        with TemporaryDirectory(prefix="jarvis-open-file-") as directory:
            Path(directory, "freigabe.txt").write_text("Jarvis Desktop-Probe", encoding="utf-8")
            folder = client.post("/folders", headers=headers,
                                 json={"path": directory, "policy": "local_only"})
            folder.raise_for_status()
            client.post(f"/folders/{folder.json()['id']}/index", headers=headers).raise_for_status()
            document = next(item for item in client.get("/documents").json()
                            if item["folder_id"] == folder.json()["id"])
            opened = client.post("/runs", headers=headers, json={
                "tool_name": "open_file", "args": {"file_id": document["id"]},
                "client_request_id": str(uuid4()),
            })
            opened.raise_for_status()
            assert opened.json()["status"] == "succeeded", opened.json()
            client.delete(f"/folders/{folder.json()['id']}", headers=headers).raise_for_status()
        print(json.dumps({"private_url_status": rejected.json()["status"],
                          "private_redirect_status": redirect.json()["status"],
                          "browser_title": content["title"], "browser_url": content["url"],
                          "app_result": json.loads(app.json()["result_json"]),
                          "file_status": opened.json()["status"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()

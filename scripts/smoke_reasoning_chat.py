"""Check free-form local reasoning, a correction, a topic switch and uncertainty."""

import argparse
import re
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from jarvis.app import create_app
from jarvis.config import Settings


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="qwen3:4b-instruct")
    parser.add_argument("--only", choices=("all", "concept"), default="all")
    args = parser.parse_args()
    cases = [
        (("Angenommen, ich muss um 14:00 ankommen. Die Fahrt dauert 20 Minuten "
          "und ich will 10 Minuten Puffer. Wann sollte ich losfahren?"), "13:30"),
        (("Korrektur: Ich will 15 Minuten Puffer statt 10. Wann sollte ich jetzt "
          "losfahren?"), "13:25"),
        (("Was ist der Unterschied zwischen einer Annahme und einer bestätigten "
          "Tatsache? Erklär es an meinem Beispiel mit dem Puffer."), None),
        (("Neues Thema: Eine Freundin schreibt nur 'Antwort offen'. Weißt du "
          "damit, wer schreiben muss?"), None),
    ]
    with TemporaryDirectory(prefix="jarvis-reasoning-") as temp:
        app = create_app(Settings(JARVIS_DATA_DIR=Path(temp), JARVIS_OLLAMA_MODEL=args.model,
                                  JARVIS_MAX_OUTPUT_TOKENS=320))
        with TestClient(app) as client:
            headers = {"x-jarvis-csrf": client.get("/session").json()["csrf"]}
            chat_id = client.post("/chats", headers=headers).json()["id"]
            if args.only == "concept":
                for question, answer in ((cases[0][0], "Du solltest um 13:30 losfahren."),
                                         (cases[1][0], "Du solltest um 13:25 losfahren.")):
                    app.state.db.add_message(chat_id, "user", question)
                    app.state.db.add_message(chat_id, "assistant", answer)
            for index, (question, expected_time) in enumerate(cases):
                if args.only == "concept" and index != 2:
                    continue
                started = time.monotonic()
                response = client.post(f"/chats/{chat_id}/stream", headers=headers,
                                       json={"content": question, "use_local_tools": True})
                assert response.status_code == 200 and "event: done" in response.text, response.text
                answer = client.get(f"/chats/{chat_id}").json()["messages"][-1]
                text = answer["content"]
                print(f"{time.monotonic() - started:.1f}s | {question}\n{text}", flush=True)
                assert answer["tool_uses"] == [], answer["tool_uses"]
                if expected_time:
                    assert expected_time in text or expected_time.replace(":", ".") in text, text
                if index == 2:
                    folded = text.casefold()
                    assert "puffer" in folded and "annahme" in folded, text
                    assert any(word in folded for word in ("wunsch", "plan", "präferenz")), text
                    assert not re.search(
                        r"15 minuten puffer.{0,160}(?:das ist eine|ist eine) \*{0,2}annahme",
                        folded, re.DOTALL), text
                if index == 3:
                    folded = text.casefold()
                    assert any(phrase in folded for phrase in (
                        "nicht klar", "nicht ersichtlich", "nicht eindeutig",
                        "nicht angegeben", "nicht ableiten", "nicht wissen",
                        "lässt sich nicht", "kann man nicht", "ohne kontext",
                        "nicht mit sicherheit")), text
                    assert "du musst schreiben" not in folded, text


if __name__ == "__main__":
    main()

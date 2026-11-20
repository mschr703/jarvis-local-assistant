"""Compare a local model's final answer with optional Ollama thinking enabled."""

import argparse
import sys
import time

import httpx


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="qwen3:4b")
    parser.add_argument("--think", action="store_true")
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()
    if not 1 <= args.timeout <= 600:
        parser.error("--timeout muss zwischen 1 und 600 Sekunden liegen")
    messages = [
        {"role": "system", "content": (
            "Antworte auf Deutsch. Unterscheide einen geäußerten Wunsch oder Plan "
            "von einer Annahme über die Realität und von einer belegten Tatsache. "
            "Nenne nur die Schlussantwort, keine internen Denkschritte.")},
        {"role": "user", "content": (
            "Ich muss um 14:00 ankommen. Die Fahrt dauert 20 Minuten und ich will "
            "10 Minuten Puffer. Wann los?")},
        {"role": "assistant", "content": "13:30 Uhr."},
        {"role": "user", "content": "Korrektur: Ich will 15 Minuten Puffer. Wann los?"},
        {"role": "assistant", "content": "13:25 Uhr."},
        {"role": "user", "content": (
            "Was ist der Unterschied zwischen einer Annahme und einer bestätigten "
            "Tatsache? Erklär es an meinem Beispiel mit dem Puffer.")},
    ]
    started = time.monotonic()
    try:
        with httpx.Client(timeout=httpx.Timeout(args.timeout, connect=5),
                          trust_env=False) as client:
            response = client.post("http://127.0.0.1:11434/api/chat", json={
                "model": args.model, "messages": messages, "stream": False,
                "think": args.think,
                "options": {"num_predict": 1024, "num_ctx": 8192, "temperature": 0},
            })
            response.raise_for_status()
            result = response.json()
    except httpx.ReadTimeout:
        print({"model": args.model, "think": args.think,
               "wall_s": round(time.monotonic() - started, 1),
               "result": "timeout_without_final_answer"})
        raise SystemExit(1)
    message = result["message"]
    print({"model": args.model, "think": args.think,
           "wall_s": round(time.monotonic() - started, 1),
           "prompt_tokens": result.get("prompt_eval_count"),
           "output_tokens": result.get("eval_count"),
           "had_separate_thinking": bool(message.get("thinking"))})
    print(message.get("content", ""))


if __name__ == "__main__":
    main()

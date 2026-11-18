"""Real local Ollama probe: passage embeddings survive a Python process restart."""

import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory

from jarvis.semantic_search import embed_texts

OLLAMA_URL = "http://127.0.0.1:11434"


def passages() -> list[str]:
    return [f"Synthetischer Abschnitt {index}: Projekt Orion hat einen Zeitplan und Notizen."
            for index in range(100)]


def worker(path: Path, question: str) -> dict:
    started = time.perf_counter()
    vectors = embed_texts(OLLAMA_URL, [question, *passages()], path)
    fingerprint = hashlib.sha256(json.dumps(vectors[1:], separators=(",", ":")).encode()).hexdigest()
    return {"seconds": round(time.perf_counter() - started, 2),
            "passage_vectors_sha256": fingerprint}


def main() -> None:
    if len(sys.argv) == 4 and sys.argv[1] == "--worker":
        print(json.dumps(worker(Path(sys.argv[2]), sys.argv[3])))
        return
    with TemporaryDirectory(prefix="jarvis-semantic-persistence-") as temporary:
        cache_path = Path(temporary) / "semantic-embeddings.sqlite"
        runs = []
        for question in ("Wann ist der Termin?", "Welche Notizen gibt es?"):
            result = subprocess.run([sys.executable, __file__, "--worker",
                                     str(cache_path), question], check=True,
                                    capture_output=True, text=True, timeout=180)
            runs.append(json.loads(result.stdout))
        assert runs[0]["passage_vectors_sha256"] == runs[1]["passage_vectors_sha256"]
        print(json.dumps({"first_seconds": runs[0]["seconds"],
                          "new_process_seconds": runs[1]["seconds"],
                          "passage_vectors_equal": True,
                          "cache_bytes": cache_path.stat().st_size}))


if __name__ == "__main__":
    main()

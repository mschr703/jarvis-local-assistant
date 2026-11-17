"""Real local generation checks task routes for text, plan and research."""

import asyncio
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from jarvis.config import Settings
from jarvis.db import Database
from jarvis.generation import generate_text

ROUTED_MODEL = "qwen3:4b"


async def run() -> None:
    with TemporaryDirectory(prefix="jarvis-routed-local-") as temporary:
        settings = Settings(JARVIS_DATA_DIR=Path(temporary), JARVIS_PROVIDER="ollama",
                            JARVIS_OLLAMA_MODEL="qwen3:4b-instruct",
                            JARVIS_MODEL_ROUTES={"ollama": {"draft": ROUTED_MODEL,
                                                             "plan": ROUTED_MODEL,
                                                             "research": ROUTED_MODEL}},
                            JARVIS_MAX_OUTPUT_TOKENS=48)
        db = Database(settings.data_dir / "jarvis.db")
        for purpose in ("draft", "plan", "research_queries", "research"):
            completion = await generate_text(db, settings, purpose, [
                {"role": "system", "content": "Antworte mit einem kurzen deutschen Satz."},
                {"role": "user", "content": "Sag nur OK."},
            ])
            assert completion.text.strip()
        with db.connect() as connection:
            rows = connection.execute("SELECT purpose,provider,model,status,cost_kind "
                                      "FROM model_usage ORDER BY created_at,id").fetchall()
        assert [(row["purpose"], row["provider"], row["model"], row["status"])
                for row in rows] == [(purpose, "ollama", ROUTED_MODEL, "complete")
                                    for purpose in ("draft", "plan", "research_queries", "research")]
        assert all(row["cost_kind"] == "local_no_api_charge" for row in rows)
        print(json.dumps({"purposes": [row["purpose"] for row in rows],
                          "model": ROUTED_MODEL, "api_cost": 0}))


if __name__ == "__main__":
    asyncio.run(run())

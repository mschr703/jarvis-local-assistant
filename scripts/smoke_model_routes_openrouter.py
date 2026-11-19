"""Tiny synthetic OpenRouter call proving a task route uses priced, budgeted model."""

import asyncio
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from jarvis.config import Settings, default_data_dir
from jarvis.db import Database
from jarvis.generation import generate_text
from jarvis.provider import openrouter_api_key

ROUTED_MODEL = "openai/gpt-4o-mini"


async def run() -> None:
    key = openrouter_api_key(Settings(JARVIS_DATA_DIR=default_data_dir()))
    if not key:
        raise RuntimeError("OpenRouter-Schlüssel im Windows-Tresor oder als Prozessvariable nötig")
    with TemporaryDirectory(prefix="jarvis-routed-cloud-") as temporary:
        settings = Settings(JARVIS_DATA_DIR=Path(temporary), JARVIS_PROVIDER="openrouter",
                            JARVIS_OPENROUTER_API_KEY=key,
                            JARVIS_OPENROUTER_MODEL="invalid-default-not-for-request",
                            JARVIS_MODEL_ROUTES={"openrouter": {"draft": ROUTED_MODEL}},
                            JARVIS_MAX_OUTPUT_TOKENS=24, JARVIS_DAILY_BUDGET_USD=0.05)
        db = Database(settings.data_dir / "jarvis.db")
        completion = await generate_text(db, settings, "draft", [
            {"role": "system", "content": "Antworte mit einem sehr kurzen deutschen Satz."},
            {"role": "user", "content": "Schreibe nur: OK."},
        ])
        with db.connect() as connection:
            usage = connection.execute("SELECT provider,model,status,cost_usd,cost_kind "
                                       "FROM model_usage").fetchone()
        assert completion.text.strip()
        assert usage["provider"] == "openrouter" and usage["model"] == ROUTED_MODEL
        assert usage["status"] == "complete"
        print(json.dumps({"route": usage["model"], "status": usage["status"],
                          "cost_kind": usage["cost_kind"], "cost_usd": usage["cost_usd"]}))


if __name__ == "__main__":
    asyncio.run(run())

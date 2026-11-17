import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from pydantic import ValidationError

from jarvis.config import Settings
from jarvis.db import Database
from jarvis.generation import generate_text
from jarvis.provider import Completion, OpenRouterAdapter, ProviderAdapter, ProviderError


def test_budget_reservation_survives_interrupted_process(tmp_path):
    path = tmp_path / "jarvis.db"
    db = Database(path)
    since = "2026-10-24T00:00:00+00:00"
    first = db.reserve_usage("chat", "openrouter", "example/model", 0.7, 1.0, since)
    with pytest.raises(ValueError, match="Tagesbudget"):
        db.reserve_usage("draft", "openrouter", "example/model", 0.4, 1.0, since)
    db.finish_usage(first, 100, 10, 0.2, "estimated_from_current_list_price", "complete")
    second = db.reserve_usage("draft", "openrouter", "example/model", 0.4, 1.0, since)
    assert db.cost_since(since) == pytest.approx(0.6)
    db.reconcile_runs()
    assert db.cost_since(since) == pytest.approx(0.6)
    with db.connect() as connection:
        row = connection.execute("SELECT status,cost_kind FROM model_usage WHERE id=?", (second,)).fetchone()
        assert row["status"] == "interrupted"
        assert row["cost_kind"] == "upper_bound_after_interruption"


def test_parallel_paid_reservations_cannot_overspend_daily_budget(tmp_path):
    db = Database(tmp_path / "jarvis.db")
    since = "2026-10-24T00:00:00+00:00"
    barrier = threading.Barrier(2)

    def reserve():
        barrier.wait(timeout=5)
        try:
            return db.reserve_usage("chat", "openrouter", "example/model", 0.7, 1.0, since)
        except ValueError as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _index: reserve(), range(2)))
    assert sum("Tagesbudget" in result for result in results) == 1
    assert db.cost_since(since) == pytest.approx(0.7)


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), -1.0])
def test_invalid_cost_cannot_reduce_reserved_budget(tmp_path, invalid):
    db = Database(tmp_path / "jarvis.db")
    since = "2026-10-24T00:00:00+00:00"
    with pytest.raises(ValueError, match="Ungültige Kosten"):
        db.reserve_usage("chat", "openrouter", "example/model", invalid, 1.0, since)
    usage_id = db.reserve_usage("chat", "openrouter", "example/model", 0.5, 1.0, since)
    db.finish_usage(usage_id, 10, 5, invalid, "reported", "complete")
    assert db.cost_since(since) == pytest.approx(0.5)
    with pytest.raises(ValueError, match="Tagesbudget"):
        db.reserve_usage("chat", "openrouter", "example/model", 0.6, 1.0, since)


def test_nonfinite_daily_budget_is_rejected(tmp_path):
    db = Database(tmp_path / "jarvis.db")
    with pytest.raises(ValueError, match="Ungültige Kosten"):
        db.reserve_usage("chat", "openrouter", "example/model", 0.1,
                         float("inf"), "2026-10-24T00:00:00+00:00")


@pytest.mark.parametrize("value", ["Infinity", "NaN"])
def test_nonfinite_budget_environment_fails_at_startup(tmp_path, value):
    with pytest.raises(ValidationError):
        Settings(JARVIS_DATA_DIR=tmp_path, JARVIS_DAILY_BUDGET_USD=value)


def test_invalid_timezone_environment_fails_at_startup(tmp_path):
    with pytest.raises(ValidationError, match="IANA-Zeitzone"):
        Settings(JARVIS_DATA_DIR=tmp_path, JARVIS_TIMEZONE="Mars/Colony")


@pytest.mark.asyncio
async def test_generation_usage_keeps_paid_provider_if_settings_change_mid_request(tmp_path,
                                                                                     monkeypatch):
    settings = Settings(JARVIS_DATA_DIR=tmp_path, JARVIS_PROVIDER="openrouter",
                        JARVIS_OPENROUTER_MODEL="example/model")

    class SwitchingProvider(OpenRouterAdapter):
        async def load_pricing(self):
            settings.provider = "ollama"

        def upper_bound_cost(self, messages):
            return 0.1

        async def complete(self, messages):
            return Completion("Antwort", 10, 2, 0.03, "reported")

    monkeypatch.setattr("jarvis.generation.make_provider",
                        lambda config: SwitchingProvider("fixture", "example/model"))
    db = Database(tmp_path / "jarvis.db")
    result = await generate_text(db, settings, "draft", [{"role": "user", "content": "Hallo"}])
    assert result.text == "Antwort"
    with db.connect() as connection:
        usage = connection.execute("SELECT provider,model,cost_usd FROM model_usage").fetchone()
        assert usage["provider"] == "openrouter"
        assert usage["model"] == "example/model"
        assert usage["cost_usd"] == pytest.approx(0.03)


@pytest.mark.asyncio
@pytest.mark.parametrize(("failure", "expected_status"), [
    (ProviderError("unavailable", "Provider nicht erreichbar"), "failed"),
    (RuntimeError("Unerwarteter Fehler"), "failed"),
    (asyncio.CancelledError(), "interrupted"),
])
async def test_generation_usage_distinguishes_failure_from_interruption(
        tmp_path, monkeypatch, failure, expected_status):
    class FailingProvider(ProviderAdapter):
        async def complete(self, messages):
            raise failure

    monkeypatch.setattr("jarvis.generation.make_provider", lambda _config: FailingProvider())
    db = Database(tmp_path / "jarvis.db")
    settings = Settings(JARVIS_DATA_DIR=tmp_path)
    with pytest.raises(type(failure)):
        await generate_text(db, settings, "draft", [{"role": "user", "content": "Hallo"}])
    with db.connect() as connection:
        receipt = connection.execute("SELECT status,cost_usd FROM model_usage").fetchone()
    assert receipt["status"] == expected_status
    assert receipt["cost_usd"] == 0.0

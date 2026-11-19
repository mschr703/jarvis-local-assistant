"""Nicht streamende Textgenerierung mit persistenter Kostenreservierung."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from jarvis.config import Settings
from jarvis.db import Database
from jarvis.model_routing import settings_for
from jarvis.provider import (
    Completion,
    OllamaAdapter,
    OpenRouterAdapter,
    ProviderError,
    make_provider,
)


def day_start_utc(timezone_name: str) -> str:
    local = datetime.now(ZoneInfo(timezone_name))
    return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC).isoformat()


async def generate_text(db: Database, settings: Settings, purpose: str,
                        messages: list[dict[str, str]],
                        ollama_format: dict | str | None = None) -> Completion:
    settings = settings_for(settings, purpose)
    if sum(len(item["content"]) for item in messages) > settings.max_context_chars:
        raise ProviderError("context_limit", "Eingabetext überschreitet das Kontextlimit")
    provider_name = settings.provider
    model = settings.ollama_model if provider_name == "ollama" else settings.openrouter_model
    timezone_name = settings.timezone
    provider = make_provider(settings)
    if ollama_format is not None:
        if not isinstance(provider, OllamaAdapter):
            raise ValueError("Strukturierte lokale Ausgabe erfordert Ollama")
        provider.response_format = ollama_format
    if isinstance(provider, OllamaAdapter) and not await provider.available():
        raise ProviderError("unavailable", "Ollama starten und Modell installieren")
    reservation = 0.0
    if isinstance(provider, OpenRouterAdapter):
        await provider.load_pricing()
        reservation = provider.upper_bound_cost(messages)
    try:
        usage_id = db.reserve_usage(purpose, provider_name, model, reservation,
                                    settings.daily_budget_usd, day_start_utc(timezone_name))
    except ValueError as exc:
        raise ProviderError("budget_exceeded", str(exc)) from exc
    try:
        completion = await provider.complete(messages)
    except Exception:
        db.finish_usage(usage_id, None, None, None, None, "failed")
        raise
    except BaseException:
        db.finish_usage(usage_id, None, None, None, None, "interrupted")
        raise
    db.finish_usage(usage_id, completion.input_tokens, completion.output_tokens,
                    completion.cost_usd, completion.cost_kind, "complete")
    completion.usage_id = usage_id
    return completion

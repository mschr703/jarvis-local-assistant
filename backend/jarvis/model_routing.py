"""Explicit model rules within the selected provider; no implicit cloud fallback."""

from typing import Any

PROVIDERS = frozenset({"ollama", "openrouter"})
TASKS = frozenset({"chat", "draft", "plan", "research"})
PURPOSE_TASK = {"research_queries": "research"}


def validate_model_routes(value: Any) -> dict[str, dict[str, str]]:
    if not isinstance(value, dict) or set(value) - PROVIDERS:
        raise ValueError("Modellregeln brauchen Ollama oder OpenRouter")
    cleaned: dict[str, dict[str, str]] = {}
    for provider, routes in value.items():
        if not isinstance(routes, dict) or set(routes) - TASKS:
            raise ValueError("Unbekannter Aufgabentyp in Modellregeln")
        selected: dict[str, str] = {}
        for task, model in routes.items():
            if (not isinstance(model, str) or not 1 <= len(model) <= 120
                    or model != model.strip() or not model.isprintable()):
                raise ValueError("Modell-ID muss 1–120 druckbare Zeichen ohne Randleerraum haben")
            if provider == "openrouter" and model.lower().endswith(":online"):
                raise ValueError("OpenRouter-Onlinevariante mit zusätzlichen Suchkosten ist gesperrt")
            selected[task] = model
        if selected:
            cleaned[provider] = selected
    return cleaned


def choice(settings: Any, purpose: str) -> tuple[str | None, str]:
    """Return the effective model and why it was chosen, never a provider change."""
    provider = settings.provider
    task = PURPOSE_TASK.get(purpose, purpose)
    override = settings.model_routes.get(provider, {}).get(task) if task in TASKS else None
    default = settings.ollama_model if provider == "ollama" else settings.openrouter_model
    return (override, "Aufgabenregel") if override else (default, "Standardmodell")


def settings_for(settings: Any, purpose: str) -> Any:
    model, _reason = choice(settings, purpose)
    field = "ollama_model" if settings.provider == "ollama" else "openrouter_model"
    return settings.model_copy(update={field: model})

"""Austauschbare Modelladapter; kein Schlüssel gelangt an den Browser."""

import json
import math
from collections.abc import AsyncIterator
from dataclasses import dataclass
from decimal import Decimal, DecimalException, InvalidOperation
from typing import Literal
from urllib.parse import quote, urlsplit

import httpx

from jarvis.config import Settings
from jarvis.vault import CredentialVault

CHAT_SYSTEM_PROMPT = "Antworte direkt und auf Deutsch. Gib nur die Antwort aus."
OPENROUTER_REQUEST_TIMEOUT_MS = 180_000


def has_unbudgeted_online_search(model: str | None) -> bool:
    return bool(model and model.lower().endswith(":online"))


def openrouter_api_key(settings: Settings) -> str | None:
    """A process key takes precedence; the Windows vault is read for each new call."""
    if settings.openrouter_api_key:
        return settings.openrouter_api_key
    try:
        return CredentialVault(settings.data_dir).secret_for_identity(
            "openrouter", "default", "api_key")
    except (OSError, RuntimeError, ValueError) as exc:
        raise ProviderError("vault_unavailable", "OpenRouter-Schlüssel im Tresor nicht lesbar") from exc


def nonnegative_token_count(value: object) -> int | None:
    return value if type(value) is int and value >= 0 else None


@dataclass
class Chunk:
    kind: Literal["text", "usage"]
    text: str = ""
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None
    cost_kind: str | None = None


@dataclass
class Completion:
    text: str
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: float | None
    cost_kind: str | None
    usage_id: str | None = None


class ProviderError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class ProviderAdapter:
    async def stream(self, messages: list[dict[str, str]]) -> AsyncIterator[Chunk]:
        raise NotImplementedError

    async def complete(self, messages: list[dict[str, str]]) -> Completion:
        parts: list[str] = []
        usage: Chunk | None = None
        async for chunk in self.stream(messages):
            if chunk.kind == "text":
                parts.append(chunk.text)
            else:
                usage = chunk
        if not parts:
            raise ProviderError("empty_stream", "Modell lieferte keine Antwort")
        return Completion("".join(parts), usage.input_tokens if usage else None,
                          usage.output_tokens if usage else None,
                          usage.cost_usd if usage else None,
                          usage.cost_kind if usage else None)


class OllamaAdapter(ProviderAdapter):
    def __init__(self, url: str, model: str, max_output_tokens: int = 1024,
                 context_tokens: int = 8192):
        try:
            parsed = urlsplit(url)
            port = parsed.port
        except ValueError as exc:
            raise ValueError("Ollama-Adresse ist ungültig") from exc
        if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}
                or port is None or parsed.username is not None or parsed.password is not None
                or parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
            raise ValueError("Ollama muss an Loopback gebunden sein")
        self.url = f"http://127.0.0.1:{port}"
        self.model = model
        self.max_output_tokens = max_output_tokens
        self.context_tokens = context_tokens
        self.response_format: dict | str | None = None
        self.temperature: float | None = None

    async def available(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(15, connect=5),
                                         trust_env=False) as client:
                response = await client.get(f"{self.url}/api/tags")
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
                    return False
                names = {item["name"] for item in payload["models"]
                         if isinstance(item, dict) and isinstance(item.get("name"), str)}
                return self.model in names
        except (httpx.HTTPError, ValueError):
            return False

    async def stream(self, messages: list[dict[str, str]]) -> AsyncIterator[Chunk]:
        try:
            async with (
                httpx.AsyncClient(timeout=httpx.Timeout(180, connect=5), trust_env=False) as client,
                client.stream("POST", f"{self.url}/api/chat", json={
                    "model": self.model,
                    "messages": messages,
                    "stream": True, "think": False,
                    **({"format": self.response_format} if self.response_format else {}),
                    "options": {"num_predict": self.max_output_tokens,
                                "num_ctx": self.context_tokens,
                                **({"temperature": 0} if self.response_format else
                                   {"temperature": self.temperature}
                                   if self.temperature is not None else {})},
                }) as response,
            ):
                    if response.status_code != 200:
                        body = (await response.aread()).decode("utf-8", "replace")[:300]
                        raise ProviderError("ollama_http", f"Ollama HTTP {response.status_code}: {body}")
                    done = False
                    async for line in response.aiter_lines():
                        if not line.strip():
                            continue
                        try:
                            item = json.loads(line)
                        except json.JSONDecodeError as exc:
                            raise ProviderError("malformed_stream", "Ungültiger Ollama-Stream") from exc
                        if not isinstance(item, dict):
                            raise ProviderError("malformed_stream", "Ungültiger Ollama-Stream")
                        if item.get("error"):
                            raise ProviderError("ollama_error", str(item["error"])[:300])
                        message = item.get("message", {})
                        if not isinstance(message, dict) or not isinstance(message.get("content", ""), str):
                            raise ProviderError("malformed_stream", "Ungültiger Ollama-Stream")
                        content = message.get("content", "")
                        if content:
                            yield Chunk("text", text=content)
                        done_flag = item.get("done")
                        if done_flag is not None and type(done_flag) is not bool:
                            raise ProviderError("malformed_stream", "Ungültiger Ollama-Abschlusswert")
                        if done_flag:
                            done = True
                            yield Chunk(
                                "usage",
                                input_tokens=nonnegative_token_count(item.get("prompt_eval_count")),
                                output_tokens=nonnegative_token_count(item.get("eval_count")),
                                cost_usd=0.0, cost_kind="local_no_api_charge",
                            )
                    if not done:
                        raise ProviderError("incomplete_stream", "Ollama-Stream endete ohne Abschluss")
        except httpx.ConnectError as exc:
            raise ProviderError("unavailable", "Ollama ist nicht erreichbar") from exc
        except httpx.TimeoutException as exc:
            raise ProviderError("timeout", "Ollama hat nicht rechtzeitig geantwortet") from exc
        except httpx.HTTPError as exc:
            raise ProviderError("ollama_http", "Ollama-Verbindung fehlgeschlagen") from exc


class OpenRouterAdapter(ProviderAdapter):
    def __init__(self, api_key: str, model: str, max_output_tokens: int = 1024):
        if has_unbudgeted_online_search(model):
            raise ProviderError("unsupported_model_variant",
                                "OpenRouter-Onlinevariante hat zusätzliche Suchkosten und ist hier gesperrt")
        self.api_key = api_key
        self.model = model
        self.max_output_tokens = max_output_tokens
        self.input_price: Decimal | None = None
        self.output_price: Decimal | None = None
        self.request_price: Decimal | None = None

    async def load_pricing(self) -> None:
        """Liest aktuelle $/Token-Werte; ohne Preis keine bezahlte Anfrage."""
        if (self.input_price is not None and self.output_price is not None
                and self.request_price is not None):
            return
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(
                    f"https://openrouter.ai/api/v1/model/{quote(self.model, safe='/')}",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
            if response.status_code == 401:
                raise ProviderError("unauthorized", "OpenRouter-Schlüssel ist ungültig")
            if response.status_code == 429:
                raise ProviderError("rate_limited", "OpenRouter-Rate-Limit erreicht")
            if response.status_code != 200:
                raise ProviderError("model_lookup", f"OpenRouter-Modellprüfung: HTTP {response.status_code}")
            payload = response.json()
            if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
                raise TypeError("Ungültige Modellantwort")
            data = payload["data"]
            if data.get("id") != self.model:
                raise ProviderError("model_mismatch", "OpenRouter lieferte ein anderes Modell")
            pricing = data.get("pricing")
            if not isinstance(pricing, dict):
                raise TypeError("Ungültige Modellpreise")
            input_price = Decimal(str(pricing["prompt"]))
            output_price = Decimal(str(pricing["completion"]))
            request_price = Decimal(str(pricing.get("request", "0")))
            if not all(price.is_finite() and price >= 0
                       for price in (input_price, output_price, request_price)):
                raise ValueError("Ungültiger Preis")
            self.input_price, self.output_price, self.request_price = (
                input_price, output_price, request_price)
        except httpx.TimeoutException as exc:
            raise ProviderError("timeout", "OpenRouter-Modellprüfung hat nicht rechtzeitig geantwortet") from exc
        except (httpx.HTTPError, ValueError, TypeError, KeyError, InvalidOperation) as exc:
            raise ProviderError("model_lookup", "OpenRouter-Modell oder Preis nicht prüfbar") from exc

    def upper_bound_cost(self, messages: list[dict[str, str]]) -> float:
        if self.input_price is None or self.output_price is None or self.request_price is None:
            raise ProviderError("price_unknown", "Modellpreis wurde nicht geprüft")
        # Konservative Zeichenobergrenze für mehrsprachige Texte; ohne belastbare
        # Tokenisierung ist dies keine harte Abrechnungsschranke.
        prompt_upper_bound = sum(len(item["content"].encode("utf-8")) for item in messages) + 2048
        try:
            estimate = float(prompt_upper_bound * self.input_price
                             + self.max_output_tokens * self.output_price + self.request_price)
        except (OverflowError, DecimalException) as exc:
            raise ProviderError("price_unknown", "Modellpreis ist nicht als Budget prüfbar") from exc
        if not math.isfinite(estimate):
            raise ProviderError("price_unknown", "Modellpreis ist nicht als Budget prüfbar")
        return estimate

    async def stream(self, messages: list[dict[str, str]]) -> AsyncIterator[Chunk]:
        from langchain_openrouter import ChatOpenRouter

        model = ChatOpenRouter(
            model=self.model, api_key=self.api_key, max_retries=0,
            max_tokens=self.max_output_tokens,
            timeout=OPENROUTER_REQUEST_TIMEOUT_MS,
            openrouter_provider={"require_parameters": True, "data_collection": "deny"},
        )
        try:
            async for part in model.astream([(m["role"], m["content"]) for m in messages]):
                if isinstance(part.content, str) and part.content:
                    yield Chunk("text", text=part.content)
                if part.usage_metadata:
                    raw_input = part.usage_metadata.get("input_tokens")
                    raw_output = part.usage_metadata.get("output_tokens")
                    input_tokens = nonnegative_token_count(raw_input)
                    output_tokens = nonnegative_token_count(raw_output)
                    raw_cost = (part.response_metadata or {}).get("cost")
                    try:
                        reported_cost = Decimal(str(raw_cost)) if raw_cost is not None else None
                    except InvalidOperation:
                        reported_cost = None
                    cost = None
                    if reported_cost is not None and reported_cost.is_finite() and reported_cost >= 0:
                        try:
                            candidate = float(reported_cost)
                            if math.isfinite(candidate):
                                cost = candidate
                        except (OverflowError, DecimalException):
                            pass
                    kind = "reported" if cost is not None else "unknown"
                    if (cost is None and self.input_price is not None and self.output_price is not None
                            and self.request_price is not None
                            and input_tokens is not None and output_tokens is not None):
                        try:
                            estimate = float(input_tokens * self.input_price
                                             + output_tokens * self.output_price
                                             + self.request_price)
                            if math.isfinite(estimate):
                                cost = estimate
                                kind = "estimated_from_current_list_price"
                        except OverflowError:
                            pass
                    yield Chunk("usage", input_tokens=input_tokens,
                                output_tokens=output_tokens,
                                cost_usd=cost, cost_kind=kind)
        except Exception as exc:
            # Keine Request-Bodies oder Auth-Header in Logs/Fehlermeldungen.
            status = getattr(exc, "status_code", None)
            if status == 401:
                raise ProviderError("unauthorized", "OpenRouter-Schlüssel ist ungültig") from exc
            if status == 429:
                raise ProviderError("rate_limited", "OpenRouter-Rate-Limit erreicht") from exc
            if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
                raise ProviderError("timeout", "OpenRouter-Zeitüberschreitung") from exc
            raise ProviderError("provider_error", "OpenRouter-Anfrage fehlgeschlagen") from exc


def make_provider(settings: Settings) -> ProviderAdapter:
    if settings.provider == "ollama":
        return OllamaAdapter(settings.ollama_url, settings.ollama_model,
                             settings.max_output_tokens, settings.ollama_context_tokens)
    key = openrouter_api_key(settings)
    if not key or not settings.openrouter_model:
        raise ProviderError("not_configured", "OpenRouter-Key und Modell lokal einrichten")
    return OpenRouterAdapter(key, settings.openrouter_model,
                             settings.max_output_tokens)

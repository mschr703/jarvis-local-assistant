import json
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest

from jarvis.provider import (
    OPENROUTER_REQUEST_TIMEOUT_MS,
    OllamaAdapter,
    OpenRouterAdapter,
    ProviderError,
)


def test_openrouter_sdk_accepts_local_adapter_options_without_a_request():
    from langchain_openrouter import ChatOpenRouter

    model = ChatOpenRouter(
        model="example/model", api_key="fixture", max_retries=0, max_tokens=32,
        timeout=OPENROUTER_REQUEST_TIMEOUT_MS,
        openrouter_provider={"require_parameters": True, "data_collection": "deny"},
    )
    assert model.model_name == "example/model"
    assert model.max_tokens == 32
    assert model.stream_usage is True
    assert model.openrouter_provider["data_collection"] == "deny"
    assert model.request_timeout == OPENROUTER_REQUEST_TIMEOUT_MS


def test_openrouter_online_variant_is_rejected_before_paid_call():
    with pytest.raises(ProviderError, match="zusätzliche Suchkosten"):
        OpenRouterAdapter("fixture", "example/model:online")


@pytest.mark.parametrize("url", [
    "http://localhost:11434@evil.example",
    "http://127.0.0.1:11434@evil.example",
    "http://user@localhost:11434",
    "http://localhost:11434/api",
    "http://localhost:11434?redirect=https://evil.example",
    "https://localhost:11434",
    "http://localhost:invalid",
    "http://localhost:11434#fragment",
])
def test_ollama_url_rejects_non_local_or_ambiguous_destination(url):
    with pytest.raises(ValueError):
        OllamaAdapter(url, "fixture")


def test_ollama_localhost_is_pinned_to_loopback_ip():
    assert OllamaAdapter("http://localhost:11434/", "fixture").url == "http://127.0.0.1:11434"


@pytest.mark.parametrize("models", [
    [None, "bad"],
    [{"name": {"unexpected": "object"}}],
    [{"name": ["unexpected", "list"]}],
    [{"name": None}],
])
@pytest.mark.asyncio
async def test_ollama_unexpected_tags_response_is_unavailable(monkeypatch, models):
    client = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={"models": models})
    ))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client)
    assert not await OllamaAdapter("http://127.0.0.1:11434", "fixture").available()


@pytest.mark.asyncio
async def test_ollama_rejects_non_object_stream_record(monkeypatch):
    client = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, text='["unexpected"]\n')
    ))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client)
    with pytest.raises(ProviderError, match="Ungültiger Ollama-Stream"):
        [item async for item in OllamaAdapter("http://127.0.0.1:11434", "fixture").stream([])]


@pytest.mark.asyncio
async def test_ollama_stream_usage_and_malformed_response(monkeypatch):
    events = [
        {"message": {"content": "Hallo"}, "done": False},
        {"message": {"content": ""}, "done": True, "prompt_eval_count": 4, "eval_count": 2},
    ]

    def handler(request):
        assert request.url.path == "/api/chat"
        assert json.loads(request.content)["options"] == {"num_predict": 1024,
                                                            "num_ctx": 8192}
        return httpx.Response(200, text="\n".join(json.dumps(item) for item in events))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client)
    chunks = [item async for item in OllamaAdapter("http://127.0.0.1:11434", "fixture").stream([])]
    assert chunks[0].text == "Hallo"
    assert chunks[1].input_tokens == 4
    assert chunks[1].cost_kind == "local_no_api_charge"


@pytest.mark.parametrize("response_format", ["json", {"type": "object", "properties": {
    "tools": {"type": "array"}}}])
@pytest.mark.asyncio
async def test_ollama_structured_response_passes_format_and_zero_temperature(
        monkeypatch, response_format):

    def handler(request):
        payload = json.loads(request.content)
        assert payload["format"] == response_format
        assert payload["options"]["temperature"] == 0
        return httpx.Response(200, text=json.dumps({
            "message": {"content": '{"tools":[]}'}, "done": True}) + "\n")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client)
    provider = OllamaAdapter("http://127.0.0.1:11434", "fixture")
    provider.response_format = response_format
    chunks = [item.text async for item in provider.stream([])]
    assert chunks[0] == '{"tools":[]}'


@pytest.mark.asyncio
async def test_ollama_invalid_token_counts_do_not_become_receipts(monkeypatch):
    client = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, text=json.dumps({
            "message": {"content": "Antwort"}, "done": True,
            "prompt_eval_count": -3, "eval_count": "5",
        }) + "\n")
    ))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client)
    chunks = [item async for item in OllamaAdapter("http://127.0.0.1:11434", "fixture").stream([])]
    assert chunks[-1].input_tokens is None
    assert chunks[-1].output_tokens is None
    assert chunks[-1].cost_usd == 0.0


@pytest.mark.asyncio
async def test_ollama_rejects_non_boolean_done_flag(monkeypatch):
    client = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, text='{"message":{"content":"Teil"},"done":"false"}\n')
    ))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client)
    with pytest.raises(ProviderError, match="Abschlusswert"):
        [item async for item in OllamaAdapter("http://127.0.0.1:11434", "fixture").stream([])]


@pytest.mark.asyncio
async def test_ollama_disables_environment_proxies_for_local_requests(monkeypatch):
    options = []
    original_client = httpx.AsyncClient

    def client_factory(**kwargs):
        options.append(kwargs)
        transport = httpx.MockTransport(lambda request: httpx.Response(200, json={
            "models": [{"name": "fixture"}],
        }) if request.url.path == "/api/tags" else httpx.Response(200, text=json.dumps({
            "message": {"content": "Antwort"}, "done": True,
        }) + "\n"))
        return original_client(transport=transport, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client_factory)
    provider = OllamaAdapter("http://127.0.0.1:11434", "fixture")
    assert await provider.available()
    assert [part async for part in provider.stream([])]
    assert len(options) == 2 and all(item["trust_env"] is False for item in options)


@pytest.mark.asyncio
async def test_ollama_rejects_incomplete_stream(monkeypatch):
    client = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, text='{"message":{"content":"Teil"}}\n')
    ))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client)
    with pytest.raises(ProviderError, match="ohne Abschluss"):
        [item async for item in OllamaAdapter("http://127.0.0.1:11434", "fixture").stream([])]


@pytest.mark.asyncio
async def test_openrouter_price_required_before_budget_estimate(monkeypatch):
    model = OpenRouterAdapter("test-key", "example/model", max_output_tokens=10)
    with pytest.raises(ProviderError, match="nicht geprüft"):
        model.upper_bound_cost([{"role": "user", "content": "Hallo"}])
    def model_lookup(request):
        assert request.method == "GET"
        assert request.url.path == "/api/v1/model/example/model"
        assert request.headers["authorization"] == "Bearer test-key"
        return httpx.Response(200, json={"data": {
            "id": "example/model", "pricing": {
                "prompt": "0.000001", "completion": "0.000002", "request": "0.02",
            },
        }})

    client = httpx.AsyncClient(transport=httpx.MockTransport(model_lookup))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client)
    await model.load_pricing()
    assert model.upper_bound_cost([{"role": "user", "content": "Hallo"}]) > 0.02


@pytest.mark.asyncio
async def test_openrouter_invalid_request_price_blocks_paid_call(monkeypatch):
    client = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={"data": {
            "id": "example/model", "pricing": {
                "prompt": "0.000001", "completion": "0.000002", "request": "NaN",
            },
        }})
    ))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client)
    with pytest.raises(ProviderError, match="Preis nicht prüfbar"):
        await OpenRouterAdapter("fixture", "example/model").load_pricing()


@pytest.mark.asyncio
@pytest.mark.parametrize("status,code", [(401, "unauthorized"), (429, "rate_limited")])
async def test_openrouter_model_lookup_reports_auth_and_rate_errors(monkeypatch, status, code):
    client = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(status)
    ))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client)
    with pytest.raises(ProviderError) as captured:
        await OpenRouterAdapter("fixture", "example/model").load_pricing()
    assert captured.value.code == code


@pytest.mark.asyncio
async def test_openrouter_model_lookup_reports_timeout(monkeypatch):
    def timeout(request):
        raise httpx.ReadTimeout("fixture", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(timeout))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client)
    with pytest.raises(ProviderError) as captured:
        await OpenRouterAdapter("fixture", "example/model").load_pricing()
    assert captured.value.code == "timeout"


@pytest.mark.asyncio
@pytest.mark.parametrize("status,code", [(401, "unauthorized"), (429, "rate_limited")])
async def test_openrouter_stream_reports_auth_and_rate_errors(monkeypatch, status, code):
    class RequestFailure(Exception):
        status_code = status

    class FakeChatOpenRouter:
        def __init__(self, **_kwargs):
            pass

        async def astream(self, _messages):
            raise RequestFailure("fixture")
            yield  # pragma: no cover - erzwingt einen AsyncIterator

    monkeypatch.setattr("langchain_openrouter.ChatOpenRouter", FakeChatOpenRouter)
    with pytest.raises(ProviderError) as captured:
        [item async for item in OpenRouterAdapter("fixture", "example/model").stream([])]
    assert captured.value.code == code


@pytest.mark.asyncio
async def test_openrouter_stream_reports_timeout(monkeypatch):
    options = {}

    class FakeChatOpenRouter:
        def __init__(self, **kwargs):
            options.update(kwargs)

        async def astream(self, _messages):
            raise httpx.ReadTimeout("fixture")
            yield  # pragma: no cover - erzwingt einen AsyncIterator

    monkeypatch.setattr("langchain_openrouter.ChatOpenRouter", FakeChatOpenRouter)
    with pytest.raises(ProviderError) as captured:
        [item async for item in OpenRouterAdapter("fixture", "example/model").stream([])]
    assert captured.value.code == "timeout"
    assert options["timeout"] == OPENROUTER_REQUEST_TIMEOUT_MS
    assert options["max_retries"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [[], {"data": None}, {"data": {"id": "example/model",
                                                                   "pricing": []}}])
async def test_openrouter_malformed_model_metadata_blocks_paid_call(monkeypatch, payload):
    client = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json=payload)
    ))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client)
    with pytest.raises(ProviderError, match="Preis nicht prüfbar"):
        await OpenRouterAdapter("fixture", "example/model").load_pricing()


@pytest.mark.asyncio
@pytest.mark.parametrize("reported, expected_cost, expected_kind", [
    (-100, 0.02, "estimated_from_current_list_price"),
    ("NaN", 0.02, "estimated_from_current_list_price"),
    ("0.123", 0.123, "reported"),
])
async def test_openrouter_reported_cost_is_validated(monkeypatch, reported, expected_cost,
                                                     expected_kind):
    class FakeChatOpenRouter:
        def __init__(self, **_kwargs):
            pass

        async def astream(self, _messages):
            yield SimpleNamespace(content="", usage_metadata={"input_tokens": 10,
                                                               "output_tokens": 5},
                                  response_metadata={"cost": reported})

    monkeypatch.setattr("langchain_openrouter.ChatOpenRouter", FakeChatOpenRouter)
    model = OpenRouterAdapter("fixture", "example/model")
    model.input_price = Decimal("0.001")
    model.output_price = Decimal("0.002")
    model.request_price = Decimal("0.005")
    chunks = [item async for item in model.stream([{"role": "user", "content": "Hallo"}])]
    assert chunks[0].cost_usd == pytest.approx(
        expected_cost + (0.005 if expected_kind == "estimated_from_current_list_price" else 0))
    assert chunks[0].cost_kind == expected_kind


def test_openrouter_excessive_price_cannot_start_paid_request():
    model = OpenRouterAdapter("fixture", "example/model")
    model.input_price = Decimal("1e999999")
    model.output_price = Decimal(1)
    model.request_price = Decimal(0)
    with pytest.raises(ProviderError, match="nicht als Budget prüfbar"):
        model.upper_bound_cost([{"role": "user", "content": "Hallo"}])

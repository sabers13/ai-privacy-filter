"""BYOK backend: OpenAI-compatible request shape, content parsing and errors."""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from backends_support import fixture_json

from ai_privacy_filter import __version__
from ai_privacy_filter.backends.base import (
    ERROR_AUTH,
    ERROR_BILLING,
    ERROR_EMPTY,
    ERROR_HTTP,
    ERROR_INVALID_JSON,
    ERROR_NETWORK,
    ERROR_NOT_CONFIGURED,
    ERROR_RATE_LIMITED,
    ERROR_RESPONSE,
    ERROR_SCHEMA,
    ERROR_TIMEOUT,
)
from ai_privacy_filter.backends.byok import (
    BYOK_DEFAULT_MAX_TOKENS,
    OLLAMA_BASE_URL,
    OLLAMA_MAX_TOKENS,
    BYOKBackend,
    chat_completions_url,
)
from ai_privacy_filter.backends.registry import get_backend
from ai_privacy_filter.triage import TRIAGE_SCHEMA

PROMPT = "Synthetic Anfrage von [PERSON_1]. Antworte als JSON."

Handler = Callable[[httpx.Request], httpx.Response]


def _client(handler: Handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _backend(handler: Handler, **kwargs: Any) -> BYOKBackend:
    return BYOKBackend(
        model="deepseek-v4.1-flash",
        base_url="https://synthetic.invalid/v1",
        api_key="synthetic-key",
        client=_client(handler),
        **kwargs,
    )


def _response(json_body: dict[str, Any], status: int = 200) -> Handler:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=json_body)

    return handler


@pytest.fixture(autouse=True)
def _clear_byok_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "AIPF_BYOK_BASE_URL",
        "AIPF_BYOK_API_KEY",
        "AIPF_BYOK_MODEL",
        "AIPF_BYOK_MAX_TOKENS",
    ):
        monkeypatch.delenv(name, raising=False)


def test_request_shape_and_content_parse() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=fixture_json("byok_success.json"))

    backend = _backend(handler)
    result = backend.complete_json(PROMPT, TRIAGE_SCHEMA, 30)
    assert result.error_kind is None
    assert result.parsed == {
        "category": "OTHER",
        "urgency": "LOW",
        "reply_draft": "Guten Tag [PERSON_1], vielen Dank. Wir melden uns zeitnah.",
    }
    assert result.model == "deepseek-v4.1-flash"

    request = seen[0]
    assert str(request.url) == "https://synthetic.invalid/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer synthetic-key"
    assert request.headers["user-agent"] == f"ai-privacy-filter/{__version__}"
    session = request.headers["x-opencode-session"]
    assert str(uuid.UUID(session)) == session
    body = json.loads(request.content)
    assert body["model"] == "deepseek-v4.1-flash"
    assert body["max_tokens"] == BYOK_DEFAULT_MAX_TOKENS == 2000
    assert body["stream"] is False
    assert body["messages"] == [{"role": "user", "content": PROMPT}]
    assert "response_format" not in body


def test_reasoning_usage_is_recorded_but_content_is_the_only_parse() -> None:
    backend = _backend(_response(fixture_json("byok_success.json")))
    result = backend.complete_json(PROMPT, TRIAGE_SCHEMA, 30)
    assert result.usage == {
        "input_tokens": 800,
        "output_tokens": 64,
        "total_tokens": 864,
        "cached_tokens": 128,
        "reasoning_tokens": 21,
    }
    assert "Synthetic reasoning" not in result.text
    assert result.parsed is not None
    assert "reasoning" not in result.parsed


def test_session_header_is_fresh_per_enquiry() -> None:
    sessions: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sessions.append(request.headers["x-opencode-session"])
        return httpx.Response(200, json=fixture_json("byok_success.json"))

    backend = _backend(handler)
    backend.complete_json(PROMPT, TRIAGE_SCHEMA, 30)
    backend.complete_json(PROMPT, TRIAGE_SCHEMA, 30)
    assert len(set(sessions)) == 2


def test_fenced_json_content_is_accepted() -> None:
    payload = fixture_json("byok_success.json")
    content = payload["choices"][0]["message"]["content"]
    payload["choices"][0]["message"]["content"] = f"```json\n{content}\n```"
    backend = _backend(_response(payload))
    result = backend.complete_json(PROMPT, TRIAGE_SCHEMA, 30)
    assert result.error_kind is None
    assert result.parsed is not None


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, ERROR_AUTH),
        (403, ERROR_AUTH),
        (402, ERROR_BILLING),
        (429, ERROR_RATE_LIMITED),
        (500, ERROR_HTTP),
    ],
)
def test_http_status_mapping(status: int, expected: str) -> None:
    backend = _backend(_response({"error": "synthetic"}, status=status))
    assert backend.complete_json(PROMPT, TRIAGE_SCHEMA, 5).error_kind == expected


def test_timeout_and_network_errors() -> None:
    def timeout(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("synthetic timeout")

    def network(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("synthetic connect failure")

    assert _backend(timeout).complete_json(PROMPT, TRIAGE_SCHEMA, 1).error_kind == ERROR_TIMEOUT
    assert _backend(network).complete_json(PROMPT, TRIAGE_SCHEMA, 1).error_kind == ERROR_NETWORK


def test_response_shape_errors() -> None:
    assert (
        _backend(_response({"choices": []})).complete_json(PROMPT, TRIAGE_SCHEMA, 5).error_kind
        == ERROR_RESPONSE
    )
    assert (
        _backend(_response({"choices": [{"message": {"content": "   "}}]}))
        .complete_json(PROMPT, TRIAGE_SCHEMA, 5)
        .error_kind
        == ERROR_EMPTY
    )


def test_non_json_and_schema_errors() -> None:
    prose = fixture_json("byok_success.json")
    prose["choices"][0]["message"]["content"] = "Kategorie: OTHER"
    assert (
        _backend(_response(prose)).complete_json(PROMPT, TRIAGE_SCHEMA, 5).error_kind
        == ERROR_INVALID_JSON
    )

    incomplete = fixture_json("byok_success.json")
    incomplete["choices"][0]["message"]["content"] = json.dumps(
        {"category": "OTHER", "urgency": "LOW"}
    )
    assert (
        _backend(_response(incomplete)).complete_json(PROMPT, TRIAGE_SCHEMA, 5).error_kind
        == ERROR_SCHEMA
    )


def test_missing_configuration_never_calls_out() -> None:
    called = False

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={})

    backend = BYOKBackend(client=_client(handler))
    assert backend.is_available() is False
    assert backend.complete_json(PROMPT, TRIAGE_SCHEMA, 1).error_kind == ERROR_NOT_CONFIGURED
    assert called is False


def test_environment_configuration_is_read_at_call_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AIPF_BYOK_BASE_URL", "https://synthetic.invalid/v1")
    monkeypatch.setenv("AIPF_BYOK_API_KEY", "synthetic-env-key")
    monkeypatch.setenv("AIPF_BYOK_MODEL", "synthetic-env-model")
    monkeypatch.setenv("AIPF_BYOK_MAX_TOKENS", "1500")
    backend = get_backend("byok")
    assert isinstance(backend, BYOKBackend)
    assert backend.is_available() is True

    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=fixture_json("byok_success.json"))

    backend._client = _client(handler)
    assert backend.complete_json(PROMPT, TRIAGE_SCHEMA, 5).error_kind is None
    body = json.loads(seen[0].content)
    assert body["model"] == "synthetic-env-model"
    assert body["max_tokens"] == 1500
    assert seen[0].headers["authorization"] == "Bearer synthetic-env-key"


def test_ollama_factory_uses_short_ceiling_and_schema_format() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=fixture_json("byok_success.json"))

    backend = BYOKBackend.for_ollama("synthetic-local-model")
    backend._client = _client(handler)
    assert backend.is_available() is True
    assert backend.complete_json(PROMPT, TRIAGE_SCHEMA, 5).error_kind is None
    request = seen[0]
    assert str(request.url) == f"{OLLAMA_BASE_URL}/chat/completions"
    assert "authorization" not in request.headers
    body = json.loads(request.content)
    assert body["model"] == "synthetic-local-model"
    assert body["max_tokens"] == OLLAMA_MAX_TOKENS == 300
    assert body["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "response", "schema": TRIAGE_SCHEMA, "strict": True},
    }


def test_json_object_response_format_when_requested() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=fixture_json("byok_success.json"))

    backend = _backend(handler, response_format="json_object")
    backend.complete_json(PROMPT, TRIAGE_SCHEMA, 5)
    assert json.loads(seen[0].content)["response_format"] == {"type": "json_object"}


@pytest.mark.parametrize(
    ("base", "expected"),
    [
        ("https://host.invalid/v1", "https://host.invalid/v1/chat/completions"),
        ("https://host.invalid/v1/", "https://host.invalid/v1/chat/completions"),
        (
            "https://host.invalid/v1/chat/completions",
            "https://host.invalid/v1/chat/completions",
        ),
        ("https://host.invalid/v1/models", "https://host.invalid/v1/chat/completions"),
        ("https://host.invalid", "https://host.invalid/chat/completions"),
        ("http://127.0.0.1:11435/v1", "http://127.0.0.1:11435/v1/chat/completions"),
    ],
)
def test_chat_completions_url(base: str, expected: str) -> None:
    assert chat_completions_url(base) == expected

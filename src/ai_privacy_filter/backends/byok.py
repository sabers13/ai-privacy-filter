"""BYOK backend: OpenAI-compatible chat completions.

Adapted from SaberOps, Apache-2.0 (``src/saberops/workers/direct_api.py`` and
``src/saberops/workers/api_agent.py``). Only the request/response code is kept:
the tool loop, worktree scoping, connection store, credential references and
event streaming are dropped.

The same backend serves OpenCode Go and a local Ollama instance; only the base
URL, token ceiling and structured-output mode differ. Requests carry a fresh
``x-opencode-session`` UUID per enquiry, the project user agent, and (when
configured) a bearer key. Only ``choices[0].message.content`` is parsed --
reasoning text is never read -- and ``reasoning_tokens`` from the usage block is
recorded when the provider reports it.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any, Final, Literal

import httpx

from ai_privacy_filter import __version__
from ai_privacy_filter.backends import schema as schema_validator
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
    failure,
)
from ai_privacy_filter.models import BackendResult

__all__ = [
    "BYOK_DEFAULT_MAX_TOKENS",
    "OLLAMA_BASE_URL",
    "OLLAMA_MAX_TOKENS",
    "BYOKBackend",
    "chat_completions_url",
]

#: Default ceiling for reasoning models, per the project standing rules.
BYOK_DEFAULT_MAX_TOKENS: Final = 2000
#: Local model calls keep a short reply ceiling.
OLLAMA_MAX_TOKENS: Final = 300
#: Local Ollama endpoint, reached through the server tunnel.
OLLAMA_BASE_URL: Final = "http://127.0.0.1:11435/v1"

ResponseFormat = Literal["none", "json_object", "json_schema"]

_USER_AGENT: Final = f"ai-privacy-filter/{__version__}"


def chat_completions_url(base_url: str) -> str:
    """Return the chat-completions URL for an OpenAI-compatible ``base_url``.

    A base that already ends in ``/chat/completions`` is kept as-is; a trailing
    ``/models``, ``/responses`` or ``/completions`` is replaced; otherwise
    ``/chat/completions`` is appended (so ``http://host:port/v1`` works).
    """
    parsed = httpx.URL(base_url)
    path = parsed.path.rstrip("/")
    if not path.endswith("/chat/completions"):
        for suffix in ("/models", "/responses", "/completions"):
            if path.endswith(suffix):
                path = path[: -len(suffix)]
                break
        path = f"{path}/chat/completions"
    return str(parsed.copy_with(path=path, query=None, fragment=None))


def _as_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _normalise_usage(raw: object) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    details = raw.get("prompt_tokens_details")
    completion_details = raw.get("completion_tokens_details")
    return {
        "input_tokens": _as_int(raw.get("prompt_tokens")),
        "output_tokens": _as_int(raw.get("completion_tokens")),
        "total_tokens": _as_int(raw.get("total_tokens")),
        "cached_tokens": (
            _as_int(details.get("cached_tokens")) if isinstance(details, dict) else None
        ),
        "reasoning_tokens": (
            _as_int(completion_details.get("reasoning_tokens"))
            if isinstance(completion_details, dict)
            else None
        ),
    }


def _extract_json_object(text: str) -> dict[str, Any] | None:
    """Return the first JSON object in ``text``, tolerating a code fence."""
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = [line for line in stripped.splitlines() if not line.strip().startswith("```")]
        stripped = "\n".join(lines).strip()
    decoder = json.JSONDecoder()
    for index, character in enumerate(stripped):
        if character != "{":
            continue
        try:
            payload, _ = decoder.raw_decode(stripped[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return None


class BYOKBackend:
    """OpenAI-compatible backend; defaults come from ``AIPF_BYOK_*`` at call time."""

    name: str = "byok"

    def __init__(
        self,
        model: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        *,
        max_tokens: int | None = None,
        response_format: ResponseFormat = "none",
        client: httpx.Client | None = None,
    ) -> None:
        self.model = model
        self.base_url = base_url
        self.api_key = api_key
        self.max_tokens = max_tokens
        self.response_format = response_format
        self._client = client

    @classmethod
    def for_ollama(
        cls,
        model: str | None = None,
        *,
        base_url: str = OLLAMA_BASE_URL,
    ) -> BYOKBackend:
        """Return a backend for the tunnel-local Ollama endpoint.

        Short reply ceiling and a JSON-schema response format; no bearer key.
        """
        return cls(
            model=model,
            base_url=base_url,
            api_key="",
            max_tokens=OLLAMA_MAX_TOKENS,
            response_format="json_schema",
        )

    def is_available(self) -> bool:
        """Return whether the endpoint is configured (no network call)."""
        base_url = self._setting("base_url", "AIPF_BYOK_BASE_URL")
        if not base_url:
            return False
        return self._is_local(base_url) or bool(self._setting("api_key", "AIPF_BYOK_API_KEY"))

    def complete_json(
        self,
        prompt: str,
        schema: dict[str, Any],
        timeout_s: int = 30,
    ) -> BackendResult:
        """POST one chat completion and return its JSON content or an error kind."""
        started = time.monotonic()
        base_url = self._setting("base_url", "AIPF_BYOK_BASE_URL")
        api_key = self._setting("api_key", "AIPF_BYOK_API_KEY")
        model = self._setting("model", "AIPF_BYOK_MODEL")
        max_tokens = self._max_tokens()
        if not base_url or not model or (not api_key and not self._is_local(base_url)):
            return failure(ERROR_NOT_CONFIGURED, 0)

        headers = {
            "Authorization": f"Bearer {api_key}" if api_key else "",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": _USER_AGENT,
            "x-opencode-session": str(uuid.uuid4()),
        }
        if not headers["Authorization"]:
            del headers["Authorization"]
        body: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "stream": False,
        }
        if self.response_format == "json_object":
            body["response_format"] = {"type": "json_object"}
        elif self.response_format == "json_schema":
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "response", "schema": schema, "strict": True},
            }

        client = self._client
        owned = client is None
        if client is None:
            client = httpx.Client(timeout=float(max(1, timeout_s)))
        try:
            response = client.post(
                chat_completions_url(base_url),
                headers=headers,
                json=body,
                timeout=float(max(1, timeout_s)),
            )
        except httpx.TimeoutException:
            return failure(ERROR_TIMEOUT, self._elapsed_ms(started))
        except httpx.HTTPError:
            return failure(ERROR_NETWORK, self._elapsed_ms(started))
        finally:
            if owned:
                client.close()
        latency_ms = self._elapsed_ms(started)

        status_kind = self._status_kind(response.status_code)
        if status_kind is not None:
            return failure(status_kind, latency_ms)

        try:
            data = response.json()
        except ValueError:
            return failure(ERROR_RESPONSE, latency_ms)
        if not isinstance(data, dict):
            return failure(ERROR_RESPONSE, latency_ms)
        usage = _normalise_usage(data.get("usage"))
        reported_model = data.get("model")
        actual_model = reported_model if isinstance(reported_model, str) else model

        choices = data.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            return failure(ERROR_RESPONSE, latency_ms, model=actual_model, usage=usage)
        message = choices[0].get("message")
        if not isinstance(message, dict):
            return failure(ERROR_RESPONSE, latency_ms, model=actual_model, usage=usage)
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            return failure(ERROR_EMPTY, latency_ms, model=actual_model, usage=usage)

        payload = _extract_json_object(content)
        if payload is None:
            return failure(
                ERROR_INVALID_JSON,
                latency_ms,
                model=actual_model,
                usage=usage,
                text=content,
            )
        error = schema_validator.validate(payload, schema)
        if error is not None:
            return failure(
                ERROR_SCHEMA,
                latency_ms,
                model=actual_model,
                usage=usage,
                text=content,
            )
        return BackendResult(
            text=content,
            parsed=payload,
            model=actual_model,
            latency_ms=latency_ms,
            usage=usage,
            error_kind=None,
        )

    def _setting(self, attribute: str, env_name: str) -> str | None:
        """Constructor value wins; an empty string explicitly means "unset".

        ``None`` (the default) falls back to the environment so the registry can
        build a backend before the private env file is loaded.
        """
        value = getattr(self, attribute)
        if isinstance(value, str):
            return value or None
        if value is not None:  # pragma: no cover - defensive
            return str(value)
        return os.environ.get(env_name) or None

    def _max_tokens(self) -> int:
        if self.max_tokens is not None:
            return max(1, self.max_tokens)
        raw = os.environ.get("AIPF_BYOK_MAX_TOKENS", "")
        if raw.isdigit() and int(raw) > 0:
            return int(raw)
        return BYOK_DEFAULT_MAX_TOKENS

    @staticmethod
    def _is_local(base_url: str) -> bool:
        host = httpx.URL(base_url).host
        return host in {"127.0.0.1", "localhost", "::1"}

    @staticmethod
    def _status_kind(status_code: int) -> str | None:
        if 200 <= status_code < 300:
            return None
        if status_code in {401, 403}:
            return ERROR_AUTH
        if status_code == 402:
            return ERROR_BILLING
        if status_code == 429:
            return ERROR_RATE_LIMITED
        return ERROR_HTTP

    @staticmethod
    def _elapsed_ms(started: float) -> int:
        return max(0, int((time.monotonic() - started) * 1000))

"""Backend protocol shared by every LLM adapter.

Adapted from SaberOps, Apache-2.0 (``src/saberops/workers/base.py``): this keeps
only the provider-neutral result shape. Worktree handling, the control plane and
streaming callbacks are deliberately absent.
"""

from __future__ import annotations

from typing import Any, Final, Protocol, runtime_checkable

from ai_privacy_filter.models import BackendResult

__all__ = [
    "ERROR_AUTH",
    "ERROR_BILLING",
    "ERROR_CLI",
    "ERROR_EMPTY",
    "ERROR_HTTP",
    "ERROR_INVALID_JSON",
    "ERROR_NETWORK",
    "ERROR_NOT_CONFIGURED",
    "ERROR_NOT_INSTALLED",
    "ERROR_QUOTA_EXHAUSTED",
    "ERROR_RATE_LIMITED",
    "ERROR_RESPONSE",
    "ERROR_SCHEMA",
    "ERROR_TIMEOUT",
    "ERROR_TOOL_USE",
    "KNOWN_ERROR_KINDS",
    "LLMBackend",
    "failure",
]

#: The child binary is not installed or not on ``PATH``.
ERROR_NOT_INSTALLED: Final = "not_installed"
#: Required configuration (for example a BYOK key or base URL) is missing.
ERROR_NOT_CONFIGURED: Final = "not_configured"
#: The call exceeded its timeout.
ERROR_TIMEOUT: Final = "timeout"
#: Login is missing, expired or rejected.
ERROR_AUTH: Final = "auth_error"
#: The provider reports a billing or payment problem (never retried as quota).
ERROR_BILLING: Final = "billing_error"
#: A plan or usage limit is exhausted; retrying now cannot succeed.
ERROR_QUOTA_EXHAUSTED: Final = "quota_exhausted"
#: A short-lived rate limit; a later retry may succeed.
ERROR_RATE_LIMITED: Final = "rate_limited"
#: A tool or command event appeared; the isolation contract was violated.
ERROR_TOOL_USE: Final = "tool_use"
#: The CLI or HTTP call failed for any other reason.
ERROR_CLI: Final = "cli_error"
#: An HTTP error status that is not auth, billing or rate limiting.
ERROR_HTTP: Final = "http_error"
#: The transport failed before a response was available.
ERROR_NETWORK: Final = "network_error"
#: The reply was empty where text was required.
ERROR_EMPTY: Final = "empty_response"
#: The provider response did not have the expected shape.
ERROR_RESPONSE: Final = "invalid_response"
#: The reply was not parseable JSON.
ERROR_INVALID_JSON: Final = "invalid_json"
#: The reply parsed but does not satisfy the requested JSON schema.
ERROR_SCHEMA: Final = "schema_error"

#: Every ``error_kind`` a backend may return. Documented in ``docs/backends.md``.
KNOWN_ERROR_KINDS: Final[frozenset[str]] = frozenset(
    {
        ERROR_AUTH,
        ERROR_BILLING,
        ERROR_CLI,
        ERROR_EMPTY,
        ERROR_HTTP,
        ERROR_INVALID_JSON,
        ERROR_NETWORK,
        ERROR_NOT_CONFIGURED,
        ERROR_NOT_INSTALLED,
        ERROR_QUOTA_EXHAUSTED,
        ERROR_RATE_LIMITED,
        ERROR_RESPONSE,
        ERROR_SCHEMA,
        ERROR_TIMEOUT,
        ERROR_TOOL_USE,
    }
)


@runtime_checkable
class LLMBackend(Protocol):
    """One LLM backend that returns a validated JSON object.

    Implementations must not log prompts, replies or credentials. ``complete_json``
    raises nothing for provider failures: it returns a :class:`BackendResult`
    whose ``error_kind`` names the failure.
    """

    name: str

    def is_available(self) -> bool:
        """Return whether this backend can be used right now (no network call)."""
        ...

    def complete_json(
        self,
        prompt: str,
        schema: dict[str, Any],
        timeout_s: int = 30,
    ) -> BackendResult:
        """Ask for one JSON object matching ``schema`` within ``timeout_s``."""
        ...


def failure(
    error_kind: str,
    latency_ms: int,
    *,
    text: str = "",
    model: str | None = None,
    usage: dict[str, Any] | None = None,
) -> BackendResult:
    """Build a failed :class:`BackendResult` without raising.

    ``error_kind`` must be one of :data:`KNOWN_ERROR_KINDS`; an unknown value is
    a programming error and raises ``ValueError``.
    """
    if error_kind not in KNOWN_ERROR_KINDS:
        raise ValueError(f"unknown error kind: {error_kind}")
    return BackendResult(
        text=text,
        parsed=None,
        model=model,
        latency_ms=max(0, latency_ms),
        usage=usage,
        error_kind=error_kind,
    )

"""Backend protocol shared by every LLM adapter."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from ai_privacy_filter.models import BackendResult


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

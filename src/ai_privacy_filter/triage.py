"""Triage prompt, JSON schema and parsing.

The prompt and parser land in T06/T12; the schema is fixed here in T01 so every
backend and the fake implementation share one contract. T01 ships typed stubs
for the functions.
"""

from __future__ import annotations

from typing import Any, Final

from ai_privacy_filter.backends.base import LLMBackend
from ai_privacy_filter.models import Category, TriageResult, Urgency

TRIAGE_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["category", "urgency", "reply_draft"],
    "properties": {
        "category": {
            "type": "string",
            "enum": [category.value for category in Category],
        },
        "urgency": {
            "type": "string",
            "enum": [urgency.value for urgency in Urgency],
        },
        "reply_draft": {"type": "string", "minLength": 1},
    },
}


def build_prompt(text: str) -> str:
    """Build the triage prompt for already-pseudonymised ``text``.

    The prompt asks for strict JSON and names the categories and urgency levels
    from :data:`TRIAGE_SCHEMA`. Raw personal data must never be passed here.
    """
    raise NotImplementedError("build_prompt lands in T06/T12")


def parse_triage(
    payload: dict[str, Any],
    *,
    backend: str,
    model: str | None,
    latency_ms: int,
) -> TriageResult:
    """Validate a backend payload and build a :class:`TriageResult`."""
    raise NotImplementedError("parse_triage lands in T06/T12")


def classify(
    text: str,
    backend: LLMBackend,
    *,
    timeout_s: int = 60,
) -> TriageResult:
    """Run prompt -> backend JSON call -> validated triage result."""
    raise NotImplementedError("classify lands in T06/T12")

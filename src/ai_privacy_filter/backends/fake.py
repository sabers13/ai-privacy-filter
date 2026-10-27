"""Deterministic fake LLM backend.

Deterministic fake-worker pattern adapted from SaberOps (Apache-2.0).

The fake backend exists so tests, the HTTP service and the page export can run
the whole pipeline with no network and no model download. Answers depend only on
a SHA-256 hash of the prompt, so the same input always produces the same output.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Final

from ai_privacy_filter.models import BackendResult, Category, Urgency

_PLACEHOLDER_RE: Final = re.compile(r"\[[A-Z][A-Z0-9_]*_\d+\]")

_TEMPLATES: Final[dict[Category, str]] = {
    Category.QUOTE_REQUEST: (
        "Guten Tag {who}, vielen Dank für Ihre Anfrage. "
        "Wir prüfen die Anforderungen und senden Ihnen ein Angebot zu."
    ),
    Category.SUPPORT: (
        "Guten Tag {who}, danke für Ihre Nachricht. "
        "Wir haben das Problem aufgenommen und melden uns mit einer Lösung."
    ),
    Category.JOB_APPLICATION: (
        "Guten Tag {who}, vielen Dank für Ihre Bewerbung. "
        "Wir prüfen Ihre Unterlagen und melden uns zum weiteren Ablauf."
    ),
    Category.PARTNERSHIP: (
        "Guten Tag {who}, danke für Ihr Interesse an einer Zusammenarbeit. "
        "Wir sehen uns die Eckpunkte an und schlagen einen Termin vor."
    ),
    Category.OTHER: (
        "Guten Tag {who}, vielen Dank für Ihre Nachricht. "
        "Wir prüfen Ihr Anliegen und melden uns zeitnah."
    ),
}

_URGENCY_SENTENCE: Final[dict[Urgency, str]] = {
    Urgency.LOW: " Eine Rückmeldung erfolgt in den kommenden Werktagen.",
    Urgency.NORMAL: "",
    Urgency.HIGH: " Da Ihr Anliegen dringend ist, priorisieren wir es.",
}


class FakeBackend:
    """Schema-valid, deterministic backend keyed by the prompt hash."""

    name: str = "fake"
    model: str = "fake-deterministic-v1"

    def is_available(self) -> bool:
        return True

    def complete_json(
        self,
        prompt: str,
        schema: dict[str, Any],
        timeout_s: int = 30,
    ) -> BackendResult:
        del schema, timeout_s  # the fake always speaks TRIAGE_SCHEMA
        digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        seed = int(digest[:16], 16)

        categories = list(Category)
        urgencies = list(Urgency)
        category = categories[seed % len(categories)]
        urgency = urgencies[(seed >> 16) % len(urgencies)]

        match = _PLACEHOLDER_RE.search(prompt)
        who = match.group(0) if match is not None else "Ihnen"
        reply_draft = _TEMPLATES[category].format(who=who) + _URGENCY_SENTENCE[urgency]

        parsed: dict[str, Any] = {
            "category": category.value,
            "urgency": urgency.value,
            "reply_draft": reply_draft,
        }
        text = json.dumps(parsed, ensure_ascii=False, sort_keys=True)
        latency_ms = 120 + (seed >> 32) % 80
        usage = {
            "prompt_tokens": len(prompt) // 4,
            "completion_tokens": len(text) // 4,
            "total_tokens": (len(prompt) + len(text)) // 4,
        }
        return BackendResult(
            text=text,
            parsed=parsed,
            model=self.model,
            latency_ms=latency_ms,
            usage=usage,
            error_kind=None,
        )

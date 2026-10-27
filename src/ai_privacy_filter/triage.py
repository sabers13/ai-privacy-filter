"""Triage prompt, JSON schema and parsing.

The prompt asks a backend for one JSON object with ``category``, ``urgency``
and a short German ``reply_draft``. It is built from already-pseudonymised text
only: the pipeline redacts and guards before calling :func:`build_prompt`.

The prompt and parser are frozen for the evaluation run described in
``METHOD.md`` section 10. Changing the prompt after a test number exists is a
protocol change and must go through the rerun workflow.

Privacy: a backend failure is reported by ``error_kind`` only. Prompt text and
replies are never logged here.
"""

from __future__ import annotations

from typing import Any, Final

from ai_privacy_filter.backends import schema as schema_validator
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

#: Stable instruction prefix. Kept short so the local 1.5B model fits its
#: context window with a full enquiry.
PROMPT_PREFIX: Final = """Du bist der Posteingang eines deutschen Unternehmens. \
Ordne die folgende Kundenanfrage ein und entwirf eine kurze Antwort.

Kategorien:
- QUOTE_REQUEST: Angebot, Preis oder Lieferumfang angefragt
- SUPPORT: Frage zu Produkt, Bestellung oder einem Problem
- JOB_APPLICATION: Bewerbung oder Anfrage zu einer Stelle
- PARTNERSHIP: Kooperation, Lieferant oder Vertriebspartnerschaft
- OTHER: alles andere

Dringlichkeit:
- HIGH: Frist, Ausfall, akutes Problem oder ausdrücklicher Zeitdruck
- NORMAL: übliche Bearbeitung
- LOW: keine Eile, reine Information

Antworte ausschließlich mit einem JSON-Objekt, ohne Erklärung und ohne Markdown:
{"category": "...", "urgency": "...", "reply_draft": "..."}

Regeln für reply_draft:
- kurzer, höflicher deutscher Text mit zwei bis vier Sätzen
- Platzhalter wie [PERSON_1], [ORG_1] oder [EMAIL_1] unverändert übernehmen
- keine neuen Platzhalter erfinden
- keine Fakten, Preise, Termine oder Zusagen erfinden
- keine Werkzeuge benutzen und keine Dateien lesen

Anfrage:
"""


class TriageError(RuntimeError):
    """A backend call or reply failed; carries the public ``error_kind``."""

    def __init__(self, error_kind: str, *, latency_ms: int = 0) -> None:
        super().__init__(f"triage backend failed: {error_kind}")
        self.error_kind = error_kind
        self.latency_ms = latency_ms


def build_prompt(text: str) -> str:
    """Build the triage prompt for already-pseudonymised ``text``.

    The prompt asks for strict JSON and names the categories and urgency levels
    from :data:`TRIAGE_SCHEMA`. Raw personal data must never be passed here.
    """
    return f"{PROMPT_PREFIX}{text.strip()}\n"


def parse_triage(
    payload: dict[str, Any],
    *,
    backend: str,
    model: str | None,
    latency_ms: int,
) -> TriageResult:
    """Validate a backend payload and build a :class:`TriageResult`.

    Raises:
        TriageError: with ``error_kind="schema_error"`` when the payload does
            not match :data:`TRIAGE_SCHEMA`.
    """
    error = schema_validator.validate(payload, TRIAGE_SCHEMA)
    if error is not None:
        raise TriageError("schema_error", latency_ms=latency_ms)
    try:
        category = Category(payload["category"])
        urgency = Urgency(payload["urgency"])
    except (KeyError, ValueError) as exc:
        raise TriageError("schema_error", latency_ms=latency_ms) from exc
    draft = payload["reply_draft"]
    if not isinstance(draft, str) or not draft.strip():
        raise TriageError("schema_error", latency_ms=latency_ms)
    return TriageResult(
        category=category,
        urgency=urgency,
        reply_draft=draft,
        backend=backend,
        model=model,
        latency_ms=max(0, latency_ms),
    )


def classify(
    text: str,
    backend: LLMBackend,
    *,
    timeout_s: int = 60,
) -> TriageResult:
    """Run prompt -> backend JSON call -> validated triage result.

    Raises:
        TriageError: when the backend reports an ``error_kind`` or returns a
            payload that does not satisfy :data:`TRIAGE_SCHEMA`.
    """
    prompt = build_prompt(text)
    result = backend.complete_json(prompt, TRIAGE_SCHEMA, timeout_s)
    if result.error_kind is not None:
        raise TriageError(result.error_kind, latency_ms=result.latency_ms)
    if result.parsed is None:
        raise TriageError("invalid_response", latency_ms=result.latency_ms)
    return parse_triage(
        result.parsed,
        backend=backend.name,
        model=result.model,
        latency_ms=result.latency_ms,
    )

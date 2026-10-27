"""Page data files follow the T01 schemas."""

from __future__ import annotations

from ai_privacy_filter.models import (
    Category,
    PageExample,
    PageResults,
    Urgency,
)

EXAMPLES_JSON = """
[
  {
    "id": "example-001",
    "original": "Guten Tag, bitte senden Sie ein Angebot.",
    "redacted": "Guten Tag, bitte senden Sie ein Angebot.",
    "reply_draft_redacted": "Guten Tag [PERSON_1], wir melden uns.",
    "reply_draft_restored": "Guten Tag [PERSON_1], wir melden uns.",
    "spans": [],
    "misses": [],
    "category": "QUOTE_REQUEST",
    "urgency": "NORMAL",
    "backend": "fake",
    "mock": true
  }
]
"""

RESULTS_JSON = """
{
  "detection": {"germeval": {"person_recall": 0.0}, "synthetic": {}},
  "triage": {"per_backend": {"fake": {"category_accuracy": 0.0}}},
  "meta": {
    "version": "0.1.0",
    "commit": "0000000",
    "generated_at": "2026-10-27T00:00:00Z",
    "sha256": "0000000000000000000000000000000000000000000000000000000000000000",
    "mock": true
  }
}
"""


def test_examples_json_parses() -> None:
    import json

    examples = [PageExample.model_validate(item) for item in json.loads(EXAMPLES_JSON)]
    assert examples[0].category is Category.QUOTE_REQUEST
    assert examples[0].urgency is Urgency.NORMAL
    assert examples[0].mock is True


def test_results_json_parses() -> None:
    results = PageResults.model_validate_json(RESULTS_JSON)
    assert results.detection.germeval["person_recall"] == 0.0
    assert "fake" in results.triage.per_backend
    assert results.meta.mock is True

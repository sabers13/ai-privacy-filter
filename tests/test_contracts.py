"""Contract shape and validation tests for T01."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from ai_privacy_filter.models import (
    BackendResult,
    Category,
    DetectionResults,
    Enquiry,
    EntityType,
    GuardVerdict,
    PageExample,
    PageMeta,
    PageResults,
    RedactionResult,
    RestoreResult,
    Span,
    TriageResult,
    TriageResults,
    Urgency,
)


def _span(**overrides: object) -> Span:
    values: dict[str, object] = {
        "start": 0,
        "end": 11,
        "type": EntityType.PERSON,
        "text": "Platzhalter",
        "score": 0.9,
        "source": "unit-test",
    }
    values.update(overrides)
    return Span.model_validate(values)


def test_entity_type_members_are_exact() -> None:
    assert [member.value for member in EntityType] == [
        "PERSON",
        "ORG",
        "EMAIL",
        "PHONE",
        "IBAN",
        "ADDRESS_STREET",
        "POSTCODE_CITY",
        "URL",
        "VAT_ID",
        "DATE_OF_BIRTH",
    ]


def test_category_and_urgency_members() -> None:
    assert [member.value for member in Category] == [
        "QUOTE_REQUEST",
        "SUPPORT",
        "JOB_APPLICATION",
        "PARTNERSHIP",
        "OTHER",
    ]
    assert [member.value for member in Urgency] == ["LOW", "NORMAL", "HIGH"]


def test_span_round_trip() -> None:
    span = _span()
    assert Span.model_validate_json(span.model_dump_json()) == span
    assert span.text[span.start : span.end] == span.text


def test_span_rejects_end_before_start() -> None:
    with pytest.raises(ValidationError):
        _span(start=5, end=4)


def test_span_rejects_out_of_range_score() -> None:
    with pytest.raises(ValidationError):
        _span(score=1.5)
    with pytest.raises(ValidationError):
        _span(score=-0.1)


def test_span_requires_non_empty_source() -> None:
    with pytest.raises(ValidationError):
        _span(source="")


def test_contracts_are_frozen_and_closed() -> None:
    span = _span()
    with pytest.raises(ValidationError):
        span.start = 1
    with pytest.raises(ValidationError):
        _span(unexpected="value")


def test_redaction_result_hides_mapping_in_repr() -> None:
    result = RedactionResult(
        redacted_text="Guten Tag [PERSON_1]",
        mapping={"[PERSON_1]": "Synthetic Name"},
        spans=[_span()],
    )
    assert "Synthetic Name" not in repr(result)
    assert "mapping" not in repr(result)


def test_restore_result_reports_unknown_placeholders() -> None:
    result = RestoreResult(text="Hallo [PERSON_9]", unknown_placeholders=["[PERSON_9]"])
    assert result.unknown_placeholders == ["[PERSON_9]"]


def test_guard_verdict_ok_requires_no_leaks() -> None:
    assert GuardVerdict(ok=True).leaked_types == []
    with pytest.raises(ValidationError):
        GuardVerdict(ok=True, leaked_types=[EntityType.EMAIL])
    verdict = GuardVerdict(ok=False, leaked_types=[EntityType.EMAIL])
    assert verdict.ok is False


def test_guard_verdict_has_no_value_field() -> None:
    assert set(GuardVerdict.model_fields) == {"ok", "leaked_types"}


def test_triage_result_allows_null_model() -> None:
    result = TriageResult(
        category=Category.SUPPORT,
        urgency=Urgency.HIGH,
        reply_draft="Danke.",
        backend="fake",
        model=None,
        latency_ms=0,
    )
    assert result.model is None


def test_triage_result_rejects_negative_latency() -> None:
    with pytest.raises(ValidationError):
        TriageResult(
            category=Category.OTHER,
            urgency=Urgency.LOW,
            reply_draft="Danke.",
            backend="fake",
            latency_ms=-1,
        )


def test_backend_result_success_and_failure_shapes() -> None:
    ok = BackendResult(
        text='{"category": "OTHER"}',
        parsed={"category": "OTHER"},
        model="fake-deterministic-v1",
        latency_ms=12,
        usage={"completion_tokens": 3},
    )
    assert ok.error_kind is None
    failed = BackendResult(text="", latency_ms=5, error_kind="timeout")
    assert failed.parsed is None


def test_enquiry_split_is_closed() -> None:
    enquiry = Enquiry(
        id="synthetic-001",
        split="dev",
        style="web_form",
        text="Synthetic text",
        gold_spans=[],
        gold_category=Category.OTHER,
        gold_urgency=Urgency.NORMAL,
    )
    assert enquiry.split == "dev"
    with pytest.raises(ValidationError):
        Enquiry(
            id="synthetic-002",
            split="train",
            style="web_form",
            text="Synthetic text",
            gold_category=Category.OTHER,
            gold_urgency=Urgency.NORMAL,
        )


def test_page_example_and_results_round_trip() -> None:
    example = PageExample(
        id="example-001",
        original="Synthetic text",
        redacted="Synthetic text",
        reply_draft_redacted="Danke.",
        reply_draft_restored="Danke.",
        spans=[],
        misses=[],
        category=Category.QUOTE_REQUEST,
        urgency=Urgency.NORMAL,
        backend="fake",
        mock=True,
    )
    assert PageExample.model_validate_json(example.model_dump_json()) == example

    results = PageResults(
        detection=DetectionResults(germeval={}, synthetic={}),
        triage=TriageResults(per_backend={}),
        meta=PageMeta(
            version="0.1.0",
            commit="0" * 7,
            generated_at=datetime(2026, 10, 27, tzinfo=UTC),
            sha256="0" * 64,
            mock=True,
        ),
    )
    assert PageResults.model_validate_json(results.model_dump_json()) == results

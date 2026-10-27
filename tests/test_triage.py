"""Triage prompt, parsing and the classify glue."""

from __future__ import annotations

from typing import Any

import pytest

from ai_privacy_filter.backends.base import failure
from ai_privacy_filter.backends.fake import FakeBackend
from ai_privacy_filter.models import BackendResult, Category, Urgency
from ai_privacy_filter.triage import (
    TRIAGE_SCHEMA,
    TriageError,
    build_prompt,
    classify,
    parse_triage,
)


class _FailingBackend:
    name = "failing"

    def is_available(self) -> bool:
        return True

    def complete_json(
        self, prompt: str, schema: dict[str, Any], timeout_s: int = 30
    ) -> BackendResult:
        del prompt, schema, timeout_s
        return failure("timeout", 4321)


class _SchemaBreakingBackend:
    name = "broken"

    def is_available(self) -> bool:
        return True

    def complete_json(
        self, prompt: str, schema: dict[str, Any], timeout_s: int = 30
    ) -> BackendResult:
        del prompt, schema, timeout_s
        return BackendResult(
            text='{"category": "NOPE"}',
            parsed={"category": "NOPE"},
            model="broken-1",
            latency_ms=17,
            usage=None,
            error_kind=None,
        )


def test_build_prompt_keeps_placeholders_and_asks_for_json() -> None:
    prompt = build_prompt("Guten Tag [PERSON_1], bitte um ein Angebot.")
    assert "[PERSON_1]" in prompt
    assert "QUOTE_REQUEST" in prompt
    assert "JSON" in prompt
    assert "reply_draft" in prompt
    assert prompt.endswith("\n")


def test_parse_triage_builds_the_contract() -> None:
    result = parse_triage(
        {"category": "SUPPORT", "urgency": "HIGH", "reply_draft": "Guten Tag [PERSON_1]."},
        backend="fake",
        model="fake-deterministic-v1",
        latency_ms=42,
    )
    assert result.category is Category.SUPPORT
    assert result.urgency is Urgency.HIGH
    assert result.backend == "fake"
    assert result.latency_ms == 42


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"category": "SUPPORT", "urgency": "HIGH"},
        {"category": "SUPPORT", "urgency": "HIGH", "reply_draft": ""},
        {"category": "SUPPORT", "urgency": "HIGH", "reply_draft": "x", "extra": 1},
        {"category": "NOPE", "urgency": "HIGH", "reply_draft": "x"},
    ],
)
def test_parse_triage_rejects_invalid_payloads(payload: dict[str, Any]) -> None:
    with pytest.raises(TriageError) as caught:
        parse_triage(payload, backend="fake", model=None, latency_ms=0)
    assert caught.value.error_kind == "schema_error"


def test_classify_uses_the_backend_and_validates() -> None:
    result = classify("Guten Tag [PERSON_1], bitte um ein Angebot.", FakeBackend())
    assert result.backend == "fake"
    assert result.category in set(Category)
    assert result.urgency in set(Urgency)
    assert result.reply_draft.strip()


def test_classify_reports_backend_error_kind() -> None:
    with pytest.raises(TriageError) as caught:
        classify("Anfrage", _FailingBackend())  # type: ignore[arg-type]
    assert caught.value.error_kind == "timeout"
    assert caught.value.latency_ms == 4321


def test_classify_reports_schema_error() -> None:
    with pytest.raises(TriageError) as caught:
        classify("Anfrage", _SchemaBreakingBackend())  # type: ignore[arg-type]
    assert caught.value.error_kind == "schema_error"


def test_schema_is_the_frozen_contract() -> None:
    assert TRIAGE_SCHEMA["required"] == ["category", "urgency", "reply_draft"]
    assert TRIAGE_SCHEMA["additionalProperties"] is False
    assert TRIAGE_SCHEMA["properties"]["category"]["enum"] == [
        "QUOTE_REQUEST",
        "SUPPORT",
        "JOB_APPLICATION",
        "PARTNERSHIP",
        "OTHER",
    ]
    assert TRIAGE_SCHEMA["properties"]["urgency"]["enum"] == ["LOW", "NORMAL", "HIGH"]

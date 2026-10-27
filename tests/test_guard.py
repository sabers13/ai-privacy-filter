"""Outbound leak guard: planted leaks are blocked, nothing leaks into logs."""

from __future__ import annotations

import logging

import pytest

from ai_privacy_filter.guard import GuardBlockedError, guard, require_clean
from ai_privacy_filter.models import BackendResult, EntityType, Span
from ai_privacy_filter.pseudonymise import redact
from ai_privacy_filter.triage import TRIAGE_SCHEMA


def _redact(text: str, spans: list[Span]) -> dict[str, str]:
    return dict(redact(text, spans).mapping)


def _span(text: str, needle: str, entity_type: EntityType) -> Span:
    start = text.index(needle)
    return Span(
        start=start,
        end=start + len(needle),
        type=entity_type,
        text=needle,
        score=0.9,
        source="unit-test",
    )


class _Recorder:
    """Minimal backend that remembers whether it was called."""

    name = "recorder"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def is_available(self) -> bool:
        return True

    def complete_json(
        self,
        prompt: str,
        schema: dict[str, object],
        timeout_s: int = 30,
    ) -> BackendResult:
        del schema, timeout_s
        self.calls.append(prompt)
        return BackendResult(text="{}", latency_ms=0)


def test_clean_redacted_text_passes() -> None:
    text = "Anna Müller schreibt an anna@example.org."
    mapping = _redact(
        text,
        [
            _span(text, "Anna Müller", EntityType.PERSON),
            _span(text, "anna@example.org", EntityType.EMAIL),
        ],
    )
    verdict = guard("[PERSON_1] schreibt an [EMAIL_1].", mapping)
    assert verdict.ok is True
    assert verdict.leaked_types == []


def test_empty_mapping_passes() -> None:
    verdict = guard("Nichts zu prüfen.", {})
    assert verdict.ok is True
    assert verdict.leaked_types == []


def test_exact_leak_is_blocked() -> None:
    text = "Anna Müller"
    mapping = _redact(text, [_span(text, text, EntityType.PERSON)])
    verdict = guard("Rückruf bei Anna Müller erbeten.", mapping)
    assert verdict.ok is False
    assert verdict.leaked_types == [EntityType.PERSON]


def test_case_shifted_leak_is_blocked() -> None:
    text = "Anna Müller"
    mapping = _redact(text, [_span(text, text, EntityType.PERSON)])
    assert guard("Rückruf bei ANNA MÜLLER erbeten.", mapping).ok is False
    assert guard("Rückruf bei anna müller erbeten.", mapping).ok is False


def test_whitespace_shifted_leak_is_blocked() -> None:
    text = "Anna Müller"
    mapping = _redact(text, [_span(text, text, EntityType.PERSON)])
    verdict = guard("Rückruf bei Anna \n  Müller erbeten.", mapping)
    assert verdict.ok is False
    assert verdict.leaked_types == [EntityType.PERSON]


def test_digit_stripped_phone_leak_is_blocked() -> None:
    text = "Rückruf an 0170 1234567."
    mapping = _redact(text, [_span(text, "0170 1234567", EntityType.PHONE)])
    for leaky in ("01701234567", "0170/1234567", "+49 (0)170 1234567"):
        verdict = guard(leaky, mapping)
        assert verdict.ok is False, leaky
        assert verdict.leaked_types == [EntityType.PHONE]


def test_digit_stripped_iban_leak_is_blocked() -> None:
    text = "IBAN DE89 3704 0044 0532 0130 00"
    mapping = _redact(text, [_span(text, "DE89 3704 0044 0532 0130 00", EntityType.IBAN)])
    verdict = guard("Konto DE89370400440532013000", mapping)
    assert verdict.ok is False
    assert verdict.leaked_types == [EntityType.IBAN]


def test_reformatted_but_complete_iban_leak_is_blocked() -> None:
    text = "IBAN DE89 3704 0044 0532 0130 00"
    mapping = _redact(text, [_span(text, "DE89 3704 0044 0532 0130 00", EntityType.IBAN)])
    verdict = guard("Konto de89 3704 0044 0532 0130 00", mapping)
    assert verdict.ok is False
    assert verdict.leaked_types == [EntityType.IBAN]


def test_multiple_leaks_report_each_type_once() -> None:
    text = "Anna Müller, 0170 1234567, anna@example.org"
    mapping = _redact(
        text,
        [
            _span(text, "Anna Müller", EntityType.PERSON),
            _span(text, "0170 1234567", EntityType.PHONE),
            _span(text, "anna@example.org", EntityType.EMAIL),
        ],
    )
    verdict = guard("Anna Müller (0170 1234567) und ANNA@EXAMPLE.ORG", mapping)
    assert verdict.ok is False
    assert verdict.leaked_types == [EntityType.PERSON, EntityType.PHONE, EntityType.EMAIL]


def test_placeholder_only_text_passes() -> None:
    mapping = {"[PERSON_1]": "Anna Müller", "[PHONE_1]": "0170 1234567"}
    verdict = guard("Guten Tag [PERSON_1], Rückruf an [PHONE_1].", mapping)
    assert verdict.ok is True


def test_verdict_and_exception_carry_no_values() -> None:
    text = "Anna Müller"
    mapping = _redact(text, [_span(text, text, EntityType.PERSON)])
    verdict = guard(text, mapping)
    assert "Anna Müller" not in repr(verdict)
    with pytest.raises(GuardBlockedError) as raised:
        require_clean(text, mapping)
    assert "Anna Müller" not in str(raised.value)
    assert "Anna Müller" not in repr(raised.value.verdict)


def test_guard_does_not_log_values(caplog: pytest.LogCaptureFixture) -> None:
    text = "Anna Müller, 0170 1234567"
    mapping = _redact(
        text,
        [
            _span(text, "Anna Müller", EntityType.PERSON),
            _span(text, "0170 1234567", EntityType.PHONE),
        ],
    )
    with caplog.at_level(logging.DEBUG):
        verdict = guard(text, mapping)
    assert verdict.ok is False
    assert "Anna Müller" not in caplog.text
    assert "0170 1234567" not in caplog.text


def test_require_clean_blocks_before_the_backend_call() -> None:
    text = "Anna Müller"
    mapping = _redact(text, [_span(text, text, EntityType.PERSON)])
    backend = _Recorder()

    def pipeline(redacted_text: str) -> BackendResult:
        require_clean(redacted_text, mapping)
        return backend.complete_json(redacted_text, TRIAGE_SCHEMA, 5)

    with pytest.raises(GuardBlockedError):
        pipeline("Rückruf bei Anna Müller")
    assert backend.calls == []


def test_require_clean_returns_the_verdict_for_clean_text() -> None:
    mapping = {"[PERSON_1]": "Anna Müller"}
    verdict = require_clean("Guten Tag [PERSON_1]", mapping)
    assert verdict.ok is True


def test_unknown_placeholder_type_still_blocks() -> None:
    verdict = guard("Anna Müller", {"[MYSTERY_1]": "Anna Müller"})
    assert verdict.ok is False
    assert verdict.leaked_types == []


def test_short_values_are_not_reported_as_leaks_by_digits_alone() -> None:
    mapping = {"[PHONE_1]": "0170 1234567"}
    assert guard("Die Zahl 0170 steht im Text.", mapping).ok is True

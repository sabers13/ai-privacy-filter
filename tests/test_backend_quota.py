"""Quota and rate-limit classification rules."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from ai_privacy_filter.backends.quota import (
    QuotaVerdict,
    classify_claude,
    classify_codex,
    classify_quota,
    reset_to_iso,
)

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)


def test_claude_fixture_quota_extracts_zoned_reset() -> None:
    verdict = classify_claude(
        '{"type": "result", "subtype": "success", "is_error": true,'
        ' "api_error_status": 429, "result": "You\'ve hit your weekly limit.'
        ' Your limit resets Oct 10, 2am (UTC)."}',
        now=NOW,
    )
    assert verdict.verdict is QuotaVerdict.QUOTA_EXHAUSTED
    assert verdict.reset_at == datetime(2026, 10, 10, 2, 0, tzinfo=UTC)
    assert verdict.rule == "claude:usage-limit"


def test_claude_structured_reset_wins() -> None:
    epoch = int(datetime(2026, 2, 2, 2, 40, tzinfo=UTC).timestamp())
    verdict = classify_claude(
        '{"type": "assistant", "api_error": {"api_error": "usage_limit_reached",'
        f' "resetsAt": {epoch}}}}}',
        now=NOW,
    )
    assert verdict.verdict is QuotaVerdict.QUOTA_EXHAUSTED
    assert verdict.reset_at == datetime(2026, 2, 2, 2, 40, tzinfo=UTC)


def test_claude_rate_limit() -> None:
    verdict = classify_claude(
        '{"type": "system", "subtype": "api_retry", "message": "synthetic"}', now=NOW
    )
    assert verdict.verdict is QuotaVerdict.RATE_LIMITED
    assert verdict.rule == "claude:rate-limit"


@pytest.mark.parametrize(
    ("text", "rule"),
    [
        ('{"type": "result", "is_error": true, "result": "Invalid API key"}', "claude:auth"),
        (
            '{"type": "result", "is_error": true, "result": "enforced_spend_limit_reached"}',
            "claude:spend-cap",
        ),
        (
            '{"type": "result", "is_error": true, "result": "Add a payment method"}',
            "claude:billing",
        ),
        ('{"type": "result", "is_error": true, "result": "overloaded"}', "claude:throttle"),
        ('{"type": "result", "is_error": false, "result": "done"}', "claude:other"),
    ],
)
def test_claude_non_quota_outcomes(text: str, rule: str) -> None:
    verdict = classify_claude(text, now=NOW)
    assert verdict.verdict is QuotaVerdict.OTHER
    assert verdict.rule == rule


def test_codex_quota() -> None:
    verdict = classify_codex(
        '{"type": "turn.failed", "error": {"message": "You\'ve hit your usage limit."}}',
        now=NOW,
    )
    assert verdict.verdict is QuotaVerdict.QUOTA_EXHAUSTED
    assert verdict.reset_at is None


def test_codex_rate_limit_reset_is_relative() -> None:
    verdict = classify_codex(
        '{"type": "error", "message": "Rate limit reached for m on tokens per min.'
        ' Please try again in 20s."}',
        now=NOW,
    )
    assert verdict.verdict is QuotaVerdict.RATE_LIMITED
    assert verdict.reset_at == NOW + timedelta(seconds=20)


def test_codex_without_terminal_event_is_other() -> None:
    verdict = classify_codex('{"type": "item.completed", "item": {"type": "agent_message"}}')
    assert verdict.verdict is QuotaVerdict.OTHER
    assert verdict.rule == "codex:no-terminal-event"


@pytest.mark.parametrize(
    ("text", "rule", "expected"),
    [
        (
            '{"type": "error", "message": "Not authenticated"}',
            "codex:auth",
            QuotaVerdict.OTHER,
        ),
        (
            '{"type": "error", "message": "You have hit your usage limit"}',
            "codex:usage-limit",
            QuotaVerdict.QUOTA_EXHAUSTED,
        ),
        (
            '{"type": "error", "message": "At capacity"}',
            "codex:capacity",
            QuotaVerdict.OTHER,
        ),
        (
            '{"type": "turn.failed", "error": {"message": "Unexpected failure"}}',
            "codex:other",
            QuotaVerdict.OTHER,
        ),
    ],
)
def test_codex_rules(text: str, rule: str, expected: QuotaVerdict) -> None:
    verdict = classify_codex(text, now=NOW)
    assert verdict.rule == rule
    assert verdict.verdict is expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", None),
        ("You've hit your usage limit", "QUOTA_EXHAUSTED"),
        ("insufficient_quota", "QUOTA_EXHAUSTED"),
        ("Payment required", None),
        ("Not authenticated", None),
        ("HTTP 429 too many requests", "RATE_LIMITED"),
        ("something else", None),
    ],
)
def test_generic_text_classifier(text: str, expected: str | None) -> None:
    assert classify_quota(text) == expected


def test_reset_to_iso_is_utc_and_naive_is_assumed_utc() -> None:
    assert reset_to_iso(None) is None
    assert reset_to_iso(datetime(2026, 1, 2, 3, 4, tzinfo=UTC)) == "2026-01-02T03:04:00+00:00"
    assert reset_to_iso(datetime(2026, 1, 2, 3, 4)) == "2026-01-02T03:04:00+00:00"

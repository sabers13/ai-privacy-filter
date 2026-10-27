"""Quota and rate-limit classification for native CLI backends.

Adapted from SaberOps, Apache-2.0 (``src/saberops/workers/exhaustion.py``,
T-004 / R-001). This port keeps the classification rules and the structured
reset extraction; the routing pool, quota-observation persistence and
control-plane exclusion logic are intentionally dropped.

Three outcomes are possible:

* ``QUOTA_EXHAUSTED`` -- a plan or usage window is spent; retrying now cannot
  succeed.
* ``RATE_LIMITED`` -- a short-lived limit; a later retry may succeed.
* ``OTHER`` -- neither (including authentication, billing and spend-cap
  errors, which are never treated as quota).

Matching order follows R-001: structured fields first, prose second. Auth and
billing errors are checked before quota so a rejected key or a payment problem
is never retried as a plan window. A prose date without a timezone is never
guessed; ``reset_at`` stays ``None`` instead.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

__all__ = [
    "QuotaClassification",
    "QuotaVerdict",
    "classify_claude",
    "classify_codex",
    "classify_quota",
    "looks_like_auth_error",
    "looks_like_billing_error",
    "reset_to_iso",
]


class QuotaVerdict(StrEnum):
    """Outcome of quota classification."""

    QUOTA_EXHAUSTED = "QUOTA_EXHAUSTED"
    RATE_LIMITED = "RATE_LIMITED"
    OTHER = "OTHER"


@dataclass(frozen=True)
class QuotaClassification:
    """Pure result of classifying one CLI invocation's captured output."""

    verdict: QuotaVerdict
    reset_at: datetime | None
    rule: str


def reset_to_iso(reset_at: datetime | None) -> str | None:
    """Render a reset moment as canonical UTC ISO-8601, or ``None``."""
    if reset_at is None:
        return None
    aware = reset_at if reset_at.tzinfo is not None else reset_at.replace(tzinfo=UTC)
    return aware.astimezone(UTC).isoformat()


def _utcnow(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(UTC)
    return now.astimezone(UTC) if now.tzinfo is not None else now.replace(tzinfo=UTC)


# ---------------------------------------------------------------------------
# Cross-cutting guards: auth and billing errors are never quota.
# ---------------------------------------------------------------------------

_AUTH_RE = re.compile(
    r"not authenticated|unauthenticated|unauthorized|authentication (required|failed)"
    r"|invalid[ _-]?api[ _-]?key|api[ _-]?key (required|missing|invalid)"
    r"|login required|sign in|credentials|auth_required|subscription_inactive"
    r"|subscription inactive|unknown or disabled provider|permission denied",
    re.IGNORECASE,
)

_BILLING_RE = re.compile(
    r"payment required|payment method|add (a )?payment|billing error|invoice"
    r"|top up|out of credits|insufficient credit|\b402\b|enforced_spend_limit_reached",
    re.IGNORECASE,
)


def looks_like_auth_error(text: str) -> bool:
    """Return whether ``text`` reads as a missing, expired or rejected login."""
    return _AUTH_RE.search(text) is not None


def looks_like_billing_error(text: str) -> bool:
    """Return whether ``text`` reads as a billing or payment problem."""
    return _BILLING_RE.search(text) is not None


def _json_objects(raw: str) -> list[dict[str, Any]]:
    """Parse JSONL or one JSON object into a list of dictionaries."""
    objects: list[dict[str, Any]] = []
    stripped = raw.strip()
    if not stripped:
        return objects
    try:
        payload = json.loads(stripped)
    except (json.JSONDecodeError, TypeError):
        payload = None
    if isinstance(payload, dict):
        return [payload]
    for line in stripped.splitlines():
        try:
            item = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(item, dict):
            objects.append(item)
    return objects


# ---------------------------------------------------------------------------
# Codex CLI -- ``codex exec --json`` (JSONL).
# ---------------------------------------------------------------------------

_CODEX_QUOTA_RE = re.compile(
    r"you('ve| have) hit your usage limit|usage limit (has been )?(reached|exceeded)",
    re.IGNORECASE,
)
_CODEX_RATE_RE = re.compile(
    r"rate limit reached for .* on tokens per min|rate_limit_exceeded"
    r"|please try again in [\d.]+s",
    re.IGNORECASE,
)
_CODEX_CAPACITY_RE = re.compile(r"at capacity|exceeded retry limit.*429", re.IGNORECASE)
_CODEX_RETRY_IN_RE = re.compile(r"please try again in ([\d.]+)s", re.IGNORECASE)


def classify_codex(
    stdout: str, stderr: str = "", *, now: datetime | None = None
) -> QuotaClassification:
    """Classify Codex ``exec --json`` output.

    A stream without a ``turn.failed`` or top-level ``error`` event is
    ``OTHER``: an interrupted turn emits no terminal event, so stream end alone
    is never read as a limit. ``stderr`` is ignored because the JSON stream is
    the terminal channel.
    """
    del stderr
    messages: list[str] = []
    saw_terminal_failure = False
    for event in _json_objects(stdout):
        event_type = event.get("type")
        if event_type == "turn.failed":
            error = event.get("error")
            text = error.get("message") if isinstance(error, dict) else None
            if isinstance(text, str) and text:
                messages.append(text)
                saw_terminal_failure = True
        elif event_type == "error":
            text = event.get("message")
            if isinstance(text, str) and text:
                messages.append(text)
                saw_terminal_failure = True
    if not saw_terminal_failure:
        return QuotaClassification(QuotaVerdict.OTHER, None, "codex:no-terminal-event")
    combined = "\n".join(messages)
    if _AUTH_RE.search(combined):
        return QuotaClassification(QuotaVerdict.OTHER, None, "codex:auth")
    if _CODEX_QUOTA_RE.search(combined):
        return QuotaClassification(QuotaVerdict.QUOTA_EXHAUSTED, None, "codex:usage-limit")
    if _BILLING_RE.search(combined):
        return QuotaClassification(QuotaVerdict.OTHER, None, "codex:billing")
    if _CODEX_RATE_RE.search(combined):
        reset = _relative_reset(combined, now)
        return QuotaClassification(QuotaVerdict.RATE_LIMITED, reset, "codex:rate-limit")
    if _CODEX_CAPACITY_RE.search(combined):
        return QuotaClassification(QuotaVerdict.OTHER, None, "codex:capacity")
    return QuotaClassification(QuotaVerdict.OTHER, None, "codex:other")


def _relative_reset(text: str, now: datetime | None) -> datetime | None:
    match = _CODEX_RETRY_IN_RE.search(text)
    if match is None:
        return None
    try:
        seconds = float(match.group(1))
    except ValueError:  # pragma: no cover - regex guarantees a float
        return None
    return _utcnow(now) + timedelta(seconds=seconds)


# ---------------------------------------------------------------------------
# Claude Code -- ``claude -p --output-format json`` (one result object).
# ---------------------------------------------------------------------------

_CLAUDE_QUOTA_PROSE_RE = re.compile(
    r"you've hit your (session|weekly|opus|sonnet) limit|monthly spend limit"
    r"|weekly limit resets|session limit",
    re.IGNORECASE,
)
_CLAUDE_THROTTLE_RE = re.compile(
    r"server is temporarily limiting requests|overloaded|\b529\b", re.IGNORECASE
)
_CLAUDE_RATE_RE = re.compile(r"request rejected ?\(429\)", re.IGNORECASE)
_CLAUDE_ZONED_RESET_RE = re.compile(
    r"resets?\b(?P<body>[^.\n]*?)\((?P<zone>[A-Za-z_][A-Za-z0-9_+\-/]*)\)", re.IGNORECASE
)
_MONTH_RE = re.compile(
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{1,2}"
    r"(?:st|nd|rd|th)?(?:,?\s+\d{4})?",
    re.IGNORECASE,
)
_MONTH_NUMBER = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}
_CLOCK_RE = re.compile(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)", re.IGNORECASE)


def _epoch_to_utc(value: object) -> datetime | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    seconds = float(value)
    if seconds >= 1e12:  # milliseconds
        seconds /= 1000.0
    if not 1e9 <= seconds < 1e11:
        return None
    try:
        return datetime.fromtimestamp(seconds, tz=UTC)
    except (OverflowError, OSError, ValueError):  # pragma: no cover - platform
        return None


def _iso_to_utc(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _parse_zoned_reset(body: str, zone: str, now: datetime) -> datetime | None:
    """Parse ``<month day[, year], clock>`` plus a zone into UTC.

    A clock without a calendar date is not guessed.
    """
    month_match = _MONTH_RE.search(body)
    clock_match = _CLOCK_RE.search(body)
    if month_match is None or clock_match is None:
        return None
    try:
        from zoneinfo import ZoneInfo

        tz = ZoneInfo("UTC" if zone.upper() == "UTC" else zone)
    except Exception:
        return None
    token = month_match.group(0)
    month = _MONTH_NUMBER[token[:3].lower()]
    day_match = re.search(r"\d{1,2}", token[3:] or token)
    if day_match is None:  # pragma: no cover - regex guarantees a day
        return None
    day = int(day_match.group(0))
    year_match = re.search(r"\d{4}", token)
    year = int(year_match.group(0)) if year_match else now.astimezone(tz).year
    hour = int(clock_match.group(1)) % 12
    if clock_match.group(3).lower() == "pm":
        hour += 12
    minute = int(clock_match.group(2)) if clock_match.group(2) else 0
    try:
        return datetime(year, month, day, hour, minute, tzinfo=tz).astimezone(UTC)
    except ValueError:
        return None


def _claude_reset(objects: list[dict[str, Any]], now: datetime) -> datetime | None:
    for event in objects:
        for container in (
            event,
            event.get("api_error"),
            event.get("rate_limit_info"),
        ):
            if not isinstance(container, dict):
                continue
            reset = _epoch_to_utc(container.get("resetsAt"))
            if reset is not None:
                return reset
        reset = _iso_to_utc(event.get("resets_at"))
        if reset is not None:
            return reset
        for key in ("result", "message"):
            text = event.get(key)
            if isinstance(text, str):
                match = _CLAUDE_ZONED_RESET_RE.search(text)
                if match is not None:
                    parsed = _parse_zoned_reset(match.group("body"), match.group("zone"), now)
                    if parsed is not None:
                        return parsed
    return None


def classify_claude(
    stdout: str, stderr: str = "", *, now: datetime | None = None
) -> QuotaClassification:
    """Classify Claude ``--output-format json`` output.

    A plan limit can arrive as ``subtype="success"`` together with
    ``is_error=true`` (R-001): treating the subtype as success misses every real
    limit event, so ``is_error`` and the structured 429 marker decide.
    """
    del stderr
    moment = _utcnow(now)
    objects = _json_objects(stdout)
    raw = stdout
    if _AUTH_RE.search(raw):
        return QuotaClassification(QuotaVerdict.OTHER, None, "claude:auth")
    if "enforced_spend_limit_reached" in raw:
        return QuotaClassification(QuotaVerdict.OTHER, None, "claude:spend-cap")
    if _BILLING_RE.search(raw):
        return QuotaClassification(QuotaVerdict.OTHER, None, "claude:billing")
    if _CLAUDE_THROTTLE_RE.search(raw):
        return QuotaClassification(QuotaVerdict.OTHER, None, "claude:throttle")

    saw_error_result = any(
        event.get("type") == "result" and event.get("is_error") is True for event in objects
    )
    saw_usage_marker = '"usage_limit_reached"' in raw or any(
        isinstance(event.get("api_error"), dict)
        and event["api_error"].get("api_error") == "usage_limit_reached"
        for event in objects
    )
    quota_result = any(
        event.get("type") == "result"
        and event.get("is_error") is True
        and (
            event.get("api_error_status") == 429
            or (
                isinstance(event.get("result"), str)
                and _CLAUDE_QUOTA_PROSE_RE.search(event["result"]) is not None
            )
        )
        for event in objects
    )
    quota_prose = _CLAUDE_QUOTA_PROSE_RE.search(raw) is not None
    if saw_usage_marker or quota_result or (quota_prose and saw_error_result):
        return QuotaClassification(
            QuotaVerdict.QUOTA_EXHAUSTED, _claude_reset(objects, moment), "claude:usage-limit"
        )
    if _CLAUDE_RATE_RE.search(raw) or any(
        event.get("type") == "system" and event.get("subtype") == "api_retry" for event in objects
    ):
        return QuotaClassification(QuotaVerdict.RATE_LIMITED, None, "claude:rate-limit")
    return QuotaClassification(QuotaVerdict.OTHER, None, "claude:other")


# ---------------------------------------------------------------------------
# Generic text classifier for providers without a native event stream.
# ---------------------------------------------------------------------------

_QUOTA_TEXT_RE = re.compile(
    r"you('ve| have) hit your .*limit|usage limit (has been )?(reached|exceeded)"
    r"|quota (exceeded|exhausted)|insufficient_quota|out of quota",
    re.IGNORECASE,
)
_RATE_TEXT_RE = re.compile(
    r"rate limit|too many requests|\b429\b|try again in [\d.]+s", re.IGNORECASE
)


def classify_quota(stderr: str) -> str | None:
    """Classify free text as ``QUOTA_EXHAUSTED``, ``RATE_LIMITED`` or ``None``.

    Auth and billing text are never quota. This is the fallback for surfaces
    without native events; the CLI backends use their structured classifiers.
    """
    if not stderr.strip():
        return None
    if _AUTH_RE.search(stderr) or _BILLING_RE.search(stderr):
        return None
    if _QUOTA_TEXT_RE.search(stderr):
        return QuotaVerdict.QUOTA_EXHAUSTED.value
    if _RATE_TEXT_RE.search(stderr):
        return QuotaVerdict.RATE_LIMITED.value
    return None

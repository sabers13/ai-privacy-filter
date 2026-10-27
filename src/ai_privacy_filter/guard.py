"""Outbound leak guard.

The guard runs after pseudonymisation and before any LLM request. It compares
every original value from the in-memory mapping against the outbound text and
refuses to continue when one is still present. Phone numbers and IBANs are also
compared digit by digit, so ``0170 1234567`` is still caught when the text has
become ``0170/1234567`` or ``01701234567``.

The verdict reports entity types only. Values never appear in a verdict, an
exception message or a log line.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Final

from ai_privacy_filter.models import EntityType, GuardVerdict
from ai_privacy_filter.pseudonymise import normalise_value, placeholder_type

logger = logging.getLogger(__name__)

_DIGIT_ONLY_TYPES: Final = frozenset({EntityType.PHONE, EntityType.IBAN})
_MIN_DIGITS: Final = 4


class GuardBlockedError(RuntimeError):
    """Raised instead of making an LLM call when the outbound text still leaks."""

    def __init__(self, verdict: GuardVerdict) -> None:
        names = ", ".join(member.value for member in verdict.leaked_types) or "unknown type"
        super().__init__(f"outbound text would leak personal data ({names}); LLM call blocked")
        self.verdict = verdict


def guard(redacted_text: str, mapping: Mapping[str, str]) -> GuardVerdict:
    """Check that no original value occurs in the outbound text.

    Checks normalised values plus digit-only forms for phone numbers and IBANs.
    On any leak ``ok`` is ``False`` and the caller must abort before the LLM
    call. The returned verdict names types only; values never appear in the
    verdict or in logs.
    """
    haystack = normalise_value(redacted_text)
    haystack_digits = _digits_only(redacted_text)
    leaked: list[EntityType] = []
    found = False
    for placeholder, original in mapping.items():
        entity_type = placeholder_type(placeholder)
        if not _occurs(original, haystack, haystack_digits, entity_type):
            continue
        found = True
        if entity_type is not None and entity_type not in leaked:
            leaked.append(entity_type)
    if found:
        logger.debug("guard: outbound text still contains %d personal-data type(s)", len(leaked))
    return GuardVerdict(ok=not found, leaked_types=leaked)


def require_clean(redacted_text: str, mapping: Mapping[str, str]) -> GuardVerdict:
    """Return the verdict when clean; raise :class:`GuardBlockedError` on a leak.

    Pipeline code calls this immediately before the backend request, so a leak
    cannot reach the model even if a caller forgets to inspect the verdict.
    """
    verdict = guard(redacted_text, mapping)
    if not verdict.ok:
        raise GuardBlockedError(verdict)
    return verdict


def _occurs(
    original: str,
    haystack: str,
    haystack_digits: str,
    entity_type: EntityType | None,
) -> bool:
    needle = normalise_value(original)
    if needle and needle in haystack:
        return True
    if entity_type in _DIGIT_ONLY_TYPES:
        digits = _digits_only(original)
        return len(digits) >= _MIN_DIGITS and digits in haystack_digits
    return False


def _digits_only(value: str) -> str:
    """Return the ASCII digits of ``value`` in order."""
    return "".join(character for character in value if character in "0123456789")

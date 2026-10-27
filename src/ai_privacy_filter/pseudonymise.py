"""Placeholder mapping and restoration.

The mapping produced here lives in memory for one message only. Nothing in this
module logs, persists or transmits a value: only the number of replacements is
ever a candidate for a log line, and this module does not log at all.

Matching is case- and whitespace-insensitive: ``Max  Müller`` and ``MAX MÜLLER``
resolve to the same placeholder inside one message. Restoration is tolerant in
the other direction: it accepts a placeholder the model re-spaced or re-cased.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Final

from ai_privacy_filter.models import EntityType, RedactionResult, RestoreResult, Span

_PLACEHOLDER_RE: Final = re.compile(r"\[([^\[\]\n]{1,64})\]")
_SEPARATOR_RE: Final = re.compile(r"[\s_]+")
_INDEX_RE: Final = re.compile(r"_0*(\d+)$")
_TYPE_RE: Final = re.compile(r"^([A-Z][A-Z_]*)_\d+$")


def normalise_value(value: str) -> str:
    """Return the comparison form of a value: NFC, casefolded, whitespace collapsed."""
    return " ".join(unicodedata.normalize("NFC", value).split()).casefold()


def canonical_placeholder(token: str) -> str:
    """Return the lookup form of a placeholder, with or without brackets.

    ``"[ person _ 01 ]"`` and ``"[PERSON_1]"`` both canonicalise to ``PERSON_1``,
    so a model that changes the case or the spacing still restores correctly.
    """
    inner = token.strip()
    if inner.startswith("[") and inner.endswith("]"):
        inner = inner[1:-1]
    inner = unicodedata.normalize("NFC", inner).strip().upper()
    inner = _SEPARATOR_RE.sub("_", inner).strip("_")
    return _INDEX_RE.sub(r"_\1", inner)


def placeholder_type(token: str) -> EntityType | None:
    """Return the entity type encoded in a placeholder, or ``None`` if unknown."""
    match = _TYPE_RE.match(canonical_placeholder(token))
    if match is None:
        return None
    try:
        return EntityType(match.group(1))
    except ValueError:
        return None


def redact(text: str, spans: Sequence[Span]) -> RedactionResult:
    """Replace ``spans`` in ``text`` with ``[TYPE_n]`` placeholders.

    Placeholders are numbered per type in order of first appearance. Equal
    values (case- and whitespace-normalised) share one placeholder within the
    message. Overlapping spans resolve to the earlier, longer one; spans that
    are empty, whitespace-only or outside ``text`` are ignored. The returned
    mapping stays in memory and is never logged.
    """
    selected = _select_spans(text, spans)
    counters: dict[str, int] = {}
    placeholders: dict[str, str] = {}
    mapping: dict[str, str] = {}
    parts: list[str] = []
    cursor = 0
    for span in selected:
        original = text[span.start : span.end]
        key = normalise_value(original)
        placeholder = placeholders.get(key)
        if placeholder is None:
            index = counters.get(span.type.value, 0) + 1
            counters[span.type.value] = index
            placeholder = f"[{span.type.value}_{index}]"
            placeholders[key] = placeholder
            mapping[placeholder] = original
        parts.append(text[cursor : span.start])
        parts.append(placeholder)
        cursor = span.end
    parts.append(text[cursor:])
    return RedactionResult(redacted_text="".join(parts), mapping=mapping, spans=selected)


def restore(text: str, mapping: Mapping[str, str]) -> RestoreResult:
    """Put original values back into ``text``.

    Placeholder case, inner spacing and zero-padded numbers are tolerated.
    Unknown or invented placeholders are left exactly as written and reported
    once each, in order of first appearance.
    """
    by_canonical: dict[str, str] = {}
    for placeholder in mapping:
        by_canonical.setdefault(canonical_placeholder(placeholder), placeholder)

    unknown: list[str] = []
    seen: set[str] = set()

    def _replace(match: re.Match[str]) -> str:
        written = match.group(0)
        known = by_canonical.get(canonical_placeholder(written))
        if known is not None:
            return mapping[known]
        if written not in seen:
            seen.add(written)
            unknown.append(written)
        return written

    return RestoreResult(
        text=_PLACEHOLDER_RE.sub(_replace, text),
        unknown_placeholders=unknown,
    )


def _select_spans(text: str, spans: Sequence[Span]) -> list[Span]:
    """Return the usable, non-overlapping spans in source order."""
    limit = len(text)
    usable = [
        span
        for span in spans
        if span.start < span.end <= limit and text[span.start : span.end].strip()
    ]
    usable.sort(
        key=lambda span: (span.start, -(span.end - span.start), span.type.value, span.source)
    )
    selected: list[Span] = []
    covered_until = 0
    for span in usable:
        if span.start < covered_until:
            continue
        selected.append(span)
        covered_until = span.end
    return selected

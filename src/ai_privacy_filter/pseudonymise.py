"""Placeholder mapping and restoration.

Lands in T03. T01 ships typed signatures only.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from ai_privacy_filter.models import RedactionResult, RestoreResult, Span


def redact(text: str, spans: Sequence[Span]) -> RedactionResult:
    """Replace ``spans`` in ``text`` with ``[TYPE_n]`` placeholders.

    Placeholders are numbered per type in order of first appearance. Equal
    values (case- and whitespace-normalised) share one placeholder within the
    message. The returned mapping stays in memory and is never logged.
    """
    raise NotImplementedError("redact lands in T03")


def restore(text: str, mapping: Mapping[str, str]) -> RestoreResult:
    """Put original values back into ``text``.

    Tolerates case and spacing changes around placeholders. Unknown or invented
    placeholders are left as-is and reported in ``unknown_placeholders``.
    """
    raise NotImplementedError("restore lands in T03")

"""Outbound leak guard.

Lands in T03. T01 ships the typed signature only.
"""

from __future__ import annotations

from collections.abc import Mapping

from ai_privacy_filter.models import GuardVerdict


def guard(redacted_text: str, mapping: Mapping[str, str]) -> GuardVerdict:
    """Check that no original value occurs in the outbound text.

    Checks normalised values plus digit-only forms for phone and IBAN. On any
    leak the caller must abort before the LLM call. The returned verdict names
    types only; values never appear in the verdict or in logs.
    """
    raise NotImplementedError("guard lands in T03")

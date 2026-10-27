"""Codex CLI backend.

Lands in T06 (ported from SaberOps, Apache-2.0). T01 ships the typed stub only.
"""

from __future__ import annotations

from typing import Any

from ai_privacy_filter.models import BackendResult


class CodexCLIBackend:
    """Runs ``codex exec`` in an empty temp directory with tools off."""

    name: str = "codex-cli"

    def is_available(self) -> bool:
        raise NotImplementedError("the Codex CLI backend lands in T06")

    def complete_json(
        self,
        prompt: str,
        schema: dict[str, Any],
        timeout_s: int = 30,
    ) -> BackendResult:
        raise NotImplementedError("the Codex CLI backend lands in T06")

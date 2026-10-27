"""BYOK backend (OpenAI-compatible chat completions).

Lands in T06. T01 ships the typed stub only.

Planned request shape (from the task brief):
* ``POST {base}/chat/completions``;
* headers ``User-Agent: ai-privacy-filter/<version>`` and
  ``x-opencode-session: <uuid per enquiry>``;
* ``max_tokens`` default 2000 for reasoning models, and only
  ``message.content`` is parsed -- never reasoning text.
"""

from __future__ import annotations

from typing import Any

from ai_privacy_filter.models import BackendResult


class BYOKBackend:
    """OpenAI-compatible backend; key comes from ``AIPF_BYOK_API_KEY`` at run time."""

    name: str = "byok"

    def __init__(self, model: str | None = None, base_url: str | None = None) -> None:
        self.model = model
        self.base_url = base_url

    def is_available(self) -> bool:
        raise NotImplementedError("the BYOK backend lands in T06")

    def complete_json(
        self,
        prompt: str,
        schema: dict[str, Any],
        timeout_s: int = 30,
    ) -> BackendResult:
        raise NotImplementedError("the BYOK backend lands in T06")

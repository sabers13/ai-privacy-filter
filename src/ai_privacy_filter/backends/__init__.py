"""LLM backend adapters.

The fake backend is implemented in T01. BYOK, Claude CLI and Codex CLI land in
T06. Use :func:`ai_privacy_filter.backends.registry.get_backend` to construct one.
"""

from __future__ import annotations

from ai_privacy_filter.backends.base import LLMBackend
from ai_privacy_filter.backends.fake import FakeBackend
from ai_privacy_filter.backends.registry import available_backends, get_backend

__all__ = ["FakeBackend", "LLMBackend", "available_backends", "get_backend"]

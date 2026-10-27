"""LLM backend adapters.

``fake`` is deterministic and offline; ``byok`` speaks the OpenAI-compatible
chat-completions protocol (OpenCode Go, or a local Ollama endpoint);
``claude-cli`` and ``codex-cli`` run the native CLIs with tools off inside a
fresh empty temporary directory. Use
:func:`ai_privacy_filter.backends.registry.get_backend` to construct one.
"""

from __future__ import annotations

from ai_privacy_filter.backends.base import LLMBackend
from ai_privacy_filter.backends.byok import BYOKBackend
from ai_privacy_filter.backends.claude_cli import ClaudeCLIBackend
from ai_privacy_filter.backends.codex_cli import CodexCLIBackend
from ai_privacy_filter.backends.fake import FakeBackend
from ai_privacy_filter.backends.registry import available_backends, get_backend

__all__ = [
    "BYOKBackend",
    "ClaudeCLIBackend",
    "CodexCLIBackend",
    "FakeBackend",
    "LLMBackend",
    "available_backends",
    "get_backend",
]

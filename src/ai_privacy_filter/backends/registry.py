"""Backend registry.

T01 registers the implemented fake backend plus typed placeholders for the
backends that land in T06. ``available_backends`` lists registrations without
probing them; probing is a separate, explicit call per backend.
"""

from __future__ import annotations

from collections.abc import Callable

from ai_privacy_filter.backends.base import LLMBackend
from ai_privacy_filter.backends.byok import BYOKBackend
from ai_privacy_filter.backends.claude_cli import ClaudeCLIBackend
from ai_privacy_filter.backends.codex_cli import CodexCLIBackend
from ai_privacy_filter.backends.fake import FakeBackend

_REGISTRY: dict[str, Callable[[], LLMBackend]] = {
    "fake": FakeBackend,
    "byok": BYOKBackend,
    "claude-cli": ClaudeCLIBackend,
    "codex-cli": CodexCLIBackend,
}


def get_backend(name: str) -> LLMBackend:
    """Return a new backend instance for ``name``."""
    try:
        factory = _REGISTRY[name]
    except KeyError as exc:
        raise ValueError(f"unknown backend: {name}") from exc
    return factory()


def available_backends() -> tuple[str, ...]:
    """Return registered backend names in stable order."""
    return tuple(_REGISTRY)

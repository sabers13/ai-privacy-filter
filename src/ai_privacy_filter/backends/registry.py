"""Backend registry.

``get_backend`` constructs one backend by name; ``available_backends`` lists the
registered names in stable order. Listing does not probe a backend: probing is an
explicit ``is_available()`` call per backend, so importing the registry never
touches the network, the CLI binaries or a credential.
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
    """Return a new backend instance for ``name``.

    Raises:
        ValueError: if ``name`` is not registered.
    """
    try:
        factory = _REGISTRY[name]
    except KeyError as exc:
        raise ValueError(f"unknown backend: {name}") from exc
    return factory()


def available_backends() -> tuple[str, ...]:
    """Return registered backend names in stable order.

    A registered backend is not necessarily usable: call ``is_available()`` on
    the instance to learn whether its binary or configuration is present.
    """
    return tuple(_REGISTRY)

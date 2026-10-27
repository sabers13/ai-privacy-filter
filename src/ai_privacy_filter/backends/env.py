"""Hermetic child environment for native CLI backends.

Adapted from SaberOps, Apache-2.0 (``src/saberops/workers/env.py``).

A CLI child gets an explicit allowlist of runtime variables plus the
harness-specific configuration directory and nothing else. Ambient provider
credential variables and project/user configuration selectors are dropped, so
an exported ``ANTHROPIC_API_KEY``, ``OPENAI_API_KEY`` or a shell-level base URL
cannot change which account or billing path a call uses.

The projection never mutates ``os.environ``.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

__all__ = [
    "BASE_ENV_ALLOWLIST",
    "BASE_ENV_ALLOWLIST_PREFIXES",
    "HARNESS_ENV_ADDITIONS",
    "hermetic_env",
]

#: Exact non-secret runtime variable names a CLI child may need. ``LC_*`` is
#: additionally matched by prefix (see :data:`BASE_ENV_ALLOWLIST_PREFIXES`).
BASE_ENV_ALLOWLIST: tuple[str, ...] = (
    "PATH",
    "HOME",
    "USER",
    "LOGNAME",
    "SHELL",
    "LANG",
    "TZ",
    "TERM",
    "COLORTERM",
    "TMPDIR",
    "XDG_RUNTIME_DIR",
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
    "XDG_CACHE_HOME",
    "XDG_STATE_HOME",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "no_proxy",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "NODE_EXTRA_CA_CERTS",
)

#: Additional allowlist prefixes matched against the parent environment.
BASE_ENV_ALLOWLIST_PREFIXES: tuple[str, ...] = ("LC_",)

#: Per-harness configuration directory (never a credential value).
HARNESS_ENV_ADDITIONS: Mapping[str, tuple[str, ...]] = {
    "claude": ("CLAUDE_CONFIG_DIR", "CLAUDE_CODE_DISABLE_FAST_MODE"),
    "codex": ("CODEX_HOME",),
}

#: Credential prefixes that are denied for a harness even when supplied through
#: an explicit overlay. Deny wins over allow.
_OVERLAY_DENYLIST_PREFIXES: Mapping[str, tuple[str, ...]] = {
    "claude": ("ANTHROPIC_", "CLAUDE_CODE_USE_"),
    "codex": ("OPENAI_", "CODEX_ACCESS_"),
}

#: Exact credential names denied for a harness.
_OVERLAY_DENYLIST_NAMES: Mapping[str, frozenset[str]] = {
    "claude": frozenset({"ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"}),
    "codex": frozenset({"CODEX_API_KEY", "OPENAI_API_KEY"}),
}


def _allowed(harness: str, key: str) -> bool:
    if key in BASE_ENV_ALLOWLIST:
        return True
    if key.startswith(BASE_ENV_ALLOWLIST_PREFIXES):
        return True
    return key in HARNESS_ENV_ADDITIONS.get(harness, ())


def _denied(harness: str, key: str) -> bool:
    if any(key.startswith(prefix) for prefix in _OVERLAY_DENYLIST_PREFIXES.get(harness, ())):
        return True
    return key in _OVERLAY_DENYLIST_NAMES.get(harness, frozenset())


def hermetic_env(
    harness: str,
    overlay: Mapping[str, str] | None = None,
    parent_env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return the hermetic child environment for one CLI dispatch.

    ``harness`` is ``"claude"`` or ``"codex"``. Only allowlisted runtime names,
    ``LC_*`` variables, the harness configuration directory and allowlisted
    overlay keys survive. Credential-shaped overlay keys are dropped even when
    otherwise allowlisted; an unknown harness simply gets the base allowlist.

    Raises:
        ValueError: if ``harness`` is not a known harness name.
    """
    if harness not in HARNESS_ENV_ADDITIONS:
        raise ValueError(f"unknown harness: {harness}")
    source = os.environ if parent_env is None else parent_env
    child: dict[str, str] = {}
    for key in BASE_ENV_ALLOWLIST:
        value = source.get(key)
        if value is not None:
            child[key] = value
    for key, value in source.items():
        if key.startswith(BASE_ENV_ALLOWLIST_PREFIXES):
            child[key] = value
    for key in HARNESS_ENV_ADDITIONS[harness]:
        value = source.get(key)
        if value is not None:
            child[key] = value
    if overlay:
        for key, value in overlay.items():
            if not _allowed(harness, key):
                continue
            if _denied(harness, key):
                continue
            child[key] = value
    return child

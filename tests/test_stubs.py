"""Every remaining T01 stub raises NotImplementedError with a useful message.

The real backends and quota classification land in T06 and are covered by the
backend test modules; the pipeline, triage prompt and service still stubs here.
"""

from __future__ import annotations

import pytest

from ai_privacy_filter import api, cli, detect, triage
from ai_privacy_filter.backends import registry
from ai_privacy_filter.backends.byok import BYOKBackend
from ai_privacy_filter.backends.claude_cli import ClaudeCLIBackend
from ai_privacy_filter.backends.codex_cli import CodexCLIBackend
from ai_privacy_filter.backends.fake import FakeBackend


def test_detect_is_a_stub() -> None:
    with pytest.raises(NotImplementedError):
        detect.detect("Synthetic text")


def test_triage_functions_are_stubs() -> None:
    with pytest.raises(NotImplementedError):
        triage.build_prompt("Synthetic text")
    with pytest.raises(NotImplementedError):
        triage.parse_triage({}, backend="fake", model=None, latency_ms=0)
    with pytest.raises(NotImplementedError):
        triage.classify("Synthetic text", FakeBackend())


def test_api_is_a_stub() -> None:
    with pytest.raises(NotImplementedError):
        api.create_app()


def test_cli_is_a_stub() -> None:
    with pytest.raises(NotImplementedError):
        cli.main(["redact", "Synthetic text"])


def test_registry_lists_backends_and_builds_each_type() -> None:
    assert registry.available_backends() == ("fake", "byok", "claude-cli", "codex-cli")
    assert isinstance(registry.get_backend("fake"), FakeBackend)
    assert isinstance(registry.get_backend("byok"), BYOKBackend)
    assert isinstance(registry.get_backend("claude-cli"), ClaudeCLIBackend)
    assert isinstance(registry.get_backend("codex-cli"), CodexCLIBackend)


def test_registry_rejects_unknown_backend() -> None:
    with pytest.raises(ValueError, match="unknown backend"):
        registry.get_backend("nope")


@pytest.mark.parametrize("name", ["byok", "claude-cli", "codex-cli"])
def test_registry_listing_does_not_probe(name: str) -> None:
    backend = registry.get_backend(name)
    assert isinstance(backend.name, str)
    assert callable(backend.is_available)

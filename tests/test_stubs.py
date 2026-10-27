"""Every T01 stub raises NotImplementedError with a useful message."""

from __future__ import annotations

import pytest

from ai_privacy_filter import api, cli, detect, triage
from ai_privacy_filter.backends import registry
from ai_privacy_filter.backends.byok import BYOKBackend
from ai_privacy_filter.backends.claude_cli import ClaudeCLIBackend
from ai_privacy_filter.backends.codex_cli import CodexCLIBackend
from ai_privacy_filter.backends.fake import FakeBackend
from ai_privacy_filter.backends.quota import classify_quota

BACKEND_STUBS = [BYOKBackend(), ClaudeCLIBackend(), CodexCLIBackend()]


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


@pytest.mark.parametrize("backend", BACKEND_STUBS, ids=lambda backend: backend.name)
def test_backend_stubs(backend: object) -> None:
    with pytest.raises(NotImplementedError):
        backend.is_available()  # type: ignore[attr-defined]
    with pytest.raises(NotImplementedError):
        backend.complete_json("Synthetic prompt", {}, 5)  # type: ignore[attr-defined]


def test_quota_is_a_stub() -> None:
    with pytest.raises(NotImplementedError):
        classify_quota("")


def test_registry_lists_backends_and_builds_fake() -> None:
    assert registry.available_backends() == ("fake", "byok", "claude-cli", "codex-cli")
    backend = registry.get_backend("fake")
    assert isinstance(backend, FakeBackend)


def test_registry_rejects_unknown_backend() -> None:
    with pytest.raises(ValueError, match="unknown backend"):
        registry.get_backend("nope")

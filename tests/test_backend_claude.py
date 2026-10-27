"""Claude CLI backend: exact argv, isolation and native result parsing."""

from __future__ import annotations

import json
from typing import Any

import pytest
from backends_support import RecordingRunner, fixture_json, fixture_text, outcome

from ai_privacy_filter.backends.base import (
    ERROR_AUTH,
    ERROR_CLI,
    ERROR_EMPTY,
    ERROR_INVALID_JSON,
    ERROR_NOT_INSTALLED,
    ERROR_QUOTA_EXHAUSTED,
    ERROR_SCHEMA,
    ERROR_TIMEOUT,
    ERROR_TOOL_USE,
)
from ai_privacy_filter.backends.claude_cli import (
    CLAUDE_FLAGS,
    CLAUDE_SETTINGS,
    ClaudeCLIBackend,
)
from ai_privacy_filter.triage import TRIAGE_SCHEMA

PROMPT = "Synthetic Anfrage von [PERSON_1] zu einer Bestellung. Antworte als JSON."


def _backend(monkeypatch: pytest.MonkeyPatch, runner: RecordingRunner) -> ClaudeCLIBackend:
    monkeypatch.setattr(
        "ai_privacy_filter.backends.claude_cli.shutil.which",
        lambda _name: "/synthetic/bin/claude",
    )
    return ClaudeCLIBackend(runner=runner)


def test_argv_is_exact(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = RecordingRunner(outcome(fixture_text("claude_success.json")))
    backend = _backend(monkeypatch, runner)
    backend.complete_json(PROMPT, TRIAGE_SCHEMA, 30)
    assert runner.call.argv == ["claude", *CLAUDE_FLAGS]
    assert runner.call.argv[1:] == [
        "-p",
        "--output-format",
        "json",
        "--tools",
        "",
        "--strict-mcp-config",
        "--no-session-persistence",
        "--setting-sources",
        "",
        "--settings",
        CLAUDE_SETTINGS,
    ]


def test_argv_appends_model_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = RecordingRunner(outcome(fixture_text("claude_success.json")))
    monkeypatch.setattr(
        "ai_privacy_filter.backends.claude_cli.shutil.which",
        lambda _name: "/synthetic/bin/claude",
    )
    backend = ClaudeCLIBackend(model="synthetic-model", runner=runner)
    backend.complete_json(PROMPT, TRIAGE_SCHEMA, 30)
    assert runner.call.argv[-2:] == ["--model", "synthetic-model"]


def test_call_is_isolated_and_stdin_fed(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = RecordingRunner(outcome(fixture_text("claude_success.json")))
    backend = _backend(monkeypatch, runner)
    backend.complete_json(PROMPT, TRIAGE_SCHEMA, 18)
    call = runner.call
    assert call.prompt == PROMPT
    assert call.timeout_s == 18
    assert call.entries_before_run == []
    assert call.env["CLAUDE_CODE_DISABLE_FAST_MODE"] == "1"
    assert not any(name.startswith(("ANTHROPIC_", "OPENAI_")) for name in call.env)
    runner.check_cleanup()


def test_success_parses_native_result(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = RecordingRunner(outcome(fixture_text("claude_success.json")))
    backend = _backend(monkeypatch, runner)
    result = backend.complete_json(PROMPT, TRIAGE_SCHEMA, 30)
    assert result.error_kind is None
    assert result.parsed == {
        "category": "QUOTE_REQUEST",
        "urgency": "NORMAL",
        "reply_draft": (
            "Guten Tag [PERSON_1], vielen Dank fuer Ihre Anfrage. Wir senden Ihnen ein Angebot zu."
        ),
    }
    assert result.model == "claude-synthetic-model"
    assert result.usage == {
        "input_tokens": 1200,
        "output_tokens": 48,
        "total_tokens": 1248,
        "cached_tokens": 64,
        "reasoning_tokens": None,
    }
    assert result.latency_ms >= 0


def test_success_is_read_from_native_evidence_not_exit_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = RecordingRunner(outcome(fixture_text("claude_success.json"), exit_code=1))
    backend = _backend(monkeypatch, runner)
    result = backend.complete_json(PROMPT, TRIAGE_SCHEMA, 30)
    assert result.error_kind is None


def test_a_leading_warning_line_is_tolerated(monkeypatch: pytest.MonkeyPatch) -> None:
    stdout = "warning: synthetic notice\n" + fixture_text("claude_success.json")
    runner = RecordingRunner(outcome(stdout))
    backend = _backend(monkeypatch, runner)
    assert backend.complete_json(PROMPT, TRIAGE_SCHEMA, 30).error_kind is None


def test_quota_is_classified(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = RecordingRunner(outcome(fixture_text("claude_quota.json")))
    backend = _backend(monkeypatch, runner)
    result = backend.complete_json(PROMPT, TRIAGE_SCHEMA, 30)
    assert result.error_kind == ERROR_QUOTA_EXHAUSTED
    assert result.parsed is None


def test_auth_error_is_not_quota(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = RecordingRunner(outcome(fixture_text("claude_auth.json")))
    backend = _backend(monkeypatch, runner)
    assert backend.complete_json(PROMPT, TRIAGE_SCHEMA, 30).error_kind == ERROR_AUTH


def test_a_permission_denial_is_a_tool_use_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = RecordingRunner(outcome(fixture_text("claude_tool_denial.json")))
    backend = _backend(monkeypatch, runner)
    assert backend.complete_json(PROMPT, TRIAGE_SCHEMA, 30).error_kind == ERROR_TOOL_USE


def test_prose_result_is_invalid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = RecordingRunner(outcome(fixture_text("claude_not_json.json")))
    backend = _backend(monkeypatch, runner)
    result = backend.complete_json(PROMPT, TRIAGE_SCHEMA, 30)
    assert result.error_kind == ERROR_INVALID_JSON
    assert "Kategorie" in result.text


def test_schema_violation_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = fixture_json("claude_success.json")
    answer: dict[str, Any] = {"category": "QUOTE_REQUEST", "urgency": "NORMAL"}
    payload["result"] = json.dumps(answer)
    runner = RecordingRunner(outcome(json.dumps(payload)))
    backend = _backend(monkeypatch, runner)
    assert backend.complete_json(PROMPT, TRIAGE_SCHEMA, 30).error_kind == ERROR_SCHEMA


def test_empty_result_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = fixture_json("claude_success.json")
    payload["result"] = ""
    runner = RecordingRunner(outcome(json.dumps(payload)))
    backend = _backend(monkeypatch, runner)
    assert backend.complete_json(PROMPT, TRIAGE_SCHEMA, 30).error_kind == ERROR_EMPTY


def test_timeout_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = RecordingRunner(outcome(timed_out=True))
    backend = _backend(monkeypatch, runner)
    assert backend.complete_json(PROMPT, TRIAGE_SCHEMA, 5).error_kind == ERROR_TIMEOUT


def test_missing_binary_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = RecordingRunner(outcome(""))
    backend = ClaudeCLIBackend(binary="aipf-missing-claude", runner=runner)
    monkeypatch.setattr("ai_privacy_filter.backends.claude_cli.shutil.which", lambda _name: None)
    assert backend.is_available() is False
    assert backend.complete_json(PROMPT, TRIAGE_SCHEMA, 5).error_kind == ERROR_NOT_INSTALLED
    assert runner.calls == []


def test_os_error_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = RecordingRunner(outcome(""), exception=FileNotFoundError("synthetic"))
    backend = _backend(monkeypatch, runner)
    assert backend.complete_json(PROMPT, TRIAGE_SCHEMA, 5).error_kind == ERROR_CLI


def test_unstructured_failure_is_a_cli_error(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = RecordingRunner(outcome("", "synthetic crash", exit_code=2))
    backend = _backend(monkeypatch, runner)
    assert backend.complete_json(PROMPT, TRIAGE_SCHEMA, 5).error_kind == ERROR_CLI

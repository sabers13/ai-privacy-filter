"""Codex CLI backend: exact argv, billing pins and JSONL event parsing."""

from __future__ import annotations

import json

import pytest
from backends_support import RecordingRunner, fixture_text, outcome

from ai_privacy_filter.backends.base import (
    ERROR_AUTH,
    ERROR_CLI,
    ERROR_INVALID_JSON,
    ERROR_NOT_INSTALLED,
    ERROR_QUOTA_EXHAUSTED,
    ERROR_RATE_LIMITED,
    ERROR_SCHEMA,
    ERROR_TIMEOUT,
    ERROR_TOOL_USE,
)
from ai_privacy_filter.backends.codex_cli import (
    CODEX_BILLING_PINS,
    CODEX_FLAGS,
    CodexCLIBackend,
)
from ai_privacy_filter.triage import TRIAGE_SCHEMA

PROMPT = "Synthetic Anfrage von [PERSON_1]. Antworte als JSON, ohne Werkzeuge zu benutzen."


def _backend(monkeypatch: pytest.MonkeyPatch, runner: RecordingRunner) -> CodexCLIBackend:
    monkeypatch.setattr(
        "ai_privacy_filter.backends.codex_cli.shutil.which",
        lambda _name: "/synthetic/bin/codex",
    )
    return CodexCLIBackend(runner=runner)


def test_argv_is_exact(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = RecordingRunner(outcome(fixture_text("codex_success.jsonl")))
    backend = _backend(monkeypatch, runner)
    backend.complete_json(PROMPT, TRIAGE_SCHEMA, 30)
    argv = runner.call.argv
    workdir = runner.call.cwd
    assert argv[:2] == ["codex", "exec"]
    assert argv[2:8] == list(CODEX_FLAGS[1:])
    assert argv[8:10] == ["-C", str(workdir)]
    assert argv[10:12] == ["--output-schema", str(workdir / "schema.json")]
    assert argv[12:18] == list(CODEX_BILLING_PINS)
    assert argv[18:] == ["-"]


def test_billing_pins_are_the_expected_values(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = RecordingRunner(outcome(fixture_text("codex_success.jsonl")))
    backend = _backend(monkeypatch, runner)
    backend.complete_json(PROMPT, TRIAGE_SCHEMA, 30)
    assert CODEX_BILLING_PINS == (
        "-c",
        'forced_login_method="chatgpt"',
        "-c",
        'model_provider="openai"',
        "-c",
        'service_tier="default"',
    )
    assert "--ignore-user-config" in runner.call.argv
    assert "--ephemeral" in runner.call.argv
    assert "--skip-git-repo-check" in runner.call.argv
    assert runner.call.argv[runner.call.argv.index("--sandbox") + 1] == "read-only"


def test_argv_appends_model(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = RecordingRunner(outcome(fixture_text("codex_success.jsonl")))
    monkeypatch.setattr(
        "ai_privacy_filter.backends.codex_cli.shutil.which",
        lambda _name: "/synthetic/bin/codex",
    )
    backend = CodexCLIBackend(model="synthetic-model", runner=runner)
    backend.complete_json(PROMPT, TRIAGE_SCHEMA, 30)
    assert runner.call.argv[-3:] == ["--model", "synthetic-model", "-"]


def test_schema_is_written_into_the_temp_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = RecordingRunner(
        outcome(fixture_text("codex_success.jsonl")), read_files=("schema.json",)
    )
    backend = _backend(monkeypatch, runner)
    backend.complete_json(PROMPT, TRIAGE_SCHEMA, 30)
    assert json.loads(runner.call.files["schema.json"]) == TRIAGE_SCHEMA


def test_call_is_isolated_and_stdin_fed(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = RecordingRunner(outcome(fixture_text("codex_success.jsonl")))
    backend = _backend(monkeypatch, runner)
    backend.complete_json(PROMPT, TRIAGE_SCHEMA, 20)
    call = runner.call
    assert call.prompt == PROMPT
    assert call.timeout_s == 20
    assert call.entries_before_run == ["schema.json"]
    assert "OPENAI_API_KEY" not in call.env
    runner.check_cleanup()


def test_success_parses_native_events(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = RecordingRunner(outcome(fixture_text("codex_success.jsonl")))
    backend = _backend(monkeypatch, runner)
    result = backend.complete_json(PROMPT, TRIAGE_SCHEMA, 30)
    assert result.error_kind is None
    assert result.parsed is not None
    assert result.parsed["category"] == "SUPPORT"
    assert result.usage == {
        "input_tokens": 900,
        "output_tokens": 40,
        "total_tokens": 940,
        "cached_tokens": 128,
        "reasoning_tokens": 12,
    }


def test_tool_event_is_a_hard_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = RecordingRunner(outcome(fixture_text("codex_tool_event.jsonl")))
    backend = _backend(monkeypatch, runner)
    result = backend.complete_json(PROMPT, TRIAGE_SCHEMA, 30)
    assert result.error_kind == ERROR_TOOL_USE
    assert result.parsed is None
    assert "CANARY" not in result.text


def test_an_unknown_item_type_is_a_hard_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    events = (
        '{"type": "item.completed", "item": {"id": "i", "type": "file_change", '
        '"path": "synthetic.txt"}}\n'
        '{"type": "turn.completed", "usage": {"input_tokens": 1, "output_tokens": 1}}\n'
    )
    runner = RecordingRunner(outcome(events))
    backend = _backend(monkeypatch, runner)
    assert backend.complete_json(PROMPT, TRIAGE_SCHEMA, 30).error_kind == ERROR_TOOL_USE


def test_quota_is_classified(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = RecordingRunner(outcome(fixture_text("codex_quota.jsonl")))
    backend = _backend(monkeypatch, runner)
    assert backend.complete_json(PROMPT, TRIAGE_SCHEMA, 30).error_kind == ERROR_QUOTA_EXHAUSTED


def test_rate_limit_is_classified(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = RecordingRunner(outcome(fixture_text("codex_rate_limit.jsonl")))
    backend = _backend(monkeypatch, runner)
    assert backend.complete_json(PROMPT, TRIAGE_SCHEMA, 30).error_kind == ERROR_RATE_LIMITED


def test_auth_is_not_quota(monkeypatch: pytest.MonkeyPatch) -> None:
    events = '{"type": "error", "message": "Not authenticated. Run codex login."}\n'
    runner = RecordingRunner(outcome(events))
    backend = _backend(monkeypatch, runner)
    assert backend.complete_json(PROMPT, TRIAGE_SCHEMA, 30).error_kind == ERROR_AUTH


def test_a_stream_without_turn_completed_is_a_cli_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events = (
        '{"type": "thread.started", "thread_id": "synthetic"}\n'
        '{"type": "item.completed", "item": {"id": "i", "type": "agent_message", '
        '"text": "{\\"category\\": \\"OTHER\\"}"}}\n'
    )
    runner = RecordingRunner(outcome(events))
    backend = _backend(monkeypatch, runner)
    assert backend.complete_json(PROMPT, TRIAGE_SCHEMA, 30).error_kind == ERROR_CLI


def test_schema_violation_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    events = (
        '{"type": "item.completed", "item": {"id": "i", "type": "agent_message", '
        '"text": "{\\"category\\": \\"OTHER\\", \\"urgency\\": \\"LOW\\"}"}}\n'
        '{"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 5}}\n'
    )
    runner = RecordingRunner(outcome(events))
    backend = _backend(monkeypatch, runner)
    assert backend.complete_json(PROMPT, TRIAGE_SCHEMA, 30).error_kind == ERROR_SCHEMA


def test_non_json_message_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    events = (
        '{"type": "item.completed", "item": {"id": "i", "type": "agent_message", '
        '"text": "Ich habe keine Werkzeuge benutzt."}}\n'
        '{"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 5}}\n'
    )
    runner = RecordingRunner(outcome(events))
    backend = _backend(monkeypatch, runner)
    assert backend.complete_json(PROMPT, TRIAGE_SCHEMA, 30).error_kind == ERROR_INVALID_JSON


def test_timeout_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = RecordingRunner(outcome(timed_out=True))
    backend = _backend(monkeypatch, runner)
    assert backend.complete_json(PROMPT, TRIAGE_SCHEMA, 5).error_kind == ERROR_TIMEOUT


def test_missing_binary_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("ai_privacy_filter.backends.codex_cli.shutil.which", lambda _name: None)
    runner = RecordingRunner(outcome(""))
    backend = CodexCLIBackend(binary="aipf-missing-codex", runner=runner)
    assert backend.is_available() is False
    assert backend.complete_json(PROMPT, TRIAGE_SCHEMA, 5).error_kind == ERROR_NOT_INSTALLED
    assert runner.calls == []


def test_usage_without_numbers_is_normalised_to_nulls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events = (
        '{"type": "item.completed", "item": {"id": "i", "type": "agent_message", '
        '"text": "{\\"category\\": \\"OTHER\\", \\"urgency\\": \\"LOW\\", '
        '\\"reply_draft\\": \\"Danke.\\"}"}}\n'
        '{"type": "turn.completed"}\n'
    )
    runner = RecordingRunner(outcome(events))
    backend = _backend(monkeypatch, runner)
    result = backend.complete_json(PROMPT, TRIAGE_SCHEMA, 30)
    assert result.error_kind is None
    assert result.usage is None

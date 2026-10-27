"""Claude Code CLI backend with subscription billing pinned.

Adapted from SaberOps, Apache-2.0 (``src/saberops/workers/claude.py``).

Every call runs ``claude -p`` inside a fresh empty temporary directory, with the
prompt on stdin, a hermetic environment and every tool disabled. The invocation
pins the Claude.ai subscription as the billing method and drops user, project and
local settings, so an exported API key, a settings file or an MCP server cannot
change where a call goes or how it is billed.

``--bare`` is deliberately not used: the installed CLI documents it as reading
Anthropic auth "strictly ANTHROPIC_API_KEY or apiKeyHelper", which would defeat
the ``forceLoginMethod: "claudeai"`` pin.
"""

from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from ai_privacy_filter.backends import schema as schema_validator
from ai_privacy_filter.backends.base import (
    ERROR_AUTH,
    ERROR_BILLING,
    ERROR_CLI,
    ERROR_EMPTY,
    ERROR_INVALID_JSON,
    ERROR_NOT_INSTALLED,
    ERROR_QUOTA_EXHAUSTED,
    ERROR_RATE_LIMITED,
    ERROR_SCHEMA,
    ERROR_TIMEOUT,
    ERROR_TOOL_USE,
    failure,
)
from ai_privacy_filter.backends.env import hermetic_env
from ai_privacy_filter.backends.isolation import Runner, empty_workdir, run_process
from ai_privacy_filter.backends.quota import (
    QuotaVerdict,
    classify_claude,
    looks_like_auth_error,
    looks_like_billing_error,
)
from ai_privacy_filter.models import BackendResult

__all__ = ["CLAUDE_SETTINGS", "ClaudeCLIBackend"]

#: Invocation-local settings. ``forceLoginMethod`` pins the Claude.ai
#: subscription connection; ``disableAllHooks`` removes hook execution; prompt
#: suggestions are turned off because a batch call reads no prompt box.
CLAUDE_SETTINGS: Final[str] = json.dumps(
    {
        "forceLoginMethod": "claudeai",
        "disableAllHooks": True,
        "promptSuggestionEnabled": False,
    },
    separators=(",", ":"),
    sort_keys=True,
)

#: Fixed flags, in the order they are passed. ``--tools ""`` is the documented
#: way to disable every built-in tool; ``--setting-sources ""`` loads no user,
#: project or local settings file; ``--strict-mcp-config`` without an
#: ``--mcp-config`` value leaves no MCP server configured.
CLAUDE_FLAGS: Final[tuple[str, ...]] = (
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
)


@dataclass(frozen=True)
class _ClaudeOutput:
    """Native fields read from one ``--output-format json`` payload."""

    text: str = ""
    completed: bool = False
    model: str | None = None
    usage: dict[str, Any] | None = None
    permission_denials: int = 0
    detail: str = ""


def _as_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _normalise_usage(raw: object) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    input_tokens = _as_int(raw.get("input_tokens"))
    output_tokens = _as_int(raw.get("output_tokens"))
    total = (
        input_tokens + output_tokens
        if input_tokens is not None and output_tokens is not None
        else None
    )
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total,
        "cached_tokens": _as_int(raw.get("cache_read_input_tokens")),
        "reasoning_tokens": None,
    }


def _first_object(stdout: str) -> dict[str, Any] | None:
    """Return the result object from ``--output-format json`` output.

    The documented format is a single object on stdout. A scan for the first
    decodable object keeps a stray warning line from turning a valid answer into
    a failure.
    """
    text = stdout.strip()
    if not text:
        return None
    decoder = json.JSONDecoder()
    for index, character in enumerate(text):
        if character != "{":
            continue
        try:
            payload, _ = decoder.raw_decode(text[index:])
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(payload, dict):
            return payload
    return None


def _parse_output(stdout: str) -> _ClaudeOutput:
    payload = _first_object(stdout)
    if payload is None:
        return _ClaudeOutput(detail="no JSON object on stdout")
    text = payload.get("result")
    if not isinstance(text, str):
        text = ""
    denials = payload.get("permission_denials")
    denial_count = len(denials) if isinstance(denials, list) else 0
    model: str | None = None
    model_usage = payload.get("modelUsage")
    if isinstance(model_usage, dict):
        for key in model_usage:
            if isinstance(key, str) and key:
                model = key
                break
    completed = (
        payload.get("type") == "result"
        and payload.get("subtype") == "success"
        and payload.get("is_error") is False
        and denial_count == 0
    )
    subtype = payload.get("subtype")
    detail = subtype if isinstance(subtype, str) else ""
    return _ClaudeOutput(
        text=text,
        completed=completed,
        model=model,
        usage=_normalise_usage(payload.get("usage")),
        permission_denials=denial_count,
        detail=detail,
    )


class ClaudeCLIBackend:
    """Run ``claude -p`` in an empty temp directory with every tool disabled."""

    name: str = "claude-cli"

    def __init__(
        self,
        model: str | None = None,
        *,
        binary: str = "claude",
        runner: Runner = run_process,
        workdir_parent: Path | None = None,
    ) -> None:
        self.model = model
        self.binary = binary
        self.runner = runner
        self.workdir_parent = workdir_parent

    def is_available(self) -> bool:
        """Return whether the CLI binary is on ``PATH`` (no network call)."""
        return shutil.which(self.binary) is not None

    def build_argv(self) -> list[str]:
        """Return the exact argv for one call (the working directory is cwd)."""
        argv = [self.binary, *CLAUDE_FLAGS]
        if self.model:
            argv.extend(["--model", self.model])
        return argv

    def complete_json(
        self,
        prompt: str,
        schema: dict[str, Any],
        timeout_s: int = 30,
    ) -> BackendResult:
        """Ask for one JSON object and return it parsed or as an error kind."""
        started = time.monotonic()
        if not self.is_available():
            return failure(ERROR_NOT_INSTALLED, 0)

        env = hermetic_env("claude", {"CLAUDE_CODE_DISABLE_FAST_MODE": "1"})
        try:
            with empty_workdir(self.workdir_parent) as workdir:
                outcome = self.runner(
                    self.build_argv(),
                    prompt=prompt,
                    env=env,
                    timeout_s=timeout_s,
                    cwd=workdir,
                )
        except OSError:
            return failure(ERROR_CLI, self._elapsed_ms(started))
        latency_ms = self._elapsed_ms(started)

        if outcome.timed_out:
            return failure(ERROR_TIMEOUT, latency_ms)

        parsed = _parse_output(outcome.stdout)
        if parsed.permission_denials:
            return failure(
                ERROR_TOOL_USE,
                latency_ms,
                model=parsed.model,
                usage=parsed.usage,
                text=parsed.text,
            )

        if not parsed.completed:
            return self._failure_for(
                output=parsed,
                stdout=outcome.stdout,
                stderr=outcome.stderr,
                latency_ms=latency_ms,
            )

        if not parsed.text.strip():
            return failure(ERROR_EMPTY, latency_ms, model=parsed.model, usage=parsed.usage)

        try:
            payload = json.loads(parsed.text)
        except (json.JSONDecodeError, TypeError):
            return failure(
                ERROR_INVALID_JSON,
                latency_ms,
                model=parsed.model,
                usage=parsed.usage,
                text=parsed.text,
            )
        error = schema_validator.validate(payload, schema)
        if error is not None or not isinstance(payload, dict):
            return failure(
                ERROR_SCHEMA,
                latency_ms,
                model=parsed.model,
                usage=parsed.usage,
                text=parsed.text,
            )
        return BackendResult(
            text=parsed.text,
            parsed=payload,
            model=parsed.model,
            latency_ms=latency_ms,
            usage=parsed.usage,
            error_kind=None,
        )

    def _failure_for(
        self,
        *,
        output: _ClaudeOutput,
        stdout: str,
        stderr: str,
        latency_ms: int,
    ) -> BackendResult:
        combined = f"{stdout}\n{stderr}"
        quota = classify_claude(stdout, stderr)
        if quota.verdict is QuotaVerdict.QUOTA_EXHAUSTED:
            return failure(
                ERROR_QUOTA_EXHAUSTED,
                latency_ms,
                model=output.model,
                usage=output.usage,
                text=output.text,
            )
        if quota.verdict is QuotaVerdict.RATE_LIMITED:
            return failure(
                ERROR_RATE_LIMITED,
                latency_ms,
                model=output.model,
                usage=output.usage,
                text=output.text,
            )
        if looks_like_auth_error(combined):
            return failure(
                ERROR_AUTH,
                latency_ms,
                model=output.model,
                usage=output.usage,
                text=output.text,
            )
        if looks_like_billing_error(combined):
            return failure(
                ERROR_BILLING,
                latency_ms,
                model=output.model,
                usage=output.usage,
                text=output.text,
            )
        return failure(
            ERROR_CLI,
            latency_ms,
            model=output.model,
            usage=output.usage,
            text=output.text,
        )

    @staticmethod
    def _elapsed_ms(started: float) -> int:
        return max(0, int((time.monotonic() - started) * 1000))

"""Codex CLI backend with ChatGPT billing pinned and a read-only sandbox.

Adapted from SaberOps, Apache-2.0 (``src/saberops/workers/codex.py``).

Every call runs ``codex exec`` inside a fresh empty temporary directory, with the
prompt on stdin, a hermetic environment and ``--sandbox read-only``. Billing and
provider settings are pinned per invocation with ``-c`` overrides, and
``--ignore-user-config`` keeps ``$CODEX_HOME/config.toml`` (and with it project
trust) out of the turn. ``CODEX_HOME`` still supplies the ChatGPT login.

Honest limitation: a read-only sandbox can still read files. The prompt says not
to use tools, and any tool or command event in the JSON stream is a hard failure
with ``error_kind="tool_use"`` -- but only events the CLI actually emits can be
observed, so this is detection, not prevention.
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
    classify_codex,
    looks_like_auth_error,
    looks_like_billing_error,
)
from ai_privacy_filter.models import BackendResult

__all__ = ["CODEX_BILLING_PINS", "CodexCLIBackend"]

#: Billing-relevant ``-c`` pins passed on every invocation. Each key is read by
#: the installed CLI (0.164.0): ``forced_login_method`` forces the ChatGPT
#: login, ``model_provider`` pins the built-in OpenAI provider, and
#: ``service_tier`` pins the standard tier so a config file cannot opt into a
#: separately billed fast mode.
CODEX_BILLING_PINS: Final[tuple[str, ...]] = (
    "-c",
    'forced_login_method="chatgpt"',
    "-c",
    'model_provider="openai"',
    "-c",
    'service_tier="default"',
)

#: Fixed flags, in the order they are passed.
CODEX_FLAGS: Final[tuple[str, ...]] = (
    "exec",
    "--json",
    "--ephemeral",
    "--skip-git-repo-check",
    "--ignore-user-config",
    "--sandbox",
    "read-only",
)

#: Item types that carry no tool execution. Anything else is a hard failure.
_SAFE_ITEM_TYPES: Final[frozenset[str]] = frozenset(
    {"agent_message", "reasoning", "todo_list", "error"}
)

_SCHEMA_FILENAME: Final = "schema.json"


@dataclass(frozen=True)
class _CodexOutput:
    """Fields read from one ``codex exec --json`` event stream."""

    texts: tuple[str, ...] = ()
    completed: bool = False
    tool_event: bool = False
    model: str | None = None
    usage: dict[str, Any] | None = None
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
        "cached_tokens": _as_int(raw.get("cached_input_tokens")),
        "reasoning_tokens": _as_int(raw.get("reasoning_output_tokens")),
    }


def _parse_events(stdout: str) -> _CodexOutput:
    texts: list[str] = []
    completed = False
    tool_event = False
    model: str | None = None
    usage: dict[str, Any] | None = None
    detail = ""
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(event, dict):
            continue
        event_type = event.get("type")
        raw_model = event.get("model")
        if isinstance(raw_model, str) and raw_model:
            model = raw_model
        if event_type == "turn.completed":
            completed = True
            usage = _normalise_usage(event.get("usage")) or usage
            continue
        if event_type == "turn.failed":
            error = event.get("error")
            if isinstance(error, dict) and isinstance(error.get("message"), str):
                detail = error["message"]
            continue
        if event_type == "error":
            message = event.get("message")
            if isinstance(message, str):
                detail = message
            continue
        if event_type not in {"item.started", "item.updated", "item.completed"}:
            continue
        item = event.get("item")
        if not isinstance(item, dict):
            tool_event = True
            continue
        item_type = item.get("type")
        if not isinstance(item_type, str) or item_type not in _SAFE_ITEM_TYPES:
            tool_event = True
            continue
        if event_type == "item.completed" and item_type == "agent_message":
            text = item.get("text")
            if isinstance(text, str) and text:
                texts.append(text)
    return _CodexOutput(
        texts=tuple(texts),
        completed=completed and not tool_event,
        tool_event=tool_event,
        model=model,
        usage=usage,
        detail=detail,
    )


def _json_candidates(output: _CodexOutput) -> list[str]:
    """Final message first, then the joined stream, for JSON extraction."""
    candidates = list(reversed(output.texts))
    if output.texts:
        candidates.append("\n".join(output.texts))
    return candidates


class CodexCLIBackend:
    """Run ``codex exec`` in an empty temp directory with no tools in the prompt."""

    name: str = "codex-cli"

    def __init__(
        self,
        model: str | None = None,
        *,
        binary: str = "codex",
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

    def build_argv(self, workdir: Path, *, with_schema: bool) -> list[str]:
        """Return the exact argv for one call in ``workdir``."""
        argv = [self.binary, *CODEX_FLAGS, "-C", str(workdir)]
        if with_schema:
            argv.extend(["--output-schema", str(workdir / _SCHEMA_FILENAME)])
        argv.extend(CODEX_BILLING_PINS)
        if self.model:
            argv.extend(["--model", self.model])
        argv.append("-")
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

        env = hermetic_env("codex", None)
        with_schema = bool(schema)
        try:
            with empty_workdir(self.workdir_parent) as workdir:
                if with_schema:
                    (workdir / _SCHEMA_FILENAME).write_text(
                        json.dumps(schema, sort_keys=True), encoding="utf-8"
                    )
                outcome = self.runner(
                    self.build_argv(workdir, with_schema=with_schema),
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

        output = _parse_events(outcome.stdout)
        if output.tool_event:
            return failure(
                ERROR_TOOL_USE,
                latency_ms,
                model=output.model or self.model,
                usage=output.usage,
                text=output.detail,
            )

        if not output.completed:
            return self._failure_for(
                output=output,
                stdout=outcome.stdout,
                stderr=outcome.stderr,
                latency_ms=latency_ms,
            )

        payload, text = self._first_valid(output)
        if payload is None:
            kind = ERROR_EMPTY if not output.texts else ERROR_INVALID_JSON
            return failure(
                kind,
                latency_ms,
                model=output.model or self.model,
                usage=output.usage,
                text=text,
            )
        error = schema_validator.validate(payload, schema)
        if error is not None:
            return failure(
                ERROR_SCHEMA,
                latency_ms,
                model=output.model or self.model,
                usage=output.usage,
                text=text,
            )
        return BackendResult(
            text=text,
            parsed=payload,
            model=output.model or self.model,
            latency_ms=latency_ms,
            usage=output.usage,
            error_kind=None,
        )

    @staticmethod
    def _first_valid(output: _CodexOutput) -> tuple[dict[str, Any] | None, str]:
        for candidate in _json_candidates(output):
            try:
                payload = json.loads(candidate)
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(payload, dict):
                return payload, candidate
        return None, output.texts[-1] if output.texts else ""

    def _failure_for(
        self,
        *,
        output: _CodexOutput,
        stdout: str,
        stderr: str,
        latency_ms: int,
    ) -> BackendResult:
        combined = f"{stdout}\n{stderr}"
        quota = classify_codex(stdout, stderr)
        if quota.verdict is QuotaVerdict.QUOTA_EXHAUSTED:
            kind = ERROR_QUOTA_EXHAUSTED
        elif quota.verdict is QuotaVerdict.RATE_LIMITED:
            kind = ERROR_RATE_LIMITED
        elif looks_like_auth_error(combined):
            kind = ERROR_AUTH
        elif looks_like_billing_error(combined):
            kind = ERROR_BILLING
        else:
            kind = ERROR_CLI
        return failure(
            kind,
            latency_ms,
            model=output.model or self.model,
            usage=output.usage,
            text=output.detail,
        )

    @staticmethod
    def _elapsed_ms(started: float) -> int:
        return max(0, int((time.monotonic() - started) * 1000))

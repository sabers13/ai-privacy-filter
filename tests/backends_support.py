"""Shared helpers for backend tests (not collected as tests)."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ai_privacy_filter.backends.isolation import ProcessOutcome

FIXTURES = Path(__file__).parent / "fixtures" / "backends"


def fixture_text(name: str) -> str:
    """Return one recorded fixture as text."""
    return (FIXTURES / name).read_text(encoding="utf-8")


def fixture_json(name: str) -> dict[str, Any]:
    """Return one recorded fixture parsed as a JSON object."""
    payload = json.loads(fixture_text(name))
    assert isinstance(payload, dict)
    return payload


@dataclass
class RecordedCall:
    """One runner invocation captured for assertions."""

    argv: list[str]
    prompt: str
    env: dict[str, str]
    timeout_s: int
    cwd: Path
    entries_before_run: list[str]
    files: dict[str, str] = field(default_factory=dict)
    survived_after_call: bool = True


@dataclass
class RecordingRunner:
    """Runner stub that records its call and returns ``outcome``.

    ``exception`` is raised instead of returning, so a fault path can be
    exercised. ``read_files`` names files whose content is captured before the
    outcome is returned. The recorded cwd is re-checked after the backend returns
    to prove the temporary directory was removed.
    """

    outcome: ProcessOutcome
    calls: list[RecordedCall] = field(default_factory=list)
    exception: Exception | None = None
    read_files: tuple[str, ...] = ()

    def __call__(
        self,
        argv: Sequence[str],
        *,
        prompt: str,
        env: Mapping[str, str],
        timeout_s: int,
        cwd: Path,
    ) -> ProcessOutcome:
        captured: dict[str, str] = {}
        for name in self.read_files:
            candidate = cwd / name
            if candidate.is_file():
                captured[name] = candidate.read_text(encoding="utf-8")
        call = RecordedCall(
            argv=list(argv),
            prompt=prompt,
            env=dict(env),
            timeout_s=timeout_s,
            cwd=cwd,
            entries_before_run=sorted(path.name for path in cwd.iterdir()),
            files=captured,
        )
        self.calls.append(call)
        if self.exception is not None:
            raise self.exception
        return self.outcome

    @property
    def call(self) -> RecordedCall:
        return self.calls[-1]

    def check_cleanup(self) -> None:
        """Assert every recorded working directory is gone."""
        for call in self.calls:
            call.survived_after_call = call.cwd.exists()
            assert not call.survived_after_call, f"temporary directory survived: {call.cwd.name}"


def outcome(
    stdout: str = "",
    stderr: str = "",
    exit_code: int = 0,
    timed_out: bool = False,
) -> ProcessOutcome:
    """Build one runner outcome."""
    return ProcessOutcome(stdout=stdout, stderr=stderr, exit_code=exit_code, timed_out=timed_out)

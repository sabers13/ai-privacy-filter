"""Isolation primitives: hermetic env, empty workdir, stdin-fed process."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

from ai_privacy_filter.backends.env import hermetic_env
from ai_privacy_filter.backends.isolation import empty_workdir, run_process


def test_hermetic_env_keeps_runtime_and_harness_names() -> None:
    parent = {
        "PATH": "/usr/bin",
        "HOME": "/synthetic-home",
        "LANG": "de_DE.UTF-8",
        "LC_ALL": "de_DE.UTF-8",
        "CLAUDE_CONFIG_DIR": "/synthetic-claude",
        "CODEX_HOME": "/synthetic-codex",
        "ANTHROPIC_API_KEY": "synthetic-anthropic",
        "OPENAI_API_KEY": "synthetic-openai",
        "CLAUDE_CODE_DISABLE_FAST_MODE": "0",
    }
    claude = hermetic_env("claude", parent_env=parent)
    assert claude["PATH"] == "/usr/bin"
    assert claude["HOME"] == "/synthetic-home"
    assert claude["LC_ALL"] == "de_DE.UTF-8"
    assert claude["CLAUDE_CONFIG_DIR"] == "/synthetic-claude"
    assert "CODEX_HOME" not in claude
    assert "ANTHROPIC_API_KEY" not in claude
    assert "OPENAI_API_KEY" not in claude

    codex = hermetic_env("codex", parent_env=parent)
    assert codex["CODEX_HOME"] == "/synthetic-codex"
    assert "CLAUDE_CONFIG_DIR" not in codex
    assert "OPENAI_API_KEY" not in codex


def test_hermetic_env_overlay_denies_credentials() -> None:
    env = hermetic_env(
        "claude",
        {"ANTHROPIC_API_KEY": "synthetic", "CLAUDE_CODE_USE_BEDROCK": "1", "PATH": "/x"},
        parent_env={},
    )
    assert env == {"PATH": "/x"}
    env = hermetic_env("codex", {"OPENAI_API_KEY": "synthetic"}, parent_env={})
    assert "OPENAI_API_KEY" not in env


def test_hermetic_env_rejects_unknown_harness() -> None:
    with pytest.raises(ValueError, match="unknown harness"):
        hermetic_env("nope", parent_env={})


def test_empty_workdir_is_new_empty_and_removed(tmp_path: Path) -> None:
    with empty_workdir(tmp_path) as workdir:
        assert workdir.parent == tmp_path
        assert list(workdir.iterdir()) == []
        (workdir / "scratch.txt").write_text("synthetic")
    assert not workdir.exists()


def test_run_process_feeds_stdin_in_the_given_cwd(tmp_path: Path) -> None:
    with empty_workdir(tmp_path) as workdir:
        result = run_process(
            [sys.executable, "-c", "import os, sys; print(os.getcwd()); print(sys.stdin.read())"],
            prompt="synthetic prompt",
            env={},
            timeout_s=10,
            cwd=workdir,
        )
        assert result.timed_out is False
        assert result.exit_code == 0
        lines = result.stdout.splitlines()
        assert Path(lines[0]).resolve() == workdir.resolve()
        assert lines[1] == "synthetic prompt"
    assert not workdir.exists()


def test_run_process_does_not_inherit_environment(tmp_path: Path) -> None:
    with empty_workdir(tmp_path) as workdir:
        result = run_process(
            [
                sys.executable,
                "-c",
                "import os; print(os.environ.get('AIPF_TEST_ONLY_SECRET', 'missing'))",
            ],
            prompt="",
            env={},
            timeout_s=10,
            cwd=workdir,
        )
    assert result.stdout.strip() == "missing"


def test_run_process_timeout_reports_and_kills(tmp_path: Path) -> None:
    with empty_workdir(tmp_path) as workdir:
        started = time.monotonic()
        result = run_process(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            prompt="",
            env={},
            timeout_s=1,
            cwd=workdir,
        )
        elapsed = time.monotonic() - started
    assert result.timed_out is True
    assert elapsed < 15

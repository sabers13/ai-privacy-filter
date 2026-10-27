"""scan_forbidden finds built-in and private patterns without echoing values."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "scan_forbidden.py"

PRIVATE_IP = ".".join(["10", "24", "17", "9"])
PRIVATE_IP_172 = ".".join(["172", "20", "4", "9"])
PRIVATE_IP_192 = ".".join(["192", "168", "1", "20"])
PRIVATE_IP_CGNAT = ".".join(["100", "100", "7", "3"])
HOME_PATH = "/" + "home" + "/" + "synthetic-user"
TAILSCALE_HOST = "box" + "." + "ts" + "." + "net"
FAKE_KEY = "sk-" + "Q" * 24
FORBIDDEN_WORD = "acme" + "corp"


def _run(
    root: Path, *, extra_env: dict[str, str] | None = None, history: bool = False
) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env.pop("AIPF_FORBIDDEN_FILE", None)
    if extra_env:
        env.update(extra_env)
    command = [sys.executable, str(SCRIPT), "--root", str(root), "--quiet"]
    if not history:
        command.append("--no-history")
    return subprocess.run(command, capture_output=True, text=True, env=env, check=False)


def test_clean_tree_passes(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("Loopback 127.0.0.1 is fine.\n", encoding="utf-8")
    completed = _run(tmp_path)
    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize(
    ("content", "label"),
    [
        (f"host {PRIVATE_IP}", "private-ip-10"),
        (f"host {PRIVATE_IP_172}", "private-ip-172.16"),
        (f"host {PRIVATE_IP_192}", "private-ip-192.168"),
        (f"host {PRIVATE_IP_CGNAT}", "private-ip-100.64"),
        (f"path {HOME_PATH}/project", "home-path"),
        (f"host {TAILSCALE_HOST}", "tailscale-host"),
        (f"key {FAKE_KEY}", "api-key-sk"),
        ("gh" + "p_" + "T" * 36, "api-key-github"),
    ],
)
def test_builtin_hits_report_location_not_value(tmp_path: Path, content: str, label: str) -> None:
    target = tmp_path / "sample.txt"
    target.write_text("line one\n" + content + "\n", encoding="utf-8")
    completed = _run(tmp_path)
    assert completed.returncode == 1
    assert "sample.txt:2:" in completed.stdout
    assert f"[{label}]" in completed.stdout
    assert content not in completed.stdout
    assert content not in completed.stderr


def test_private_key_block_is_detected(tmp_path: Path) -> None:
    marker = "-----BEGIN " + "RSA PRIVATE KEY-----"
    (tmp_path / "key.pem").write_text(marker + "\n", encoding="utf-8")
    completed = _run(tmp_path)
    assert completed.returncode == 1
    assert "[private-key-block]" in completed.stdout
    assert marker not in completed.stdout


def test_forbidden_file_patterns_are_used(tmp_path: Path) -> None:
    pattern_file = tmp_path / "patterns.txt"
    pattern_file.write_text("# comment\n\n" + FORBIDDEN_WORD + "\n", encoding="utf-8")
    target_dir = tmp_path / "tree"
    target_dir.mkdir()
    (target_dir / "mail.txt").write_text("From " + FORBIDDEN_WORD + " to you\n", encoding="utf-8")

    completed = _run(target_dir, extra_env={"AIPF_FORBIDDEN_FILE": str(pattern_file)})
    assert completed.returncode == 1
    assert "mail.txt:1:" in completed.stdout
    assert "forbidden#3" in completed.stdout
    assert FORBIDDEN_WORD not in completed.stdout
    assert FORBIDDEN_WORD not in completed.stderr


def test_forbidden_file_line_numbers_survive_comments(tmp_path: Path) -> None:
    pattern_file = tmp_path / "patterns.txt"
    pattern_file.write_text("# one\n# two\n" + FORBIDDEN_WORD + "\n", encoding="utf-8")
    target = tmp_path / "tree"
    target.mkdir()
    (target / "a.txt").write_text(FORBIDDEN_WORD, encoding="utf-8")
    completed = _run(target, extra_env={"AIPF_FORBIDDEN_FILE": str(pattern_file)})
    assert "forbidden#3" in completed.stdout


def test_unreadable_forbidden_file_is_a_configuration_error(tmp_path: Path) -> None:
    (tmp_path / "clean.txt").write_text("nothing here\n", encoding="utf-8")
    completed = _run(tmp_path, extra_env={"AIPF_FORBIDDEN_FILE": str(tmp_path / "missing")})
    assert completed.returncode == 2
    assert "AIPF_FORBIDDEN_FILE" in completed.stderr


def test_vendored_and_external_data_are_skipped(tmp_path: Path) -> None:
    external = tmp_path / "data" / "external"
    external.mkdir(parents=True)
    (external / "corpus.txt").write_text(f"host {PRIVATE_IP}\n", encoding="utf-8")
    venv = tmp_path / ".venv" / "lib"
    venv.mkdir(parents=True)
    (venv / "dep.py").write_text(f"host {PRIVATE_IP}\n", encoding="utf-8")
    completed = _run(tmp_path)
    assert completed.returncode == 0, completed.stdout


def test_history_scan_finds_added_lines(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args: str) -> None:
        subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            check=True,
        )

    git("init", "-q")
    git("config", "user.email", "test@example.invalid")
    git("config", "user.name", "test")
    leak_file = repo / "leak.txt"
    leak_file.write_text(f"host {PRIVATE_IP}\n", encoding="utf-8")
    git("add", "leak.txt")
    git("commit", "-q", "-m", "add leak")

    # Hide the leak from the working tree so only history can find it.
    leak_file.write_text("clean now\n", encoding="utf-8")
    completed = _run(repo, history=True)
    assert completed.returncode == 1
    assert "history " in completed.stdout
    assert "leak.txt:1:" in completed.stdout
    assert PRIVATE_IP not in completed.stdout


def test_repository_itself_is_clean() -> None:
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--quiet"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr

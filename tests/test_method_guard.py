"""The METHOD.md hash guard refuses test access without a rerun trail."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "method_guard.py"


def _git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=check,
    )


def _repository(root: Path, *, method: bool = True) -> Path:
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.name", "Method Guard Test")
    _git(root, "config", "user.email", "guard@example.invalid")
    (root / "README.md").write_text("fixture\n", encoding="utf-8")
    paths = ["README.md"]
    if method:
        (root / "METHOD.md").write_text("# Protocol\n\nversion 1\n", encoding="utf-8")
        paths.append("METHOD.md")
    _git(root, "add", *paths)
    _git(root, "commit", "-m", "initial")
    return root


def _guard(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _test_args(stage: str = "detection", run_id: str = "run-1", *extra: str) -> list[str]:
    return ["--mode", "test", "--stage", stage, "--run-id", run_id, *extra]


def _claim(root: Path, run_id: str = "run-1") -> dict[str, object]:
    path = root / "results" / "runs" / "claims" / f"{run_id}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _method_hash(root: Path) -> str:
    return hashlib.sha256((root / "METHOD.md").read_bytes()).hexdigest()


def test_dev_run_is_allowed_and_writes_no_claim(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    completed = _guard(root, "--mode", "dev", "--stage", "detection")
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["decision"] == "dev-run"
    assert not (root / "results" / "runs" / "claims").exists()


def test_first_test_run_writes_a_claim(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    completed = _guard(root, *_test_args())
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["decision"] == "first-run"
    assert payload["allowed"] is True

    claim = _claim(root)
    assert claim["mode"] == "test"
    assert claim["stage"] == "detection"
    assert claim["method_sha256"] == _method_hash(root)
    assert claim["rerun_reason"] is None
    assert "text" not in claim
    assert "prompt" not in claim


def test_same_stage_rerun_needs_a_reason(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    assert _guard(root, *_test_args()).returncode == 0

    refused = _guard(root, *_test_args("detection", "run-2"))
    assert refused.returncode == 3
    assert "--rerun-reason" in refused.stderr
    assert not (root / "results" / "runs" / "claims" / "run-2.json").exists()

    allowed = _guard(
        root,
        *_test_args("detection", "run-2", "--rerun-reason", "harness scoring fix"),
    )
    assert allowed.returncode == 0, allowed.stderr
    assert json.loads(allowed.stdout)["decision"] == "rerun"
    assert _claim(root, "run-2")["rerun_reason"] == "harness scoring fix"


def test_new_stage_with_the_same_method_needs_no_reason(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    assert _guard(root, *_test_args()).returncode == 0
    completed = _guard(root, *_test_args("triage", "run-2"))
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["decision"] == "new-stage"


def test_method_change_needs_reason_then_approval(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    assert _guard(root, *_test_args()).returncode == 0

    (root / "METHOD.md").write_text("# Protocol\n\nversion 2\n", encoding="utf-8")
    _git(root, "add", "METHOD.md")
    _git(root, "commit", "-m", "method change")

    no_reason = _guard(root, *_test_args("triage", "run-2"))
    assert no_reason.returncode == 3

    no_approval = _guard(
        root,
        *_test_args("triage", "run-2", "--rerun-reason", "captain requested correction"),
    )
    assert no_approval.returncode == 4
    assert "approval" in no_approval.stderr

    allowed = _guard(
        root,
        *_test_args(
            "triage",
            "run-2",
            "--rerun-reason",
            "captain requested correction",
            "--method-approval",
            "captain-note-17",
        ),
    )
    assert allowed.returncode == 0, allowed.stderr
    assert json.loads(allowed.stdout)["decision"] == "method-change-new-stage"
    assert _claim(root, "run-2")["method_approval"] == "captain-note-17"


def test_same_run_id_is_idempotent(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    assert _guard(root, *_test_args()).returncode == 0
    again = _guard(root, *_test_args())
    assert again.returncode == 0, again.stderr
    assert json.loads(again.stdout)["decision"] == "already-claimed"


def test_same_run_id_with_another_stage_conflicts(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    assert _guard(root, *_test_args()).returncode == 0
    conflicted = _guard(root, *_test_args("triage", "run-1"))
    assert conflicted.returncode == 6


def test_malformed_prior_claim_fails_closed(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    assert _guard(root, *_test_args()).returncode == 0
    (root / "results" / "runs" / "claims" / "broken.json").write_text("{", encoding="utf-8")

    refused = _guard(root, *_test_args("triage", "run-2"))
    assert refused.returncode == 5
    assert "unreadable prior record" in refused.stderr


def test_prior_run_record_also_requires_a_reason(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    run_dir = root / "results" / "runs" / "old-run"
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "run_id": "old-run",
                "mode": "test",
                "stage": "detection",
                "status": "complete",
                "method_sha256": _method_hash(root),
                "started_at": "2026-10-27T12:00:00Z",
            }
        ),
        encoding="utf-8",
    )

    refused = _guard(root, *_test_args("detection", "run-1"))
    assert refused.returncode == 3
    assert "old-run" in refused.stderr


def test_dirty_method_is_refused(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    (root / "METHOD.md").write_text("# Protocol\n\nuncommitted\n", encoding="utf-8")
    refused = _guard(root, *_test_args())
    assert refused.returncode == 7


def test_untracked_method_is_refused(tmp_path: Path) -> None:
    root = _repository(tmp_path, method=False)
    (root / "METHOD.md").write_text("# Protocol\n", encoding="utf-8")
    refused = _guard(root, *_test_args())
    assert refused.returncode == 7


def test_print_hash_reports_sha_and_blob(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    completed = _guard(root, "--mode", "test", "--print-hash")
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["method_sha256"] == _method_hash(root)
    assert len(str(payload["method_git_blob"])) == 40

#!/usr/bin/env python3
"""Refuse a test-split evaluation when the method hash workflow is not satisfied.

This script is the mechanical half of ``METHOD.md`` section 5. It does not run
an evaluation and never reads an enquiry. It answers one question: may this
process read the test split now?

A test run must pass the guard *before* it reads a test record. On success the
guard writes a durable access claim under ``results/runs/claims/``. Every later
test access is checked against those claims and against the run records in
``results/runs/*/run.json``:

* first access for a stage: allowed;
* a new stage under the same METHOD.md hash: allowed;
* a repeat of a stage: needs ``--rerun-reason``;
* any access after METHOD.md changed: needs ``--rerun-reason`` and
  ``--method-approval`` (the captain decision required by the protocol);
* a malformed prior record fails closed;
* an uncommitted or untracked tree fails closed, except for untracked result
  files.

Exit codes: 0 allowed, 2 usage/IO, 3 missing rerun reason, 4 missing method
approval, 5 malformed prior record, 6 run-id conflict, 7 dirty tree or
untracked METHOD.md. Output never contains enquiry text or a private value.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
STAGES: tuple[str, ...] = ("detection", "triage", "all")
CLAIMS_DIRNAME = "claims"
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,80}$")
MAX_REASON_CHARS = 500

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_NEED_REASON = 3
EXIT_NEED_APPROVAL = 4
EXIT_BAD_RECORD = 5
EXIT_RUN_ID_CONFLICT = 6
EXIT_DIRTY = 7


class GuardError(RuntimeError):
    """A refusal with a documented exit code and a safe one-line message."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class Access:
    """One prior test access, from a claim file or a run record."""

    run_id: str
    stage: str
    mode: str
    method_sha256: str
    at: datetime
    source: str


def sha256_file(path: Path) -> str:
    """Return the SHA-256 of the raw file bytes."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def git_value(root: Path, *args: str) -> str | None:
    """Return stripped stdout for a successful git call, else ``None``."""

    completed = _git(root, *args)
    if completed is None or completed.returncode != 0:
        return None
    return completed.stdout.strip() or None


def method_relative_path(root: Path, method_path: Path) -> Path:
    """Return the method path relative to the repository root, or refuse."""

    try:
        return method_path.resolve().relative_to(root.resolve())
    except ValueError as error:
        raise GuardError(
            EXIT_USAGE, "method_guard: METHOD.md must be inside the repository root"
        ) from error


def git_blob_hash(root: Path, method_rel: Path) -> str | None:
    completed = _git(root, "hash-object", "--", str(method_rel))
    if completed is None or completed.returncode != 0:
        return None
    return completed.stdout.strip() or None


def require_clean_tree(root: Path, method_rel: Path) -> tuple[str, str]:
    """Return ``(commit, method_blob)`` or refuse with exit 7.

    Untracked files are allowed because result artifacts are written as the run
    proceeds. Tracked modifications, staged changes and an untracked or
    modified ``METHOD.md`` are not.
    """

    top = git_value(root, "rev-parse", "--show-toplevel")
    if top is None:
        raise GuardError(EXIT_DIRTY, "method_guard: not inside a git work tree")
    commit = git_value(root, "rev-parse", "HEAD")
    if commit is None:
        raise GuardError(EXIT_DIRTY, "method_guard: cannot read HEAD")

    status = _git(root, "status", "--porcelain", "--untracked-files=no")
    if status is None:
        raise GuardError(EXIT_DIRTY, "method_guard: cannot read git status")
    if status.stdout.strip():
        raise GuardError(
            EXIT_DIRTY,
            "method_guard: tracked files are modified; commit the evaluation code first",
        )

    tracked = _git(root, "ls-files", "--error-unmatch", "--", str(method_rel))
    if tracked is None or tracked.returncode != 0:
        raise GuardError(
            EXIT_DIRTY,
            "method_guard: METHOD.md is not tracked; commit the protocol before a test run",
        )
    unchanged = _git(root, "diff", "--quiet", "HEAD", "--", str(method_rel))
    if unchanged is None or unchanged.returncode != 0:
        raise GuardError(
            EXIT_DIRTY,
            "method_guard: METHOD.md differs from HEAD; commit the protocol change first",
        )

    blob = git_blob_hash(root, method_rel)
    if blob is None:
        raise GuardError(EXIT_DIRTY, "method_guard: cannot hash METHOD.md in git")
    return commit, blob


def _parse_time(value: object, source: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise GuardError(EXIT_BAD_RECORD, f"method_guard: missing timestamp in {source}")
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as error:
        raise GuardError(
            EXIT_BAD_RECORD, f"method_guard: malformed timestamp in {source}"
        ) from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise GuardError(
            EXIT_BAD_RECORD, f"method_guard: unreadable prior record {path.as_posix()}"
        ) from error
    if not isinstance(payload, dict):
        raise GuardError(
            EXIT_BAD_RECORD, f"method_guard: prior record is not an object: {path.as_posix()}"
        )
    return payload


def _safe_text(value: object, flag: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise GuardError(EXIT_USAGE, f"method_guard: {flag} must be text")
    cleaned = " ".join(value.split())
    if not cleaned:
        return None
    if len(cleaned) > MAX_REASON_CHARS:
        raise GuardError(
            EXIT_USAGE, f"method_guard: {flag} is longer than {MAX_REASON_CHARS} characters"
        )
    return cleaned


def _claim_access(payload: dict[str, Any], path: Path) -> Access:
    if payload.get("mode") != "test":
        raise GuardError(EXIT_BAD_RECORD, f"method_guard: bad mode in {path.as_posix()}")
    run_id = payload.get("run_id")
    stage = payload.get("stage")
    method = payload.get("method_sha256")
    if not isinstance(run_id, str) or not RUN_ID_PATTERN.match(run_id):
        raise GuardError(EXIT_BAD_RECORD, f"method_guard: bad run id in {path.as_posix()}")
    if stage not in STAGES:
        raise GuardError(EXIT_BAD_RECORD, f"method_guard: bad stage in {path.as_posix()}")
    if not isinstance(method, str) or not re.fullmatch(r"[0-9a-f]{64}", method):
        raise GuardError(EXIT_BAD_RECORD, f"method_guard: bad method hash in {path.as_posix()}")
    return Access(
        run_id=run_id,
        stage=stage,
        mode="test",
        method_sha256=method,
        at=_parse_time(payload.get("created_at"), path.as_posix()),
        source=path.as_posix(),
    )


def load_claims(claims_dir: Path) -> list[Access]:
    """Load every test claim; any malformed claim fails closed."""

    if not claims_dir.exists():
        return []
    accesses: list[Access] = []
    for path in sorted(claims_dir.glob("*.json")):
        payload = _read_json_object(path)
        accesses.append(_claim_access(payload, path))
    return accesses


def _run_access(payload: dict[str, Any], path: Path) -> Access | None:
    if payload.get("mode") == "dev":
        return None
    if payload.get("mode") != "test":
        raise GuardError(EXIT_BAD_RECORD, f"method_guard: bad mode in {path.as_posix()}")
    status = payload.get("status")
    if status not in {"running", "complete", "aborted"}:
        raise GuardError(EXIT_BAD_RECORD, f"method_guard: bad status in {path.as_posix()}")
    run_id = payload.get("run_id")
    stage = payload.get("stage")
    method = payload.get("method_sha256")
    if not isinstance(run_id, str) or not RUN_ID_PATTERN.match(run_id):
        raise GuardError(EXIT_BAD_RECORD, f"method_guard: bad run id in {path.as_posix()}")
    if stage not in STAGES:
        raise GuardError(EXIT_BAD_RECORD, f"method_guard: bad stage in {path.as_posix()}")
    if not isinstance(method, str) or not re.fullmatch(r"[0-9a-f]{64}", method):
        raise GuardError(EXIT_BAD_RECORD, f"method_guard: bad method hash in {path.as_posix()}")
    return Access(
        run_id=run_id,
        stage=stage,
        mode="test",
        method_sha256=method,
        at=_parse_time(payload.get("started_at"), path.as_posix()),
        source=path.as_posix(),
    )


def load_run_records(runs_dir: Path) -> list[Access]:
    """Load test run records; malformed records fail closed."""

    if not runs_dir.exists():
        return []
    accesses: list[Access] = []
    for path in sorted(runs_dir.glob("*/run.json")):
        payload = _read_json_object(path)
        access = _run_access(payload, path)
        if access is not None:
            accesses.append(access)
    return accesses


def _latest(accesses: Sequence[Access]) -> Access | None:
    if not accesses:
        return None
    return max(accesses, key=lambda access: (access.at, access.run_id))


def _same_stage(access: Access, stage: str) -> bool:
    return access.stage == stage or access.stage == "all" or stage == "all"


def _merge(claims: Sequence[Access], records: Sequence[Access]) -> list[Access]:
    """Prefer a claim over a run record with the same run id (claims are written first)."""

    by_run: dict[str, Access] = {}
    for access in records:
        by_run[access.run_id] = access
    for access in claims:
        by_run[access.run_id] = access
    return sorted(by_run.values(), key=lambda access: (access.at, access.run_id))


def _claim_path(runs_dir: Path, run_id: str) -> Path:
    return runs_dir / CLAIMS_DIRNAME / f"{run_id}.json"


def write_claim(
    runs_dir: Path,
    *,
    run_id: str,
    stage: str,
    method_sha256: str,
    method_blob: str,
    commit: str,
    decision: str,
    prior_run_id: str | None,
    rerun_reason: str | None,
    method_approval: str | None,
) -> Path:
    """Write the access claim exclusively; return its path."""

    path = _claim_path(runs_dir, run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "mode": "test",
        "stage": stage,
        "method_sha256": method_sha256,
        "method_git_blob": method_blob,
        "commit": commit,
        "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "decision": decision,
        "prior_run_id": prior_run_id,
        "rerun_reason": rerun_reason,
        "method_approval": method_approval,
    }
    serialised = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    try:
        with path.open("x", encoding="utf-8") as handle:
            handle.write(serialised)
    except FileExistsError as error:
        raise GuardError(
            EXIT_RUN_ID_CONFLICT,
            f"method_guard: claim already exists for run id {run_id}",
        ) from error
    except OSError as error:
        raise GuardError(
            EXIT_USAGE, f"method_guard: cannot write claim {path.as_posix()}"
        ) from error
    return path


def check_access(
    *,
    root: Path,
    method_path: Path,
    runs_dir: Path,
    mode: str,
    stage: str,
    run_id: str | None,
    rerun_reason: str | None,
    method_approval: str | None,
) -> dict[str, Any]:
    """Decide whether a run may read the test split, writing a claim on success."""

    if mode == "dev":
        return {
            "allowed": True,
            "decision": "dev-run",
            "mode": mode,
            "stage": stage,
            "method_sha256": sha256_file(method_path) if method_path.is_file() else None,
        }

    if run_id is None or not RUN_ID_PATTERN.match(run_id):
        raise GuardError(
            EXIT_USAGE,
            "method_guard: --run-id is required for --mode test (letters, digits, . _ -)",
        )
    if not method_path.is_file():
        raise GuardError(EXIT_USAGE, "method_guard: METHOD.md not found")

    method_sha = sha256_file(method_path)
    method_rel = method_relative_path(root, method_path)
    commit, method_blob = require_clean_tree(root, method_rel)

    claims = load_claims(runs_dir / CLAIMS_DIRNAME)
    records = load_run_records(runs_dir)
    accesses = _merge(claims, records)

    same_id = next((access for access in accesses if access.run_id == run_id), None)
    if same_id is not None:
        if same_id.stage != stage or same_id.method_sha256 != method_sha:
            raise GuardError(
                EXIT_RUN_ID_CONFLICT,
                f"method_guard: run id {run_id} is already used with another stage or method",
            )
        return {
            "allowed": True,
            "decision": "already-claimed",
            "mode": mode,
            "stage": stage,
            "run_id": run_id,
            "method_sha256": method_sha,
            "method_changed": False,
            "prior_run_id": same_id.run_id,
            "claim_path": _claim_path(runs_dir, run_id).as_posix(),
        }

    prior_any = _latest(accesses)
    prior_stage = _latest([access for access in accesses if _same_stage(access, stage)])
    method_changed = prior_any is not None and prior_any.method_sha256 != method_sha

    if method_changed:
        if not rerun_reason:
            raise GuardError(
                EXIT_NEED_REASON,
                "method_guard: METHOD.md changed since test access "
                f"{prior_any.run_id}; pass --rerun-reason",
            )
        if not method_approval:
            raise GuardError(
                EXIT_NEED_APPROVAL,
                "method_guard: METHOD.md changed since test access "
                f"{prior_any.run_id}; the captain approval reference is required",
            )
        decision = "method-change-rerun" if prior_stage is not None else "method-change-new-stage"
    elif prior_stage is not None:
        if not rerun_reason:
            raise GuardError(
                EXIT_NEED_REASON,
                f"method_guard: stage {stage} already read test data in "
                f"{prior_stage.run_id}; pass --rerun-reason",
            )
        decision = "rerun"
    elif prior_any is not None:
        decision = "new-stage"
    else:
        decision = "first-run"

    claim = write_claim(
        runs_dir,
        run_id=run_id,
        stage=stage,
        method_sha256=method_sha,
        method_blob=method_blob,
        commit=commit,
        decision=decision,
        prior_run_id=prior_stage.run_id if prior_stage is not None else None,
        rerun_reason=rerun_reason,
        method_approval=method_approval,
    )
    return {
        "allowed": True,
        "decision": decision,
        "mode": mode,
        "stage": stage,
        "run_id": run_id,
        "commit": commit,
        "method_sha256": method_sha,
        "method_git_blob": method_blob,
        "method_changed": method_changed,
        "prior_run_id": prior_stage.run_id if prior_stage is not None else None,
        "claim_path": claim.as_posix(),
    }


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mode", choices=("dev", "test"), required=True)
    parser.add_argument("--stage", choices=STAGES, default="all")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--rerun-reason", default=None)
    parser.add_argument("--method-approval", default=None)
    parser.add_argument("--root", default=".", help="repository root (default: current directory)")
    parser.add_argument("--method", default="METHOD.md", help="method path, relative to --root")
    parser.add_argument(
        "--runs-dir", default="results/runs", help="run records directory, relative to --root"
    )
    parser.add_argument("--print-hash", action="store_true", help="print the method hash and exit")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    root = Path(args.root).resolve()
    method_path = (root / args.method).resolve()
    runs_dir = (root / args.runs_dir).resolve()

    try:
        reason = _safe_text(args.rerun_reason, "--rerun-reason")
        approval = _safe_text(args.method_approval, "--method-approval")
        if args.print_hash:
            if not method_path.is_file():
                raise GuardError(EXIT_USAGE, "method_guard: METHOD.md not found")
            method_rel = method_relative_path(root, method_path)
            print(
                json.dumps(
                    {
                        "method_sha256": sha256_file(method_path),
                        "method_git_blob": git_blob_hash(root, method_rel),
                    },
                    sort_keys=True,
                )
            )
            return EXIT_OK
        decision = check_access(
            root=root,
            method_path=method_path,
            runs_dir=runs_dir,
            mode=args.mode,
            stage=args.stage,
            run_id=args.run_id,
            rerun_reason=reason,
            method_approval=approval,
        )
    except GuardError as error:
        print(error.message, file=sys.stderr)
        return error.code

    print(json.dumps(decision, sort_keys=True))
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())

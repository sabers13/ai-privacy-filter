#!/usr/bin/env python3
"""Run the fixed triage subset against real backends.

This is the live entry point behind ``make live``. It takes the shared
live-LLM lock for every real backend and, for the optional Ollama backend, also
the server lock. ``make reproduce`` never calls this script.

Examples::

    set -a; . ~/.config/ai-privacy-filter/private.env; set +a
    AIPF_LOCK_DIR=/path/to/shared/state uv run python scripts/run_triage.py --live
    AIPF_LOCK_DIR=/path/to/shared/state uv run python scripts/run_triage.py \
        --live --backends ollama --run-id <same-run-id> --resume

Exit codes: 0 complete, 1 failure, 2 guard refused (other guard codes pass
through), 3 quota checkpoint written.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Final

from ai_privacy_filter.evaluation.locks import (
    LIVE_LOCK_NAME,
    SERVER_LOCK_NAME,
    DirectoryLock,
    LockTimeout,
)
from ai_privacy_filter.evaluation.triage_eval import (
    DEFAULT_BACKENDS,
    LIVE_BACKENDS,
    GuardRefused,
    default_run_id,
    git_commit,
    run_triage,
)

ROOT: Final = Path(__file__).resolve().parents[1]
EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_QUOTA = 3


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--backends",
        default=",".join(DEFAULT_BACKENDS),
        help=f"comma-separated backend names (default: {','.join(DEFAULT_BACKENDS)})",
    )
    parser.add_argument("--mode", choices=("test", "dev"), default="test")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--rerun-reason", default=None)
    parser.add_argument("--method-approval", default=None)
    parser.add_argument("--timeout", type=int, default=None, help="call timeout in seconds")
    parser.add_argument("--resume", action="store_true", help="skip stored responses")
    parser.add_argument("--limit", type=int, default=None, help="dev-mode enquiry limit")
    parser.add_argument("--no-warmup", action="store_true", help="skip the dev warm-up call")
    parser.add_argument(
        "--live",
        action="store_true",
        help="hold the shared live-LLM lock (required for real backends)",
    )
    parser.add_argument("--lock-wait", type=int, default=3600, help="lock wait in seconds")
    parser.add_argument("--root", default=str(ROOT), help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    root = Path(args.root).resolve()
    backends = [name.strip() for name in str(args.backends).split(",") if name.strip()]
    unknown = sorted(set(backends) - set(DEFAULT_BACKENDS) - {"ollama", "fake"})
    if unknown:
        print(f"run_triage: unknown backend(s): {', '.join(unknown)}", file=sys.stderr)
        return EXIT_FAILURE
    real = [name for name in backends if name in LIVE_BACKENDS]
    if real and not args.live:
        print("run_triage: real backends need --live (the shared lock)", file=sys.stderr)
        return EXIT_FAILURE
    if args.mode == "test" and args.limit is not None:
        print("run_triage: --limit is only allowed with --mode dev", file=sys.stderr)
        return EXIT_FAILURE

    run_id = str(args.run_id) if args.run_id else default_run_id(git_commit(root))
    command = ["run_triage", *sys.argv[1:]]

    live_lock = DirectoryLock(
        LIVE_LOCK_NAME,
        timeout_s=float(args.lock_wait),
    )
    server_lock = DirectoryLock(
        SERVER_LOCK_NAME,
        timeout_s=float(args.lock_wait),
    )
    try:
        if real:
            live_lock.acquire()
            print(f"run_triage: live lock held; run id {run_id}", file=sys.stderr)
        if "ollama" in backends:
            server_lock.acquire()
            print("run_triage: server lock held for the Ollama stage", file=sys.stderr)
        run_dir, summary = run_triage(
            root=root,
            backend_names=backends,
            mode=args.mode,
            run_id=run_id,
            rerun_reason=args.rerun_reason,
            method_approval=args.method_approval,
            timeout_s=args.timeout,
            resume=args.resume,
            limit=args.limit,
            warmup=not args.no_warmup,
            command=command,
        )
    except GuardRefused as exc:
        print(f"run_triage: {exc}", file=sys.stderr)
        return exc.exit_code
    except LockTimeout as exc:
        print(f"run_triage: {exc}", file=sys.stderr)
        return EXIT_FAILURE
    finally:
        server_lock.release()
        live_lock.release()

    print(
        json.dumps({"run_dir": str(run_dir.relative_to(root)), "summary": summary}, sort_keys=True)
    )
    if summary.get("quota"):
        print(
            "run_triage: QUOTA_EXHAUSTED checkpoint written; resume after the reset",
            file=sys.stderr,
        )
        return EXIT_QUOTA
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())

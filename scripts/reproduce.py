#!/usr/bin/env python3
"""Rebuild triage metrics and the report from stored responses.

No network and no model call. The script reads the stored responses, the
committed synthetic corpus and the run record, recomputes every number, compares
it with the stored ``metrics.json`` (timestamps excepted) and rewrites both the
metrics and ``report.md``.

Exit codes: 0 every number reproduced, 1 a number changed, 2 usage error.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Final

from ai_privacy_filter.evaluation.triage_eval import latest_run_dir, reproduce

ROOT: Final = Path(__file__).resolve().parents[1]


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--run-dir",
        default=None,
        help="run directory (default: the newest triage run under results/runs)",
    )
    parser.add_argument("--root", default=str(ROOT), help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    root = Path(args.root).resolve()
    try:
        run_dir = Path(args.run_dir).resolve() if args.run_dir else latest_run_dir(root)
    except FileNotFoundError as exc:
        print(f"reproduce: {exc}", file=sys.stderr)
        return 2
    if not (run_dir / "run.json").is_file():
        print(f"reproduce: no run record in {run_dir.name}", file=sys.stderr)
        return 2
    metrics, match = reproduce(root=root, run_dir=run_dir)
    backends = ", ".join(
        f"{name}={entry.get('success', 0)}/{entry.get('expected', 0)}"
        for name, entry in metrics.get("per_backend", {}).items()
        if entry.get("available")
    )
    if match:
        print(f"reproduce: {run_dir.name}: every number reproduced ({backends})")
        return 0
    print(
        f"reproduce: {run_dir.name}: MISMATCH between stored and recomputed numbers "
        f"({backends}); metrics.json was rewritten",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

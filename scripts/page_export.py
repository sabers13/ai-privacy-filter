#!/usr/bin/env python3
"""Export ``results/page/examples.json`` and ``results/page/results.json``.

The examples come from a completed test-mode triage run. The detection block
comes from the detection run; pass ``--detection-metrics`` when it is not in
the default location.

Exit codes: 0 written, 1 failure, 2 usage error.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Final

from ai_privacy_filter.evaluation.page_data import export_page
from ai_privacy_filter.evaluation.triage_eval import latest_run_dir

ROOT: Final = Path(__file__).resolve().parents[1]


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--run-dir",
        default=None,
        help="triage run directory (default: the newest triage run)",
    )
    parser.add_argument(
        "--backend",
        default="claude-cli",
        help="backend whose stored drafts the page shows (default: claude-cli)",
    )
    parser.add_argument(
        "--fallback-backend",
        default="codex-cli",
        help="backend used when the first has no successful draft (default: codex-cli)",
    )
    parser.add_argument(
        "--detection-metrics",
        default=None,
        help="detection metrics file (default: newest results/runs/*-detection-*/metrics.json)",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="output directory (default: results/page)",
    )
    parser.add_argument("--root", default=str(ROOT), help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    root = Path(args.root).resolve()
    try:
        run_dir = Path(args.run_dir).resolve() if args.run_dir else latest_run_dir(root)
    except FileNotFoundError as exc:
        print(f"page_export: {exc}", file=sys.stderr)
        return 2
    output_dir = Path(args.output_dir).resolve() if args.output_dir else root / "results" / "page"
    detection = Path(args.detection_metrics).resolve() if args.detection_metrics else None
    backend = str(args.backend)
    try:
        examples_path, results_path, notes = export_page(
            root=root,
            run_dir=run_dir,
            backend=backend,
            detection_path=detection,
            output_dir=output_dir,
        )
    except (FileNotFoundError, ValueError) as exc:
        if backend != args.fallback_backend:
            print(
                f"page_export: {backend} produced no page data ({exc}); "
                f"trying {args.fallback_backend}",
                file=sys.stderr,
            )
            try:
                examples_path, results_path, notes = export_page(
                    root=root,
                    run_dir=run_dir,
                    backend=str(args.fallback_backend),
                    detection_path=detection,
                    output_dir=output_dir,
                )
            except (FileNotFoundError, ValueError) as second:
                print(f"page_export: {second}", file=sys.stderr)
                return 1
        else:
            print(f"page_export: {exc}", file=sys.stderr)
            return 1

    def _display(path: Path) -> str:
        try:
            return str(path.relative_to(root))
        except ValueError:
            return path.name

    print(f"page_export: wrote {_display(examples_path)} and {_display(results_path)}")
    for note in notes:
        print(f"page_export: {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

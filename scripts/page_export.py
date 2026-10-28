#!/usr/bin/env python3
"""Rebuild the whole ``results/page/`` bundle from the committed results.

The bundle holds ``kpis.json``, ``charts/*.json`` with their CSV twins,
``examples.json``, ``demos.json``, the demo material (worked example, try-it
samples and the three ``make demo`` enquiries), the screenshots and
``MANIFEST.md``. It is deterministic: the page timestamp comes from the stored
triage metrics and every number keeps its source file and JSON path.

Exit codes: 0 written, 1 failure, 2 usage error.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Final

from ai_privacy_filter.evaluation.page_bundle import export_bundle

ROOT: Final = Path(__file__).resolve().parents[1]


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
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
        help="detection metrics file (default: the committed results/)",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="output directory (default: results/page)",
    )
    parser.add_argument("--root", default=str(ROOT), help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def _run(root: Path, output_dir: Path, backend: str, detection: Path | None) -> dict[str, Path]:
    return export_bundle(
        root=root,
        output_dir=output_dir,
        backend=backend,
        detection_path=detection,
    )


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    root = Path(args.root).resolve()
    output_dir = Path(args.output_dir).resolve() if args.output_dir else root / "results" / "page"
    detection = Path(args.detection_metrics).resolve() if args.detection_metrics else None
    backend = str(args.backend)
    try:
        written = _run(root, output_dir, backend, detection)
    except (FileNotFoundError, ValueError) as exc:
        if backend != args.fallback_backend:
            print(
                f"page_export: {backend} produced no page data ({exc}); "
                f"trying {args.fallback_backend}",
                file=sys.stderr,
            )
            try:
                written = _run(root, output_dir, str(args.fallback_backend), detection)
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

    print(f"page_export: wrote {_display(written['kpis'])} and {_display(written['manifest'])}")
    print(f"page_export: bundle root {_display(output_dir)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

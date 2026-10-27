#!/usr/bin/env python3
"""Download, verify and parse GermEval 2014 NER into ``data/external/germeval/``.

The original GermEval 2014 page publishes the data under CC BY 4.0 through a
Google Drive folder. The direct file links resolve non-interactively, so no
Hugging Face mirror is needed.

Every file is pinned by byte size and SHA-256 in
:data:`ai_privacy_filter.datasets.germeval.SPLIT_SOURCES`; a mismatch stops the
run. Downloaded TSV and the parsed JSONL stay under ``data/external/``, which is
git-ignored, and no sentence text is printed.

Usage::

    uv run python data/get_germeval.py                # download, verify, parse
    uv run python data/get_germeval.py --verify-only  # re-check existing files
    uv run python data/get_germeval.py --download-only --force
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
try:
    from ai_privacy_filter.datasets import germeval
except ModuleNotFoundError:  # allow a plain `python data/get_germeval.py` run
    sys.path.insert(0, str(REPO_ROOT / "src"))
    from ai_privacy_filter.datasets import germeval


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Download and parse the GermEval 2014 NER data (CC BY 4.0).",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=REPO_ROOT / "data" / "external" / "germeval",
        help="download directory (default: data/external/germeval)",
    )
    parser.add_argument(
        "--split",
        action="append",
        choices=[source.split for source in germeval.SPLIT_SOURCES],
        help="limit the run to one split; repeat for more (default: all)",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--verify-only",
        action="store_true",
        help="check existing files against the pinned size and sha256; do not download or parse",
    )
    mode.add_argument(
        "--download-only",
        action="store_true",
        help="download and verify, but do not parse",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="download again even when a verified file already exists",
    )
    return parser


def _display(path: Path) -> str:
    """Show a path relative to the repository, never an absolute home path."""
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return path.name


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    data_dir: Path = args.data_dir
    splits: list[str] | None = args.split
    selected = germeval.select_sources(splits)

    try:
        if args.verify_only:
            for source in selected:
                germeval.verify_file(data_dir / source.filename, source)
                print(f"{source.split}: verified {source.filename}")
            return 0

        files = germeval.fetch_sources(data_dir, splits=splits, force=args.force)
        for source in selected:
            print(f"{source.split}: verified {files[source.split].name}")

        if args.download_only:
            return 0

        parsed_dir = data_dir / "parsed"
        results: dict[str, tuple[Path, germeval.SplitStats]] = {}
        for source in selected:
            sentences = germeval.parse_file(files[source.split], split=source.split)
            stats = germeval.summarise(sentences, split=source.split)
            germeval.write_parsed(sentences, parsed_dir, split=source.split)
            results[source.split] = (files[source.split], stats)
            print(
                f"{source.split}: {stats.sentences} sentences, {stats.tokens} tokens, "
                f"{stats.spans} spans ({stats.primary_spans} primary)"
            )

        if splits is None:
            germeval.check_published_sizes(
                {split: stats.sentences for split, (_, stats) in results.items()}
            )

        manifest = germeval.write_manifest(data_dir, results, retrieved_at=datetime.now(UTC))
        print(f"parsed files: {_display(parsed_dir)}")
        print(f"manifest: {_display(manifest)}")
        print(f"licence: {germeval.LICENCE} ({germeval.LICENCE_URL})")
        return 0
    except germeval.GermEvalError as exc:
        print(f"get_germeval: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

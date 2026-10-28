#!/usr/bin/env python3
"""Build and join the manual draft-quality rubric.

``METHOD.md`` section 7.7 scores a fixed 10 of the 40 drafts on relevance,
placeholder handling and German quality (0/1/2 each). Backend and model labels
are removed and the drafts are shuffled with seed ``20261027``.

Two steps:

1. ``--mode sheet`` writes ``rubric_sheet.json`` (blinded drafts plus the
   redacted enquiry) and ``rubric_key.json`` (token -> enquiry id) into a
   directory outside the repository.
2. ``--mode join`` reads the scored sheet and the key, writes
   ``rubric.json`` into the run directory with per-enquiry scores, means and
   ranges.

The reviewer is a single person; that limitation is reported.
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path
from typing import Any, Final

from ai_privacy_filter.evaluation.corpus import (
    load_split,
    rubric_sample,
    select_triage_subset,
)
from ai_privacy_filter.evaluation.triage_eval import (
    STATUS_OK,
    _load_response,
    _read_json,
    _write_json,
    latest_run_dir,
)
from ai_privacy_filter.pipeline import default_pipeline

ROOT: Final = Path(__file__).resolve().parents[1]
SEED: Final = 20261027
DIMENSIONS: Final[tuple[str, ...]] = ("relevance", "placeholder_handling", "german_quality")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mode", choices=("sheet", "join"), required=True)
    parser.add_argument("--run-dir", default=None)
    parser.add_argument("--backend", default="claude-cli")
    parser.add_argument(
        "--out-dir",
        default=None,
        help="sheet directory (default: outside the repository, under the state dir)",
    )
    parser.add_argument("--root", default=str(ROOT), help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def _run_dir(root: Path, value: str | None) -> Path:
    return Path(value).resolve() if value else latest_run_dir(root)


def _sheet(root: Path, run_dir: Path, backend: str, out_dir: Path) -> int:
    record = _read_json(run_dir / "run.json")
    if str(record.get("mode")) != "test":
        print("rubric: the rubric is scored on the test run only", file=sys.stderr)
        return 2
    subset = select_triage_subset(load_split(root / "data" / "synthetic" / "test.jsonl", "test"))
    sample = rubric_sample(subset)
    pipeline = default_pipeline()
    rows: list[dict[str, Any]] = []
    for enquiry in sample:
        stored = _load_response(root, backend, enquiry.id)
        if stored is None or stored.get("status") != STATUS_OK:
            continue
        parsed = stored.get("parsed")
        draft = parsed.get("reply_draft") if isinstance(parsed, dict) else None
        if not isinstance(draft, str) or not draft.strip():
            continue
        redaction = pipeline.redact_text(enquiry.text)
        rows.append({"id": enquiry.id, "enquiry": redaction.redacted_text, "draft": draft})
    random.Random(SEED).shuffle(rows)
    items = [
        {"token": f"d{position:02d}", "enquiry": row["enquiry"], "draft": row["draft"]}
        for position, row in enumerate(rows, start=1)
    ]
    key = {f"d{position:02d}": row["id"] for position, row in enumerate(rows, start=1)}
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_json(
        out_dir / "rubric_sheet.json",
        {
            "seed": SEED,
            "backend_labels_removed": True,
            "dimensions": list(DIMENSIONS),
            "scale": {"0": "does not meet", "1": "partly", "2": "meets"},
            "items": items,
        },
    )
    _write_json(out_dir / "rubric_key.json", key)
    print(f"rubric: {len(items)} drafts in {out_dir / 'rubric_sheet.json'}")
    return 0


def _join(run_dir: Path, backend: str, out_dir: Path) -> int:
    sheet = _read_json(out_dir / "rubric_sheet.json")
    key = _read_json(out_dir / "rubric_key.json")
    items = sheet.get("items")
    if not isinstance(items, list):
        print("rubric: sheet has no items", file=sys.stderr)
        return 2
    scores: dict[str, dict[str, Any]] = {}
    invented = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        token = str(item.get("token"))
        enquiry_id = key.get(token)
        if not isinstance(enquiry_id, str):
            continue
        row: dict[str, Any] = {}
        for dimension in DIMENSIONS:
            value = item.get(dimension)
            if value not in (0, 1, 2):
                print(f"rubric: {token} has no valid {dimension}", file=sys.stderr)
                return 2
            row[dimension] = int(value)
        row["invented_fact"] = bool(item.get("invented_fact", False))
        invented += int(row["invented_fact"])
        scores[enquiry_id] = row
    means: dict[str, float | None] = {}
    ranges: dict[str, list[int] | None] = {}
    for dimension in DIMENSIONS:
        values = [row[dimension] for row in scores.values()]
        means[dimension] = sum(values) / len(values) if values else None
        ranges[dimension] = [min(values), max(values)] if values else None
    _write_json(
        run_dir / "rubric.json",
        {
            "backend": backend,
            "seed": SEED,
            "n": len(scores),
            "reviewer": "single reviewer",
            "dimensions": list(DIMENSIONS),
            "means": means,
            "ranges": ranges,
            "invented_fact_drafts": invented,
            "scores": scores,
        },
    )
    print(f"rubric: joined {len(scores)} scores into {run_dir.name}/rubric.json")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    root = Path(args.root).resolve()
    try:
        run_dir = _run_dir(root, args.run_dir)
    except FileNotFoundError as exc:
        print(f"rubric: {exc}", file=sys.stderr)
        return 2
    out_dir = (
        Path(args.out_dir).resolve()
        if args.out_dir
        else Path.home() / ".local" / "state" / "ai-privacy-filter" / f"rubric-{run_dir.name}"
    )
    if args.mode == "sheet":
        return _sheet(root, run_dir, str(args.backend), out_dir)
    return _join(run_dir, str(args.backend), out_dir)


if __name__ == "__main__":
    raise SystemExit(main())

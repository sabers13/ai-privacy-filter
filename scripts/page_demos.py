#!/usr/bin/env python3
"""Generate the 15 page examples with a real backend.

The fixed 40-enquiry subset has no detection miss in this corpus, so the page
needs a small separate demo set: three per category, with two or three examples
chosen because the displayed detector misses a gold span. The examples are
synthetic test enquiries; the reply drafts are real backend output.

The run takes the shared live-LLM lock, calls the method guard with the triage
run id (already claimed, so this is not a scored rerun), and stores each reply
under ``results/page/responses/`` with placeholders only and a hash. A reply
that contains a gold value is discarded. The backend falls back to
``codex-cli`` for an example when ``claude-cli`` fails.

Usage::

    set -a; . ~/.config/ai-privacy-filter/private.env; set +a
    AIPF_LOCK_DIR=/path/to/shared/state uv run python scripts/page_demos.py \
        --run-id 20261027T131500Z-triage-a1ad8ff
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Final

from ai_privacy_filter.evaluation.corpus import CATEGORY_ORDER, load_split, sha256_text
from ai_privacy_filter.evaluation.locks import LIVE_LOCK_NAME, DirectoryLock, LockTimeout
from ai_privacy_filter.evaluation.page_data import detection_for
from ai_privacy_filter.evaluation.triage_eval import (
    STATUS_LEAK,
    STATUS_OK,
    GuardRefused,
    _response_record,
    _write_json,
    build_backend,
    git_commit,
    leaked_types,
    method_guard_check,
    utc_now,
)
from ai_privacy_filter.guard import GuardBlockedError, require_clean
from ai_privacy_filter.models import Enquiry
from ai_privacy_filter.pipeline import default_pipeline
from ai_privacy_filter.triage import TRIAGE_SCHEMA, build_prompt

ROOT: Final = Path(__file__).resolve().parents[1]
EXAMPLES_COUNT: Final = 15
PER_CATEGORY: Final = 3
MISS_TARGET: Final = 3
TIMEOUT_S: Final = 120


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run-id", required=True, help="existing triage run id (for the guard)")
    parser.add_argument("--backend", default="claude-cli")
    parser.add_argument("--fallback-backend", default="codex-cli")
    parser.add_argument("--timeout", type=int, default=TIMEOUT_S)
    parser.add_argument("--lock-wait", type=int, default=3600)
    parser.add_argument("--root", default=str(ROOT), help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def _select(rows: list[Enquiry]) -> tuple[list[Enquiry], set[str]]:
    """Pick 3 per category, with up to 3 examples that have a detection miss."""
    misses: dict[str, list[Any]] = {}
    for enquiry in rows:
        _, missing = detection_for(enquiry)
        if missing:
            misses[enquiry.id] = missing
    by_category: dict[str, list[Enquiry]] = {category.value: [] for category in CATEGORY_ORDER}
    for enquiry in sorted(rows, key=lambda row: row.id):
        by_category[enquiry.gold_category.value].append(enquiry)

    deliberate: list[Enquiry] = []
    used: set[str] = set()
    for category in CATEGORY_ORDER:
        if len(deliberate) >= MISS_TARGET:
            break
        candidate = next(
            (row for row in by_category[category.value] if row.id in misses and row.id not in used),
            None,
        )
        if candidate is not None:
            deliberate.append(candidate)
            used.add(candidate.id)

    selected: list[Enquiry] = []
    for category in CATEGORY_ORDER:
        wanted = [row for row in deliberate if row.gold_category is category]
        for row in by_category[category.value]:
            if len(wanted) >= PER_CATEGORY:
                break
            if row.id in used or row.id in {item.id for item in wanted}:
                continue
            wanted.append(row)
        selected.extend(wanted[:PER_CATEGORY])

    if len(selected) < EXAMPLES_COUNT:
        raise RuntimeError(f"only {len(selected)} page candidates, expected {EXAMPLES_COUNT}")
    return selected[:EXAMPLES_COUNT], set(misses)


def _call(
    enquiry: Enquiry,
    backend_name: str,
    *,
    run_id: str,
    timeout_s: int,
) -> dict[str, Any] | None:
    backend = build_backend(backend_name)
    redaction = default_pipeline().redact_text(enquiry.text)
    prompt = build_prompt(redaction.redacted_text)
    prompt_hash = sha256_text(prompt)
    try:
        require_clean(redaction.redacted_text, redaction.mapping)
    except GuardBlockedError:
        return None
    if leaked_types(prompt, enquiry.gold_spans):
        return None
    result = backend.complete_json(prompt, TRIAGE_SCHEMA, timeout_s)
    if result.error_kind is not None or result.parsed is None:
        return None
    parsed = result.parsed
    leaks = set(leaked_types(result.text, enquiry.gold_spans))
    draft = parsed.get("reply_draft")
    if isinstance(draft, str):
        leaks |= set(leaked_types(draft, enquiry.gold_spans))
    if leaks:
        return None
    model = result.model
    if backend_name == "byok" and model:
        model = f"sha256:{sha256_text(model)[:12]}"
    return _response_record(
        run_id=run_id,
        backend=backend_name,
        enquiry_id=enquiry.id,
        status=STATUS_OK,
        error_kind=None,
        model=model,
        latency_ms=result.latency_ms,
        usage=result.usage,
        prompt_sha256=prompt_hash,
        text=result.text,
        parsed=parsed,
    )


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    root = Path(args.root).resolve()
    try:
        decision = method_guard_check(
            root=root,
            run_id=str(args.run_id),
            mode="test",
            rerun_reason=None,
            method_approval=None,
        )
    except GuardRefused as exc:
        print(f"page_demos: {exc}", file=sys.stderr)
        return exc.exit_code
    if not decision.get("allowed"):
        print("page_demos: method guard refused the run", file=sys.stderr)
        return 2
    rows = load_split(root / "data" / "synthetic" / "test.jsonl", "test")
    selected, miss_ids = _select(rows)
    lock = DirectoryLock(LIVE_LOCK_NAME, timeout_s=float(args.lock_wait))
    try:
        lock.acquire()
        print(f"page_demos: live lock held; {len(selected)} examples", file=sys.stderr)
        items: list[dict[str, Any]] = []
        for enquiry in selected:
            used_backend = str(args.backend)
            blocked = False
            stored: dict[str, Any] | None = None
            if enquiry.id in miss_ids:
                redaction = default_pipeline().redact_text(enquiry.text)
                prompt = build_prompt(redaction.redacted_text)
                prompt_leaks = leaked_types(prompt, enquiry.gold_spans)
                if prompt_leaks:
                    stored = _response_record(
                        run_id=str(args.run_id),
                        backend=used_backend,
                        enquiry_id=enquiry.id,
                        status=STATUS_LEAK,
                        error_kind=STATUS_LEAK,
                        model=None,
                        latency_ms=0,
                        usage=None,
                        prompt_sha256=sha256_text(prompt),
                        text=None,
                        parsed=None,
                        leaked=prompt_leaks,
                    )
                    blocked = True
            if stored is None:
                stored = _call(
                    enquiry, used_backend, run_id=str(args.run_id), timeout_s=args.timeout
                )
            if stored is None and args.fallback_backend != args.backend:
                used_backend = str(args.fallback_backend)
                stored = _call(
                    enquiry, used_backend, run_id=str(args.run_id), timeout_s=args.timeout
                )
            if stored is None:
                print(
                    f"page_demos: no draft for {enquiry.id}; stopping",
                    file=sys.stderr,
                )
                return 1
            target = root / "results" / "page" / "responses" / f"{enquiry.id}.json"
            _write_json(target, stored)
            items.append(
                {
                    "id": enquiry.id,
                    "backend": used_backend,
                    "blocked": blocked,
                    "deliberate_miss": enquiry.id in miss_ids,
                    "response_sha256": stored["response_sha256"],
                    "prompt_sha256": stored["prompt_sha256"],
                }
            )
            state = "blocked (prompt leak)" if blocked else "ok"
            print(f"page_demos: {enquiry.id} {state} ({used_backend})", file=sys.stderr)
    except LockTimeout as exc:
        print(f"page_demos: {exc}", file=sys.stderr)
        return 1
    finally:
        lock.release()

    _write_json(
        root / "results" / "page" / "demos.json",
        {
            "schema_version": 1,
            "generated_at": utc_now(),
            "run_id": str(args.run_id),
            "commit": git_commit(root),
            "backend": str(args.backend),
            "fallback_backend": str(args.fallback_backend),
            "count": len(items),
            "deliberate_misses": sum(1 for item in items if item["deliberate_miss"]),
            "items": items,
        },
    )
    print(f"page_demos: wrote {len(items)} examples under results/page/", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

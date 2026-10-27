"""Page data export: 15 synthetic examples plus the results summary.

The page shows synthetic enquiries only. Detection spans and misses are
recomputed from the displayed detector (``detect.detect`` with the balanced
profile) so the page cannot drift from the code. Reply drafts come from the
stored triage run; a draft is restored with the in-memory mapping rebuilt from
the original text, so no mapping is persisted.

The 15 examples are three per category from the fixed 40-enquiry subset, and two
or three of them are chosen because the detector misses a gold span. Those are
the "deliberate misses" the page uses to show what the filter does not catch.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, Final

from ai_privacy_filter import __version__, detect
from ai_privacy_filter.evaluation.corpus import (
    CATEGORY_ORDER,
    canonical_json,
    load_split,
    select_triage_subset,
    sha256_text,
)
from ai_privacy_filter.evaluation.triage_eval import (
    STATUS_OK,
    _load_response,
    _read_json,
    _write_json,
    utc_now,
)
from ai_privacy_filter.models import (
    Category,
    Enquiry,
    EntityType,
    PageExample,
    PageResults,
    Span,
    Urgency,
)
from ai_privacy_filter.pseudonymise import redact, restore

EXAMPLES_COUNT: Final = 15
PER_CATEGORY: Final = 3
MAX_MISS_EXAMPLES: Final = 3

COMPATIBLE: Final[dict[EntityType, frozenset[EntityType]]] = {
    EntityType.PERSON: frozenset({EntityType.PERSON, EntityType.ORG}),
    EntityType.ORG: frozenset({EntityType.ORG, EntityType.PERSON}),
    EntityType.EMAIL: frozenset({EntityType.EMAIL, EntityType.URL}),
    EntityType.URL: frozenset({EntityType.URL, EntityType.EMAIL}),
    EntityType.PHONE: frozenset({EntityType.PHONE}),
    EntityType.IBAN: frozenset({EntityType.IBAN}),
    EntityType.ADDRESS_STREET: frozenset({EntityType.ADDRESS_STREET}),
    EntityType.POSTCODE_CITY: frozenset({EntityType.POSTCODE_CITY}),
    EntityType.VAT_ID: frozenset({EntityType.VAT_ID}),
    EntityType.DATE_OF_BIRTH: frozenset({EntityType.DATE_OF_BIRTH}),
}


def uncovered_gold_spans(text: str, gold: Sequence[Span], predicted: Sequence[Span]) -> list[Span]:
    """Return the gold spans that are not fully covered by a compatible prediction."""
    covered = [False] * len(text)
    for span in predicted:
        if span.start < 0 or span.end > len(text) or span.start >= span.end:
            continue
        for position in range(span.start, span.end):
            covered[position] = True
    misses: list[Span] = []
    for span in gold:
        compatible = COMPATIBLE.get(span.type, frozenset({span.type}))
        positions = [
            position
            for position in range(max(0, span.start), min(len(text), span.end))
            if covered[position]
            and any(
                other.type in compatible and other.start <= position < other.end
                for other in predicted
            )
        ]
        if len(positions) < max(0, span.end - span.start):
            misses.append(span)
    return misses


def detection_for(enquiry: Enquiry) -> tuple[list[Span], list[Span]]:
    """Return ``(detected, misses)`` for one enquiry."""
    predicted = detect.detect(enquiry.text)
    return predicted, uncovered_gold_spans(enquiry.text, enquiry.gold_spans, predicted)


def _eligible(
    subset: Sequence[Enquiry], root: Path, backend: str
) -> tuple[dict[str, dict[str, Any]], dict[str, list[Span]]]:
    responses: dict[str, dict[str, Any]] = {}
    misses: dict[str, list[Span]] = {}
    for enquiry in subset:
        stored = _load_response(root, backend, enquiry.id)
        if stored is None or stored.get("status") != STATUS_OK:
            continue
        parsed = stored.get("parsed")
        if not isinstance(parsed, dict):
            continue
        draft = parsed.get("reply_draft")
        if not isinstance(draft, str) or not draft.strip():
            continue
        if parsed.get("category") not in {member.value for member in Category}:
            continue
        if parsed.get("urgency") not in {member.value for member in Urgency}:
            continue
        _, missing = detection_for(enquiry)
        responses[enquiry.id] = stored
        misses[enquiry.id] = missing
    return responses, misses


def select_examples(
    subset: Sequence[Enquiry],
    *,
    root: Path,
    backend: str,
) -> tuple[list[Enquiry], list[str], dict[str, list[Span]]]:
    """Return ``(examples, notes, misses_by_id)`` in page order."""
    responses, misses = _eligible(subset, root, backend)
    by_id = {enquiry.id: enquiry for enquiry in subset}
    eligible = [enquiry for enquiry in subset if enquiry.id in responses]
    notes: list[str] = []

    miss_by_category: dict[Category, list[str]] = {category: [] for category in CATEGORY_ORDER}
    for enquiry in eligible:
        if misses[enquiry.id]:
            miss_by_category[enquiry.gold_category].append(enquiry.id)

    deliberate: list[str] = []
    for category in CATEGORY_ORDER:
        if len(deliberate) >= MAX_MISS_EXAMPLES:
            break
        if miss_by_category[category]:
            deliberate.append(miss_by_category[category][0])

    chosen: list[str] = []
    for category in CATEGORY_ORDER:
        wanted = [
            identifier for identifier in deliberate if by_id[identifier].gold_category is category
        ]
        for enquiry in eligible:
            if len(wanted) >= PER_CATEGORY:
                break
            if enquiry.gold_category is not category or enquiry.id in wanted:
                continue
            if misses[enquiry.id] and enquiry.id not in deliberate:
                continue
            wanted.append(enquiry.id)
        chosen.extend(wanted[:PER_CATEGORY])

    if len(chosen) < EXAMPLES_COUNT:
        for enquiry in eligible:
            if len(chosen) >= EXAMPLES_COUNT:
                break
            if enquiry.id not in chosen:
                chosen.append(enquiry.id)
    chosen = chosen[:EXAMPLES_COUNT]

    selected = [by_id[identifier] for identifier in chosen]
    found_misses = sum(1 for identifier in chosen if misses[identifier])
    if found_misses == 0:
        notes.append("no detection miss in the fixed subset; the page shows no miss example")
    elif found_misses < 2:
        notes.append(
            f"only {found_misses} detection miss available in the fixed subset; "
            "the page shows it as-is"
        )
    else:
        notes.append(f"{found_misses} deliberate detection misses from the fixed subset")
    notes.append(
        "examples are three per category from the fixed 40-enquiry subset; "
        "category and urgency are the backend answer, misses are gold spans the "
        "detector did not cover"
    )
    return selected, notes, misses


def build_examples(
    subset: Sequence[Enquiry],
    *,
    root: Path,
    backend: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Build the 15 page examples in contract shape."""
    selected, notes, misses = select_examples(subset, root=root, backend=backend)
    examples: list[dict[str, Any]] = []
    for enquiry in selected:
        stored = _load_response(root, backend, enquiry.id)
        if stored is None:
            continue
        parsed = stored.get("parsed")
        if not isinstance(parsed, dict):
            continue
        draft = parsed.get("reply_draft")
        if not isinstance(draft, str):
            continue
        predicted, _ = detection_for(enquiry)
        redaction = redact(enquiry.text, predicted)
        mapping = dict(redaction.mapping)
        example = PageExample(
            id=enquiry.id,
            original=enquiry.text,
            redacted=redaction.redacted_text,
            reply_draft_redacted=draft,
            reply_draft_restored=restore(draft, mapping).text,
            spans=predicted,
            misses=misses[enquiry.id],
            category=Category(str(parsed["category"])),
            urgency=Urgency(str(parsed["urgency"])),
            backend=backend,
            mock=False,
        )
        examples.append(example.model_dump(mode="json"))
    return examples, notes


def load_detection(root: Path, explicit: Path | None = None) -> dict[str, Any]:
    """Load the T11 detection block from a metrics file.

    Accepted shapes: a page-shaped ``{"detection": {"synthetic", "germeval"}}``
    file, a file with the two top-level keys, or a T11 ``metrics.json`` whose
    top level carries those keys under ``detection``.
    """
    candidates: list[Path] = []
    if explicit is not None:
        candidates.append(explicit)
    else:
        candidates.extend(sorted(root.glob("results/runs/*-detection-*/metrics.json")))
        candidates.append(root / "results" / "detection" / "metrics.json")
        candidates.append(root / "results" / "metrics.json")
    for path in candidates:
        if not path.is_file():
            continue
        payload = _read_json(path)
        block = payload.get("detection")
        if isinstance(block, dict) and {"synthetic", "germeval"} <= set(block):
            return {"synthetic": block["synthetic"], "germeval": block["germeval"]}
        if {"synthetic", "germeval"} <= set(payload):
            return {"synthetic": payload["synthetic"], "germeval": payload["germeval"]}
    raise FileNotFoundError(
        "no detection metrics found; T11 must land results/runs/<run-id>-detection-*/metrics.json"
    )


def build_results(
    *,
    root: Path,
    run_dir: Path,
    triage_metrics: Mapping[str, Any],
    detection: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the page results document from the triage run and detection block."""
    record = _read_json(run_dir / "run.json")
    body = {
        "detection": {
            "synthetic": detection["synthetic"],
            "germeval": detection["germeval"],
        },
        "triage": {"per_backend": dict(triage_metrics.get("per_backend", {}))},
    }
    results = PageResults.model_validate(
        {
            **body,
            "meta": {
                "version": __version__,
                "commit": str(record.get("commit") or ""),
                "generated_at": utc_now(),
                "sha256": sha256_text(canonical_json(body)),
                "mock": False,
            },
        }
    )
    return results.model_dump(mode="json")


def export_page(
    *,
    root: Path,
    run_dir: Path,
    backend: str,
    detection_path: Path | None,
    output_dir: Path,
) -> tuple[Path, Path, list[str]]:
    """Write ``examples.json`` and ``results.json``; return their paths and notes."""
    record = _read_json(run_dir / "run.json")
    mode = str(record.get("mode", "test"))
    if mode != "test":
        raise ValueError("page data must come from a test run")
    subset = select_triage_subset(load_split(root / "data" / "synthetic" / "test.jsonl", "test"))
    metrics_path = run_dir / "metrics.json"
    if not metrics_path.is_file():
        raise FileNotFoundError("run has no metrics.json")
    triage_metrics = _read_json(metrics_path)
    detection = load_detection(root, detection_path)
    examples, notes = build_examples(subset, root=root, backend=backend)
    if len(examples) != EXAMPLES_COUNT:
        raise ValueError(
            f"page export produced {len(examples)} examples, expected {EXAMPLES_COUNT}"
        )
    results = build_results(
        root=root,
        run_dir=run_dir,
        triage_metrics=triage_metrics,
        detection=detection,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    examples_path = output_dir / "examples.json"
    results_path = output_dir / "results.json"
    _write_json(examples_path, examples)
    _write_json(results_path, results)
    return examples_path, results_path, notes


def example_ids(examples: Iterable[Mapping[str, Any]]) -> list[str]:
    """Return the ids in page order."""
    return [str(example.get("id")) for example in examples]


__all__ = [
    "build_examples",
    "build_results",
    "example_ids",
    "export_page",
    "load_detection",
    "select_examples",
    "uncovered_gold_spans",
]

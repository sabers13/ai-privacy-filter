"""Committed corpus loading and the fixed triage subset.

The selection rule is the one fixed in ``METHOD.md`` section 8.1: group test
records by category in the documented order, sort by id, take the 1-based
positions 1, 5, 9, 13, 17, 21, 25 and 29. ``tests/test_method.py`` pins the
result to the ids documented in the protocol.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Final

from ai_privacy_filter.models import Category, Enquiry

CATEGORY_ORDER: Final[tuple[Category, ...]] = (
    Category.QUOTE_REQUEST,
    Category.SUPPORT,
    Category.JOB_APPLICATION,
    Category.PARTNERSHIP,
    Category.OTHER,
)
SELECTED_POSITIONS: Final[tuple[int, ...]] = (1, 5, 9, 13, 17, 21, 25, 29)
SUBSET_SIZE: Final = 40
RUBRIC_POSITIONS: Final[tuple[int, ...]] = tuple(range(0, SUBSET_SIZE, 4))


def sha256_file(path: Path) -> str:
    """Return the SHA-256 of the raw file bytes."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_text(text: str) -> str:
    """Return the SHA-256 of the UTF-8 encoding of ``text``."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_split(path: Path, split: str) -> list[Enquiry]:
    """Load one JSONL split into validated :class:`Enquiry` records."""
    enquiries: list[Enquiry] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            enquiries.append(Enquiry.model_validate_json(line))
    for enquiry in enquiries:
        if enquiry.split != split:
            raise ValueError(f"record {enquiry.id} is not in the {split} split")
    return enquiries


def select_triage_subset(enquiries: Iterable[Enquiry]) -> list[Enquiry]:
    """Return the fixed 40 enquiries in evaluation order."""
    rows = list(enquiries)
    selected: list[Enquiry] = []
    for category in CATEGORY_ORDER:
        category_rows = sorted(
            (row for row in rows if row.gold_category is category),
            key=lambda row: row.id,
        )
        for position in SELECTED_POSITIONS:
            selected.append(category_rows[position - 1])
    if len(selected) != SUBSET_SIZE:
        raise ValueError(f"triage subset has {len(selected)} records, expected {SUBSET_SIZE}")
    return selected


def rubric_sample(subset: Sequence[Enquiry]) -> list[Enquiry]:
    """Return positions 1, 5, 9, ..., 37 (0-based 0, 4, ..., 36)."""
    return [subset[position] for position in RUBRIC_POSITIONS]


def subset_ids(subset: Sequence[Enquiry]) -> list[str]:
    """Return the ids in evaluation order."""
    return [enquiry.id for enquiry in subset]


def canonical_json(payload: object) -> str:
    """Serialise deterministically for hashing."""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

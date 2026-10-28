"""Tests for the seeded synthetic enquiry corpus."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

from ai_privacy_filter.models import Category, Enquiry, EntityType, Span, Urgency

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "data" / "make_synthetic.py"
DATA_DIR = REPO_ROOT / "data" / "synthetic"
SEED = 20261027

# Per-type minimums for the test split.
TEST_MINIMUMS: dict[EntityType, int] = {
    EntityType.PERSON: 100,
    EntityType.EMAIL: 60,
    EntityType.PHONE: 60,
    EntityType.ADDRESS_STREET: 60,
    EntityType.POSTCODE_CITY: 60,
    EntityType.IBAN: 40,
    EntityType.ORG: 60,
}

HIGH_CUES = (
    "dringend",
    "spätestens",
    "frist",
    "endet am freitag",
    "endet am montag",
    "endet am 15. november",
    "endet morgen",
    "läuft morgen ab",
)
LOW_CUES = ("eilt nicht", "keine eile", "kein stress", "unverbindlich")


def _generate(out_dir: Path, seed: int) -> None:
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--seed", str(seed), "--out-dir", str(out_dir)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def _load(path: Path) -> list[Enquiry]:
    return [
        Enquiry.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


@pytest.fixture(scope="module")
def committed() -> dict[str, list[Enquiry]]:
    return {
        "dev": _load(DATA_DIR / "dev.jsonl"),
        "test": _load(DATA_DIR / "test.jsonl"),
    }


def test_same_seed_writes_byte_identical_files(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    _generate(first, SEED)
    _generate(second, SEED)

    for name in ("dev.jsonl", "test.jsonl", "README.md"):
        assert (first / name).read_bytes() == (second / name).read_bytes(), name


def test_committed_files_regenerate_from_documented_seed(tmp_path: Path) -> None:
    readme = (DATA_DIR / "README.md").read_text(encoding="utf-8")
    match = re.search(r"^- Seed: (\d+)$", readme, re.MULTILINE)
    assert match is not None, "README must record the generation seed"
    seed = int(match.group(1))

    regenerated = tmp_path / "regenerated"
    _generate(regenerated, seed)
    for name in ("dev.jsonl", "test.jsonl"):
        assert (DATA_DIR / name).read_bytes() == (regenerated / name).read_bytes(), name


def test_splits_have_expected_sizes_and_unique_ids(
    committed: dict[str, list[Enquiry]],
) -> None:
    assert len(committed["dev"]) == 80
    assert len(committed["test"]) == 160

    for split, records in committed.items():
        assert {record.split for record in records} == {split}
        assert len({record.id for record in records}) == len(records)
        assert {record.style for record in records} == {
            "formal_letter",
            "web_form",
            "email_signature",
            "forwarded_email",
            "informal_du",
            "mixed_people_company",
            "hard_cases",
        }


def test_all_gold_spans_slice_the_text_exactly(
    committed: dict[str, list[Enquiry]],
) -> None:
    for records in committed.values():
        for record in records:
            assert record.gold_spans, record.id
            spans: list[Span] = record.gold_spans
            starts = [span.start for span in spans]
            assert starts == sorted(starts), record.id
            previous_end = 0
            for span in spans:
                assert record.text[span.start : span.end] == span.text, record.id
                assert span.start < span.end
                assert span.start >= previous_end, f"{record.id}: overlapping spans"
                previous_end = span.end


def test_test_split_meets_per_type_minimums(
    committed: dict[str, list[Enquiry]],
) -> None:
    counts = Counter(span.type for record in committed["test"] for span in record.gold_spans)
    for entity_type, minimum in TEST_MINIMUMS.items():
        assert counts[entity_type] >= minimum, (entity_type, counts[entity_type])


def test_categories_styles_and_urgencies_are_stratified(
    committed: dict[str, list[Enquiry]],
) -> None:
    for split, records in committed.items():
        categories = Counter(record.gold_category for record in records)
        styles = Counter(record.style for record in records)
        urgencies = Counter(record.gold_urgency for record in records)

        assert set(categories) == set(Category)
        assert max(categories.values()) - min(categories.values()) <= 1
        assert max(styles.values()) - min(styles.values()) <= 1
        assert set(urgencies) == {Urgency.LOW, Urgency.NORMAL, Urgency.HIGH}
        assert urgencies[Urgency.NORMAL] > urgencies[Urgency.HIGH]

        combinations = {(record.gold_category, record.style) for record in records}
        assert len(combinations) == len(Category) * 7, split


def test_urgency_labels_follow_explicit_cues(
    committed: dict[str, list[Enquiry]],
) -> None:
    for records in committed.values():
        for record in records:
            lowered = record.text.lower()
            if record.gold_urgency is Urgency.HIGH:
                assert any(cue in lowered for cue in HIGH_CUES), record.id
            elif record.gold_urgency is Urgency.LOW:
                assert any(cue in lowered for cue in LOW_CUES), record.id
            else:
                assert not any(cue in lowered for cue in HIGH_CUES), record.id
                assert not any(cue in lowered for cue in LOW_CUES), record.id


def test_records_validate_through_the_contract_model() -> None:
    """The same check the reader uses: one valid Enquiry per JSON line."""
    for split in ("dev", "test"):
        text = (DATA_DIR / f"{split}.jsonl").read_text(encoding="utf-8")
        payloads = [json.loads(line) for line in text.splitlines() if line.strip()]
        assert all(Enquiry.model_validate(payload).split == split for payload in payloads)

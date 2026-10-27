"""METHOD.md is pinned to the committed corpus and the fixed triage subset."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
METHOD = REPO_ROOT / "METHOD.md"
DATA_DIR = REPO_ROOT / "data" / "synthetic"

CATEGORY_ORDER = (
    "QUOTE_REQUEST",
    "SUPPORT",
    "JOB_APPLICATION",
    "PARTNERSHIP",
    "OTHER",
)
SELECTED_POSITIONS = (1, 5, 9, 13, 17, 21, 25, 29)
SUBSET_SIZE = 40


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rows(split: str) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in (DATA_DIR / f"{split}.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _expected_ids(rows: list[dict[str, object]]) -> list[str]:
    selected: list[str] = []
    for category in CATEGORY_ORDER:
        category_rows = sorted(
            (row for row in rows if row["gold_category"] == category),
            key=lambda row: str(row["id"]),
        )
        selected.extend(str(category_rows[position - 1]["id"]) for position in SELECTED_POSITIONS)
    return selected


def _section(text: str, start: str, end: str) -> str:
    begin = text.index(start)
    finish = text.index(end, begin)
    return text[begin:finish]


def _documented_ids(text: str) -> list[str]:
    section = _section(text, "### 8.2 Subset ids", "### 8.3 Manual rubric sample")
    return re.findall(r"syn-test-\d{4}", section)


def _documented_rubric_ids(text: str) -> list[str]:
    section = _section(text, "### 8.3 Manual rubric sample", "## 9. Statistics")
    return re.findall(r"syn-test-\d{4}", section)


def test_method_exists_and_names_the_protocol_gates() -> None:
    text = METHOD.read_text(encoding="utf-8")
    for required in (
        "## 5. Test run, method hash and reruns",
        "## 8. Fixed triage subset (40 enquiries)",
        "scripts/method_guard.py",
        "--rerun-reason",
        "--method-approval",
        "method_sha256",
        "Wilson",
        "B0",
        "B1",
        "B2",
        "LOCATION",
        "pseudonymises",
    ):
        assert required in text


def test_documented_subset_is_exactly_the_fixed_rule() -> None:
    text = METHOD.read_text(encoding="utf-8")
    documented = _documented_ids(text)
    expected = _expected_ids(_rows("test"))

    assert len(documented) == SUBSET_SIZE
    assert len(set(documented)) == SUBSET_SIZE
    assert documented == expected


def test_subset_is_stratified_and_comes_from_test_only() -> None:
    rows = {str(row["id"]): row for row in _rows("test")}
    dev_ids = {str(row["id"]) for row in _rows("dev")}
    documented = _documented_ids(METHOD.read_text(encoding="utf-8"))

    assert all(identifier in rows for identifier in documented)
    assert not (set(documented) & dev_ids)
    assert all(rows[identifier]["split"] == "test" for identifier in documented)

    categories = Counter(str(rows[identifier]["gold_category"]) for identifier in documented)
    assert categories == Counter({category: 8 for category in CATEGORY_ORDER})

    styles = Counter(str(rows[identifier]["style"]) for identifier in documented)
    assert len(styles) == 7
    assert min(styles.values()) >= 5


def test_rubric_sample_is_every_fourth_subset_record() -> None:
    documented = _documented_rubric_ids(METHOD.read_text(encoding="utf-8"))
    expected = _documented_ids(METHOD.read_text(encoding="utf-8"))[0::4]
    assert documented == expected
    assert len(documented) == 10


def test_corpus_hashes_are_pinned_to_the_committed_files() -> None:
    text = METHOD.read_text(encoding="utf-8")
    assert _sha256(DATA_DIR / "dev.jsonl") in text
    assert _sha256(DATA_DIR / "test.jsonl") in text


def test_documented_test_span_counts_match_the_corpus() -> None:
    text = METHOD.read_text(encoding="utf-8")
    counts: Counter[str] = Counter()
    for row in _rows("test"):
        for span in row["gold_spans"]:
            counts[str(span["type"])] += 1
    for entity_type, count in counts.items():
        assert f"| {entity_type} | {count} |" in text
    for absent in ("URL", "VAT_ID", "DATE_OF_BIRTH"):
        assert f"| {absent} | 0 |" in text

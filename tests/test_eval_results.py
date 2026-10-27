"""The committed detection results are internally consistent and text-free.

``SUMMARY.md`` is parsed back and every number is compared with the JSON it was
generated from. This is the test the task brief asks for: the summary cannot
drift from the data. GermEval text must not appear anywhere under ``results/``.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from ai_privacy_filter.models import EntityType
from eval import data
from eval.stats import format_interval, format_percent

REPO_ROOT = Path(__file__).resolve().parents[1]
RESULTS = REPO_ROOT / "results"
SUMMARY = RESULTS / "SUMMARY.md"
SYNTHETIC = RESULTS / "detection_synthetic.json"
GERMEVAL = RESULTS / "detection_germeval.json"
LEAK = RESULTS / "leak_rate.json"
RESTORE = RESULTS / "restore.json"
SHA256SUMS = RESULTS / "SHA256SUMS"
BASELINES = ("B0", "B1", "B2")


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _summary_lines() -> list[str]:
    return SUMMARY.read_text(encoding="utf-8").splitlines()


def _table(marker: str) -> list[list[str]]:
    lines = _summary_lines()
    assert marker in lines, f"marker missing from SUMMARY.md: {marker}"
    index = lines.index(marker)
    position = index + 1
    while position < len(lines) and not lines[position].strip():
        position += 1
    if position >= len(lines) or not lines[position].startswith("|"):
        return []
    rows: list[list[str]] = []
    for line in lines[position + 2 :]:
        if not line.startswith("|"):
            break
        rows.append([cell.strip() for cell in line.strip("|").split("|")])
    return rows


def _int(cell: str) -> int:
    return int(cell)


def test_method_hash_file_matches_the_protocol() -> None:
    recorded = (RESULTS / "METHOD_HASH").read_text(encoding="utf-8").strip()
    assert recorded == hashlib.sha256((REPO_ROOT / "METHOD.md").read_bytes()).hexdigest()


def test_config_was_frozen_under_the_current_method() -> None:
    config = _json(REPO_ROOT / "eval" / "configs.json")
    assert config["profile"] in {"balanced", "strict"}
    assert config["method_sha256"] == (RESULTS / "METHOD_HASH").read_text(encoding="utf-8").strip()


def test_sha256sums_cover_every_result_file() -> None:
    entries = {}
    for line in SHA256SUMS.read_text(encoding="utf-8").splitlines():
        digest, _, name = line.partition("  ")
        entries[name] = digest
    for name in (
        "METHOD_HASH",
        "SUMMARY.md",
        "detection_synthetic.json",
        "detection_germeval.json",
        "leak_rate.json",
        "restore.json",
    ):
        assert name in entries
        assert entries[name] == hashlib.sha256((RESULTS / name).read_bytes()).hexdigest()


def test_result_files_share_one_provenance_block() -> None:
    provenance = _json(SYNTHETIC)["provenance"]
    for path in (GERMEVAL, LEAK, RESTORE):
        assert _json(path)["provenance"] == provenance
    assert provenance["b2_profile"] in {"balanced", "strict"}
    assert provenance["synthetic_test_sha256"] == data.synthetic_hash("test")
    assert provenance["germeval_manifest_sha256"] == data.germeval_manifest_sha256()
    recorded = (RESULTS / "METHOD_HASH").read_text(encoding="utf-8").strip()
    assert provenance["method_sha256"] == recorded
    assert (
        provenance["config_sha256"]
        == hashlib.sha256((REPO_ROOT / "eval" / "configs.json").read_bytes()).hexdigest()
    )


def test_synthetic_counts_match_the_frozen_protocol() -> None:
    payload = _json(SYNTHETIC)
    assert payload["records"] == 160
    assert payload["gold_spans"] == 1074
    assert payload["dataset"] == "synthetic_test"
    for baseline in BASELINES:
        assert baseline in payload["baselines"]


def test_germeval_counts_match_the_published_split() -> None:
    payload = _json(GERMEVAL)
    assert payload["sentences"] == 5100
    assert payload["gold"]["PERSON"] + payload["gold"]["ORG"] == 2789
    assert payload["dataset"] == "germeval_test"


def test_summary_synthetic_recall_matches_json() -> None:
    payload = _json(SYNTHETIC)
    for baseline in BASELINES:
        rows = _table(f"<!-- table:synthetic-recall:{baseline} -->")
        assert len(rows) == len(EntityType)
        by_type = {row[0]: row for row in rows}
        for entity in EntityType:
            entry = payload["baselines"][baseline]["per_type"][entity.value]
            row = by_type[entity.value]
            assert _int(row[1]) == entry["gold"]
            assert _int(row[2]) == entry["protected"]
            assert _int(row[5]) == entry["strict_protected"]
            assert _int(row[7]) == entry["partial"]
            assert _int(row[8]) == entry["missed"]
            if entry["gold"]:
                assert row[3] == format_percent(entry["recall_pct"])
                assert row[4] == format_interval(entry["recall_wilson_pct"])
                assert row[6] == format_percent(entry["strict_recall_pct"])
            else:
                assert row[3] == "not estimable"


def test_summary_synthetic_precision_matches_json() -> None:
    payload = _json(SYNTHETIC)
    for baseline in BASELINES:
        rows = _table(f"<!-- table:synthetic-precision:{baseline} -->")
        by_type = {row[0]: row for row in rows}
        for entity in EntityType:
            entry = payload["baselines"][baseline]["per_type"][entity.value]
            row = by_type[entity.value]
            assert _int(row[1]) == entry["predicted"]
            assert _int(row[2]) == entry["correct"]
            assert _int(row[5]) == entry["exact_correct"]
            if entry["predicted"]:
                assert row[3] == format_percent(entry["precision_pct"])
                assert row[4] == format_interval(entry["precision_wilson_pct"])
                assert row[6] == format_percent(entry["exact_precision_pct"])
            else:
                assert row[3] == "n/a (0 predictions)"


def test_summary_germeval_matches_json() -> None:
    payload = _json(GERMEVAL)
    for baseline in BASELINES:
        rows = _table(f"<!-- table:germeval:{baseline} -->")
        by_type = {row[0]: row for row in rows}
        for entity in ("PERSON", "ORG"):
            entry = payload["baselines"][baseline]["per_type"][entity]
            row = by_type[entity]
            assert _int(row[1]) == entry["gold"]
            assert _int(row[2]) == entry["protected"]
            assert row[3] == format_percent(entry["recall_pct"])
            assert row[4] == format_interval(entry["recall_wilson_pct"])
            assert row[6] == format_percent(entry["strict_recall_pct"])
            assert row[7] == format_percent(entry["precision_pct"])
            assert row[8] == format_percent(entry["exact_precision_pct"])
        micro = payload["baselines"][baseline]["micro_primary"]
        row = by_type["PERSON+ORG (micro)"]
        assert _int(row[1]) == micro["gold"]
        assert _int(row[2]) == micro["protected"]
        assert row[3] == format_percent(micro["recall_pct"])
        location = payload["baselines"][baseline]["location_incidental"]
        location_rows = _table(f"<!-- table:germeval-location:{baseline} -->")
        assert _int(location_rows[0][0]) == location["gold"]
        assert _int(location_rows[0][1]) == location["covered"]
        assert location_rows[0][2] == format_percent(location["coverage_pct"])


def test_summary_leak_table_matches_json() -> None:
    payload = _json(LEAK)
    rows = {row[0]: row for row in _table("<!-- table:leak -->")}
    for baseline in BASELINES:
        entry = payload["baselines"][baseline]
        row = rows[baseline]
        residual = entry["residual_gold_chars"]
        assert _int(row[1]) == entry["leaked_enquiries"]
        assert row[2] == format_percent(entry["leak_rate_pct"])
        assert row[3] == format_interval(entry["leak_wilson_pct"])
        assert _int(row[4]) == entry["missed_gold_spans"]
        assert _int(row[5]) == entry["guard_blocks"]
        assert _int(row[6]) == residual["uncovered"]
        assert row[7] == format_percent(residual["rate_pct"])
        assert row[8] == format_interval(residual["bootstrap_pct"])


def test_summary_overmask_table_matches_json() -> None:
    payload = _json(SYNTHETIC)
    rows = {row[0]: row for row in _table("<!-- table:overmask -->")}
    for baseline in BASELINES:
        entry = payload["baselines"][baseline]["over_masking"]
        row = rows[baseline]
        assert _int(row[1]) == entry["enquiries_with_extra"]
        assert row[2] == format_percent(entry["enquiry_rate_pct"])
        assert row[3] == format_interval(entry["enquiry_wilson_pct"])
        assert _int(row[4]) == entry["extra_chars"]
        assert _int(row[5]) == entry["replaced_chars"]
        assert row[6] == format_percent(entry["character_rate_pct"])
        assert row[7] == format_interval(entry["character_bootstrap_pct"])
        assert _int(row[8]) == entry["false_spans"]
        assert _int(row[9]) == entry["predictions"]


def test_summary_restore_table_matches_json() -> None:
    payload = _json(RESTORE)
    row = _table("<!-- table:restore -->")[0]
    assert _int(row[0]) == payload["exact_round_trips"]
    assert _int(row[1]) == payload["records"]
    assert row[2] == format_percent(payload["exact_pct"])
    assert row[3] == format_interval(payload["exact_wilson_pct"])
    assert _int(row[4]) == len(payload["failures"])


def test_ten_miss_patterns_with_synthetic_examples_only() -> None:
    payload = _json(SYNTHETIC)
    rows = payload["miss_patterns"]
    assert len(rows) <= 10
    assert [row["rank"] for row in rows] == list(range(1, len(rows) + 1))
    sizes = [row["count"] for row in rows]
    assert sizes == sorted(sizes, reverse=True)
    for row in rows:
        assert row["type"] in {entity.value for entity in EntityType}
        assert row["example_id"].startswith("syn-test-")
        assert "«" in row["example"] and "»" in row["example"]
    summary_rows = _table("<!-- table:miss-patterns -->")
    assert len(summary_rows) == len(rows)

    if len(rows) < 10:
        # B2 did not miss on this corpus, so the ten-pattern deliverable is
        # satisfied by B1's patterns, explicitly labelled as baseline misses.
        fallback = payload["miss_patterns_b1"]
        assert len(fallback) == 10
        assert all(row["example_id"].startswith("syn-test-") for row in fallback)
        assert "B1's ten most common miss patterns" in SUMMARY.read_text(encoding="utf-8")
        assert len(_table("<!-- table:miss-patterns-b1 -->")) == 10


def test_germeval_misses_are_categories_only() -> None:
    payload = _json(GERMEVAL)
    rows = payload["miss_categories"]
    assert rows
    for row in rows:
        assert set(row) == {"gold_type", "category", "count"}
        assert row["gold_type"] in {"PERSON", "ORG"}
        assert row["count"] > 0


def test_results_contain_no_germeval_text() -> None:
    parsed = data.GERMEVAL_DATA_DIR / "parsed" / "test.jsonl"
    if not parsed.is_file():
        pytest.skip("GermEval test split is not downloaded in this checkout")
    sentences = [
        json.loads(line)["text"]
        for line in parsed.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    haystacks = {
        path.name: path.read_text(encoding="utf-8")
        for path in (SYNTHETIC, GERMEVAL, LEAK, RESTORE, SUMMARY)
    }
    for sentence in sentences:
        if len(sentence) >= 20:
            for name, haystack in haystacks.items():
                assert sentence not in haystack, f"GermEval sentence text found in {name}"
        tokens = sentence.split()
        for start in range(0, len(tokens) - 7):
            shingle = " ".join(tokens[start : start + 8])
            for name, haystack in haystacks.items():
                assert shingle not in haystack, f"GermEval token run found in {name}"


def test_summary_uses_the_required_wording() -> None:
    text = SUMMARY.read_text(encoding="utf-8")
    assert "pseudonymises" in text or "Pseudonymisation" in text
    assert "not legal advice" in text
    for forbidden in ("GDPR-compliant", "anonymised", "anonymized", "fully offline"):
        assert forbidden not in text

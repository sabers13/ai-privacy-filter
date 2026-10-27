"""Miss-pattern analysis and winner captions."""

from __future__ import annotations

from ai_privacy_filter.models import Category, Enquiry, EntityType, Span, Urgency
from eval import report, scoring


def _enquiry(identifier: str, text: str, spans: list[Span]) -> Enquiry:
    return Enquiry(
        id=identifier,
        split="test",
        style="hard_cases",
        text=text,
        gold_spans=spans,
        gold_category=Category.OTHER,
        gold_urgency=Urgency.LOW,
    )


def _gold(start: int, end: int, entity: EntityType, text: str) -> Span:
    return Span(start=start, end=end, type=entity, text=text, score=1.0, source="gold")


def test_miss_patterns_classify_person_org_and_boundary_cases() -> None:
    person = "Dr. Reza Nette B.Eng."
    org = "Nordlicht & Partner GmbH"
    text = f"{person} und {org}, Kennung AB"
    real = _enquiry(
        "syn-test-0001",
        text,
        [
            _gold(0, len(person), EntityType.PERSON, person),
            _gold(text.index(org), text.index(org) + len(org), EntityType.ORG, org),
        ],
    )
    score = scoring.score_record(real.text, list(real.gold_spans), [])
    rows = report.miss_patterns([real], [{"id": real.id, "score": score}])
    patterns = {row["pattern"] for row in rows}
    assert "person-with-title-or-suffix" in patterns
    assert "org-with-ampersand" in patterns
    assert all(row["example_id"].startswith("syn-test-") for row in rows)
    assert all("«" in row["example"] for row in rows)


def test_partial_coverage_gets_a_boundary_pattern() -> None:
    text = "Max Müller"
    gold = [_gold(0, 10, EntityType.PERSON, text)]
    enquiry = _enquiry("syn-test-0002", text, gold)
    predictions = [
        Span(start=0, end=3, type=EntityType.PERSON, text="Max", score=0.9, source="test")
    ]
    score = scoring.score_record(text, gold, predictions)
    rows = report.miss_patterns([enquiry], [{"id": enquiry.id, "score": score}])
    assert rows[0]["pattern"] == "partial-boundary-person"
    assert rows[0]["type"] == "PERSON"


def test_germeval_categories_are_counts_without_text() -> None:
    text = "x"
    gold = _gold(0, 1, EntityType.PERSON, text)
    missed = scoring.GoldOutcome(
        entity_type=EntityType.PERSON,
        start=0,
        end=1,
        status="missed",
        strict_status="missed",
        uncovered_chars=1,
        overlap_types=(),
    )
    partial = scoring.GoldOutcome(
        entity_type=EntityType.ORG,
        start=0,
        end=1,
        status="partial",
        strict_status="partial",
        uncovered_chars=1,
        overlap_types=("ORG",),
    )
    score = scoring.RecordScore(
        gold=(missed, partial),
        predictions=(),
        gold_chars=1,
        uncovered_gold_chars=1,
        replaced_chars=0,
        extra_chars=0,
    )
    rows = report.germeval_miss_categories([{"id": "germeval-00001", "score": score}])
    assert {row["category"] for row in rows} == {
        "no overlapping prediction",
        "partial coverage, same type, boundary differs",
    }
    assert all(set(row) == {"gold_type", "category", "count"} for row in rows)
    assert gold.text == "x"


def test_winner_line_names_a_simpler_baseline_and_reports_ties() -> None:
    baselines = {
        "B0": {"per_type": {"PERSON": {"recall_pct": 0.0}}},
        "B1": {"per_type": {"PERSON": {"recall_pct": 90.0}}},
        "B2": {"per_type": {"PERSON": {"recall_pct": 80.0}}},
    }
    line = report._winner_line(baselines, "PERSON", metric="recall")
    assert "simpler baseline B1 wins" in line
    assert "10.0 points" in line

    baselines["B2"]["per_type"]["PERSON"]["recall_pct"] = 90.0
    tied = report._winner_line(baselines, "PERSON", metric="recall")
    assert "B2 ties B1" in tied

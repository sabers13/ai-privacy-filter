"""Unit tests for the one span-matching function."""

from __future__ import annotations

from ai_privacy_filter.models import EntityType, Span
from eval import scoring


def span(start: int, end: int, entity: EntityType, text: str = "x", *, score: float = 0.9) -> Span:
    return Span(start=start, end=end, type=entity, text=text, score=score, source="test")


def test_compatibility_is_exactly_the_documented_table() -> None:
    assert scoring.compatible(EntityType.PERSON, EntityType.ORG)
    assert scoring.compatible(EntityType.ORG, EntityType.PERSON)
    assert scoring.compatible(EntityType.EMAIL, EntityType.URL)
    assert scoring.compatible(EntityType.URL, EntityType.EMAIL)
    assert not scoring.compatible(EntityType.PERSON, EntityType.EMAIL)
    assert not scoring.compatible(EntityType.PHONE, EntityType.IBAN)
    assert scoring.compatible(EntityType.IBAN, EntityType.IBAN)


def test_adjacent_predictions_can_together_protect_one_gold_span() -> None:
    text = "Max Müller"
    gold = [span(0, 10, EntityType.PERSON, text)]
    predictions = [span(0, 3, EntityType.PERSON), span(3, 10, EntityType.PERSON)]
    result = scoring.score_record(text, gold, predictions)
    assert result.gold[0].status == "protected"
    assert result.gold[0].uncovered_chars == 0


def test_partial_and_missed_are_distinguished() -> None:
    text = "Max Müller wohnt hier"
    gold = [span(0, 10, EntityType.PERSON, "Max Müller")]
    partial = scoring.score_record(text, gold, [span(0, 3, EntityType.PERSON)])
    missed = scoring.score_record(text, gold, [span(11, 15, EntityType.ORG)])
    assert partial.gold[0].status == "partial"
    assert partial.gold[0].overlap_types == ("PERSON",)
    assert missed.gold[0].status == "missed"
    assert missed.gold[0].overlap_types == ()


def test_overlong_prediction_is_not_correct_but_counts_as_extra() -> None:
    text = "Max Müller schreibt"
    gold = [span(0, 10, EntityType.PERSON, "Max Müller")]
    predictions = [span(0, 19, EntityType.PERSON)]
    result = scoring.score_record(text, gold, predictions)
    assert result.gold[0].status == "protected"
    assert result.predictions[0].correct is False
    assert result.predictions[0].exact is False
    assert result.extra_chars > 0


def test_exact_boundary_is_tracked_separately_from_containment() -> None:
    text = "Max Müller"
    gold = [span(0, 10, EntityType.PERSON)]
    contained = scoring.score_record(text, gold, [span(0, 3, EntityType.PERSON)])
    exact = scoring.score_record(text, gold, [span(0, 10, EntityType.PERSON)])
    assert contained.predictions[0].correct is True
    assert contained.predictions[0].exact is False
    assert exact.predictions[0].correct is True
    assert exact.predictions[0].exact is True


def test_compatible_type_counts_for_coverage_and_precision() -> None:
    text = "Max Müller GmbH"
    gold = [span(0, 15, EntityType.ORG)]
    predictions = [span(0, 10, EntityType.PERSON)]
    result = scoring.score_record(text, gold, predictions)
    assert result.gold[0].status == "partial"
    assert result.predictions[0].correct is True
    assert result.gold[0].strict_status == "missed"


def test_duplicates_collapse_and_unusable_predictions_are_counted() -> None:
    text = "Max Müller"
    duplicates = [
        span(0, 10, EntityType.PERSON, score=0.4),
        span(0, 10, EntityType.PERSON, score=0.9),
        span(0, 0, EntityType.PERSON),
        span(3, 3, EntityType.PERSON),
        span(0, 99, EntityType.PERSON),
    ]
    kept, ignored = scoring.clean_predictions(text, duplicates)
    assert len(kept) == 1
    assert kept[0].score == 0.9
    assert ignored == 3


def test_whitespace_only_prediction_is_ignored() -> None:
    text = "Max   Müller"
    kept, ignored = scoring.clean_predictions(text, [span(3, 6, EntityType.PERSON)])
    assert kept == []
    assert ignored == 1


def test_aggregate_totals_match_record_scores() -> None:
    text = "Max Müller, max@example.org"
    gold = [
        span(0, 10, EntityType.PERSON, "Max Müller"),
        span(12, 27, EntityType.EMAIL, "max@example.org"),
    ]
    predictions = [
        span(0, 10, EntityType.PERSON),
        span(12, 27, EntityType.EMAIL),
        span(0, 5, EntityType.PERSON),
    ]
    totals = scoring.CorpusTotals()
    totals.add(text, gold, predictions)
    assert totals.by_type[EntityType.PERSON].gold == 1
    assert totals.by_type[EntityType.PERSON].protected == 1
    assert totals.by_type[EntityType.EMAIL].protected == 1
    assert totals.predictions_total == 3
    assert totals.false_spans == 0
    assert totals.extra_chars == 0


def test_fully_covered_accepts_adjacent_predictions() -> None:
    predictions = [span(0, 3, EntityType.PERSON), span(3, 10, EntityType.PERSON)]
    assert scoring.fully_covered(0, 10, predictions) is True
    assert scoring.fully_covered(0, 11, predictions) is False

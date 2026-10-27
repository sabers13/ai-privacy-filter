"""The one span-matching function used for every baseline and dataset.

``METHOD.md`` section 6 fixes these rules; synthetic and GermEval data go
through the same code, with no per-dataset hand adjustment.

* gold and predicted spans are half-open character ranges;
* a gold span is protected when every character is inside at least one
  prediction of a compatible type (several adjacent predictions may combine);
* a prediction is correct when its whole range is inside a compatible gold
  span, so an over-long prediction is over-masking and is not correct;
* exact duplicates are collapsed, other overlaps are kept for coverage and
  counted separately for precision.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ai_privacy_filter.models import EntityType, Span

#: METHOD.md section 6.3. ``_COMPATIBLE[gold]`` lists the predicted types that
#: protect it. The relation is symmetric for the pairs that differ.
_COMPATIBLE: dict[EntityType, frozenset[EntityType]] = {
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


def compatible(gold_type: EntityType, predicted_type: EntityType) -> bool:
    """Return whether ``predicted_type`` protects a gold span of ``gold_type``."""

    return predicted_type in _COMPATIBLE[gold_type]


@dataclass(frozen=True, slots=True)
class GoldOutcome:
    """Coverage outcome for one gold span occurrence."""

    entity_type: EntityType
    start: int
    end: int
    status: str  # "protected", "partial" or "missed"
    strict_status: str
    uncovered_chars: int
    overlap_types: tuple[str, ...] = ()

    @property
    def length(self) -> int:
        return self.end - self.start


@dataclass(frozen=True, slots=True)
class PredictionOutcome:
    """Correctness of one predicted span for containment and exact precision."""

    entity_type: EntityType
    start: int
    end: int
    correct: bool
    exact: bool


@dataclass(frozen=True, slots=True)
class RecordScore:
    """Everything one record contributes to the aggregate counters."""

    gold: tuple[GoldOutcome, ...]
    predictions: tuple[PredictionOutcome, ...]
    gold_chars: int
    uncovered_gold_chars: int
    replaced_chars: int
    extra_chars: int


def clean_predictions(text: str, spans: list[Span]) -> tuple[list[Span], int]:
    """Drop unusable predictions and collapse exact duplicates.

    Returns ``(kept, ignored)``. Zero-length, whitespace-only and out-of-range
    spans are ignored and counted. For identical ``(start, end, type)`` the
    highest score wins, then the lexicographically smallest source, so the
    choice is deterministic.
    """

    limit = len(text)
    ignored = 0
    unique: dict[tuple[int, int, EntityType], Span] = {}
    for span in spans:
        if span.start < 0 or span.end > limit or span.start >= span.end:
            ignored += 1
            continue
        if not text[span.start : span.end].strip():
            ignored += 1
            continue
        key = (span.start, span.end, span.type)
        current = unique.get(key)
        if current is None or (-span.score, span.source) < (-current.score, current.source):
            unique[key] = span
    kept = sorted(unique.values(), key=lambda span: (span.start, span.end, span.type.value))
    return kept, ignored


def _coverage_mask(mask: bytearray, start: int, end: int) -> None:
    for position in range(max(0, start), min(end, len(mask))):
        mask[position] = 1


def _covered_mask(
    gold: Span,
    predictions: list[Span],
    *,
    strict: bool,
) -> bytearray:
    """Return a mask of the gold positions covered by compatible predictions.

    The mask has one byte per character of the record; positions outside the
    gold span are always zero, so the caller can count the span directly.
    """

    mask = bytearray(gold.end - gold.start)
    for prediction in predictions:
        if strict:
            if prediction.type is not gold.type:
                continue
        elif not compatible(gold.type, prediction.type):
            continue
        overlap_start = max(gold.start, prediction.start) - gold.start
        overlap_end = min(gold.end, prediction.end) - gold.start
        if overlap_start < overlap_end:
            _coverage_mask(mask, overlap_start, overlap_end)
    return mask


def score_record(text: str, gold: list[Span], predictions: list[Span]) -> RecordScore:
    """Score one record under the METHOD.md matching rules."""

    for span in gold:
        if span.start < 0 or span.end > len(text) or span.start >= span.end:
            raise ValueError("gold span is empty or outside the text")
    kept, _ = clean_predictions(text, predictions)
    length = len(text)

    gold_mask = bytearray(length)
    for span in gold:
        _coverage_mask(gold_mask, span.start, span.end)
    gold_chars = gold_mask.count(1)

    prediction_mask = bytearray(length)
    for span in kept:
        _coverage_mask(prediction_mask, span.start, span.end)
    replaced_chars = prediction_mask.count(1)
    extra_chars = sum(
        1 for position in range(length) if prediction_mask[position] and not gold_mask[position]
    )

    uncovered_mask = bytearray(length)
    outcomes: list[GoldOutcome] = []
    for span in gold:
        span_length = span.end - span.start
        coverage = _covered_mask(span, kept, strict=False)
        strict_coverage = _covered_mask(span, kept, strict=True)
        covered = coverage.count(1)
        strict_covered = strict_coverage.count(1)
        if span_length == 0:
            status = "missed"
        elif covered == span_length:
            status = "protected"
        elif covered == 0:
            status = "missed"
        else:
            status = "partial"
        if span_length == 0:
            strict_status = "missed"
        elif strict_covered == span_length:
            strict_status = "protected"
        elif strict_covered == 0:
            strict_status = "missed"
        else:
            strict_status = "partial"
        for offset in range(span_length):
            if not coverage[offset]:
                uncovered_mask[span.start + offset] = 1
        outcomes.append(
            GoldOutcome(
                entity_type=span.type,
                start=span.start,
                end=span.end,
                status=status,
                strict_status=strict_status,
                uncovered_chars=span_length - covered,
                overlap_types=tuple(
                    sorted(
                        {
                            prediction.type.value
                            for prediction in kept
                            if span.start < prediction.end and prediction.start < span.end
                        }
                    )
                ),
            )
        )

    predictions_out: list[PredictionOutcome] = []
    for span in kept:
        correct = any(
            compatible(gold_span.type, span.type)
            and gold_span.start <= span.start
            and span.end <= gold_span.end
            for gold_span in gold
        )
        exact = any(
            compatible(gold_span.type, span.type)
            and gold_span.start == span.start
            and span.end == gold_span.end
            for gold_span in gold
        )
        predictions_out.append(
            PredictionOutcome(
                entity_type=span.type,
                start=span.start,
                end=span.end,
                correct=correct,
                exact=exact,
            )
        )

    return RecordScore(
        gold=tuple(outcomes),
        predictions=tuple(predictions_out),
        gold_chars=gold_chars,
        uncovered_gold_chars=uncovered_mask.count(1),
        replaced_chars=replaced_chars,
        extra_chars=extra_chars,
    )


@dataclass(slots=True)
class TypeTotals:
    """Aggregate counters for one entity type."""

    gold: int = 0
    protected: int = 0
    partial: int = 0
    missed: int = 0
    strict_protected: int = 0
    strict_partial: int = 0
    strict_missed: int = 0
    predicted: int = 0
    correct: int = 0
    exact: int = 0
    gold_chars: int = 0
    uncovered_gold_chars: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "gold": self.gold,
            "protected": self.protected,
            "partial": self.partial,
            "missed": self.missed,
            "strict_protected": self.strict_protected,
            "strict_partial": self.strict_partial,
            "strict_missed": self.strict_missed,
            "predicted": self.predicted,
            "correct": self.correct,
            "exact": self.exact,
            "gold_chars": self.gold_chars,
            "uncovered_gold_chars": self.uncovered_gold_chars,
        }


@dataclass(slots=True)
class CorpusTotals:
    """Counters for a whole corpus and one baseline."""

    records: int = 0
    by_type: dict[EntityType, TypeTotals] = field(
        default_factory=lambda: {entity: TypeTotals() for entity in EntityType}
    )
    ignored_predictions: int = 0
    predictions_total: int = 0
    false_spans: int = 0
    enquiries_with_extra: int = 0
    gold_chars: int = 0
    uncovered_gold_chars: int = 0
    replaced_chars: int = 0
    extra_chars: int = 0
    #: Per-record ``(extra_characters, replaced_characters)`` for the bootstrap.
    overmask_clusters: list[tuple[int, int]] = field(default_factory=list)
    #: Per-record ``(uncovered_gold_characters, gold_characters)``.
    residual_clusters: list[tuple[int, int]] = field(default_factory=list)
    #: Per-record extra characters by predicted type.
    extra_by_predicted_type: dict[EntityType, int] = field(
        default_factory=lambda: {entity: 0 for entity in EntityType}
    )

    def add(
        self,
        text: str,
        gold: list[Span],
        predictions: list[Span],
        *,
        gold_types: tuple[EntityType, ...] | None = None,
        char_gold: list[tuple[int, int]] | None = None,
    ) -> RecordScore:
        """Score one record and fold it into the counters.

        ``char_gold`` replaces the span types used for the character-level
        over-masking denominator. GermEval passes the union of PER, ORG, LOC and
        OTH outer spans so its secondary over-masking number is not distorted by
        classes the filter does not target.
        """

        kept, ignored = clean_predictions(text, predictions)
        score = score_record(text, gold, kept)
        self.records += 1
        self.ignored_predictions += ignored
        wanted = set(gold_types) if gold_types is not None else set(EntityType)

        for outcome in score.gold:
            if outcome.entity_type not in wanted:
                continue
            totals = self.by_type[outcome.entity_type]
            totals.gold += 1
            totals.gold_chars += outcome.length
            totals.uncovered_gold_chars += outcome.uncovered_chars
            if outcome.status == "protected":
                totals.protected += 1
            elif outcome.status == "partial":
                totals.partial += 1
            else:
                totals.missed += 1
            if outcome.strict_status == "protected":
                totals.strict_protected += 1
            elif outcome.strict_status == "partial":
                totals.strict_partial += 1
            else:
                totals.strict_missed += 1

        for prediction in score.predictions:
            totals = self.by_type[prediction.entity_type]
            totals.predicted += 1
            if prediction.correct:
                totals.correct += 1
            if prediction.exact:
                totals.exact += 1

        length = len(text)
        ranges = char_gold if char_gold is not None else [(span.start, span.end) for span in gold]
        gold_mask = bytearray(length)
        for start, end in ranges:
            _coverage_mask(gold_mask, start, end)
        prediction_mask = bytearray(length)
        for span in kept:
            _coverage_mask(prediction_mask, span.start, span.end)
        record_gold_chars = gold_mask.count(1)
        record_replaced = prediction_mask.count(1)
        record_extra = sum(
            1 for position in range(length) if prediction_mask[position] and not gold_mask[position]
        )

        self.gold_chars += record_gold_chars
        self.replaced_chars += record_replaced
        self.extra_chars += record_extra
        if record_extra:
            self.enquiries_with_extra += 1
        self.overmask_clusters.append((record_extra, record_replaced))
        # The residual character metric always uses the primary gold spans.
        self.uncovered_gold_chars += score.uncovered_gold_chars
        self.residual_clusters.append((score.uncovered_gold_chars, score.gold_chars))

        for prediction in kept:
            self.predictions_total += 1
            inside = any(
                gold_mask[position] for position in range(prediction.start, prediction.end)
            )
            if not inside:
                self.false_spans += 1
            extra = sum(
                1 for position in range(prediction.start, prediction.end) if not gold_mask[position]
            )
            self.extra_by_predicted_type[prediction.type] += extra
        return score


def type_totals_dict(totals: CorpusTotals) -> dict[str, dict[str, int]]:
    """Return the raw counters per type, with no percentages."""

    return {entity.value: totals.by_type[entity].to_dict() for entity in EntityType}


def fully_covered(start: int, end: int, predictions: list[Span]) -> bool:
    """Return whether ``[start, end)`` is fully inside the union of predictions."""

    if start >= end:
        return False
    mask = bytearray(end - start)
    for prediction in predictions:
        overlap_start = max(start, prediction.start) - start
        overlap_end = min(end, prediction.end) - start
        if overlap_start < overlap_end:
            _coverage_mask(mask, overlap_start, overlap_end)
    return mask.count(1) == end - start

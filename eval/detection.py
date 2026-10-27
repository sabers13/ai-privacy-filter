"""Detection evaluation harness: B0, B1 and B2 on synthetic test and GermEval.

Run ``python -m eval.detection --stage detection`` for the one permitted test
run. ``make reproduce`` recomputes every result file from the committed corpus
and the verified GermEval download and compares it byte for byte.

The harness follows ``METHOD.md``:

1. check the method hash and the corpus hashes before anything is read;
2. call ``scripts/method_guard.py`` so a test access is claimed and rerun rules
   apply;
3. write ``run.json`` and the rerun row before reading a test record;
4. score B0, B1 and B2 with :mod:`eval.scoring`;
5. write deterministic result files plus the run record.

No result, log line or terminal output contains GermEval text or an absolute
private path.
"""

from __future__ import annotations

import argparse
import contextlib
import gc
import hashlib
import json
import os
import socket
import subprocess
import sys
from collections import Counter
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from ai_privacy_filter.datasets.germeval import GermEvalClass, GermEvalSentence
from ai_privacy_filter.guard import GuardVerdict, guard
from ai_privacy_filter.models import Enquiry, EntityType, Span
from ai_privacy_filter.pseudonymise import normalise_value, redact, restore
from eval import baselines, data, report, scoring
from eval.stats import (
    BOOTSTRAP_RESAMPLES,
    BOOTSTRAP_SEED,
    bootstrap_ratio,
    percent,
    percent_interval,
    wilson,
)

STAGE: Final = "detection"
SCHEMA_VERSION: Final = 1
TLDEXTRACT_DIR: Final = data.GERMEVAL_DATA_DIR.parent / "tldextract"
DETECTION_SYNTHETIC = data.RESULTS_DIR / "detection_synthetic.json"
DETECTION_GERMEVAL = data.RESULTS_DIR / "detection_germeval.json"
LEAK_RATE = data.RESULTS_DIR / "leak_rate.json"
RESTORE = data.RESULTS_DIR / "restore.json"
SUMMARY = data.RESULTS_DIR / "SUMMARY.md"
SHA256SUMS = data.RESULTS_DIR / "SHA256SUMS"
TUNING_DIR = data.RESULTS_DIR / "tuning"

DETECTION_FILES: Final[tuple[Path, ...]] = (
    DETECTION_SYNTHETIC,
    DETECTION_GERMEVAL,
    LEAK_RATE,
    RESTORE,
    SUMMARY,
    data.METHOD_HASH_PATH,
)

#: Presidio names requested from the default recognizer registry (B1). The four
#: contract types with no default Presidio recognizer are requested by their
#: contract name and simply produce nothing.
_B1_ENTITIES: Final[tuple[str, ...]] = (
    "PERSON",
    "ORGANIZATION",
    "EMAIL_ADDRESS",
    "PHONE_NUMBER",
    "IBAN_CODE",
    "URL",
    "ADDRESS_STREET",
    "POSTCODE_CITY",
    "VAT_ID",
    "DATE_OF_BIRTH",
)

_PRESIDIO_TO_ENTITY: Final[dict[str, EntityType]] = {
    "PERSON": EntityType.PERSON,
    "ORGANIZATION": EntityType.ORG,
    "ORG": EntityType.ORG,
    "EMAIL_ADDRESS": EntityType.EMAIL,
    "EMAIL": EntityType.EMAIL,
    "PHONE_NUMBER": EntityType.PHONE,
    "PHONE": EntityType.PHONE,
    "IBAN_CODE": EntityType.IBAN,
    "IBAN": EntityType.IBAN,
    "URL": EntityType.URL,
    "ADDRESS_STREET": EntityType.ADDRESS_STREET,
    "POSTCODE_CITY": EntityType.POSTCODE_CITY,
    "VAT_ID": EntityType.VAT_ID,
    "DATE_OF_BIRTH": EntityType.DATE_OF_BIRTH,
}

PROMPT_PREFIX: Final = (
    "Du bist ein Assistent für ein deutsches Unternehmen. Ordne die folgende Anfrage "
    "einer Kategorie und einer Dringlichkeit zu und entwirf eine kurze Antwort. "
    'Antworte ausschließlich mit JSON: {"category": "...", "urgency": "...", '
    '"reply_draft": "..."}.\n\nAnfrage:\n'
)
PROMPT_SUFFIX: Final = "\n\nAntwort (JSON):"

#: The prompt envelope is constant and contains no data. Leak audit therefore
#: depends only on the pseudonymised message body, which is exactly what leaves
#: the machine. T12 owns the final triage wording; the envelope hash below makes
#: any change visible.
PROMPT_ENVELOPE_SHA256: Final = hashlib.sha256(
    (PROMPT_PREFIX + PROMPT_SUFFIX).encode("utf-8")
).hexdigest()

Detector = Callable[[str], list[Span]]


@dataclass(frozen=True, slots=True)
class Baseline:
    """One system under test."""

    key: str
    name: str
    description: str
    detector: Detector


@dataclass(slots=True)
class SyntheticScore:
    """Per-record outcomes kept for the miss-pattern analysis and scoring files."""

    enquiries: list[Enquiry]
    scores: list[scoring.RecordScore] = field(default_factory=list)


@dataclass(slots=True)
class Bundle:
    """Everything one evaluation stage produces."""

    synthetic: dict[str, Any]
    germeval: dict[str, Any]
    leak: dict[str, Any]
    restore: dict[str, Any]
    scoring_files: dict[str, Any]


# ---------------------------------------------------------------------------
# Environment and detector construction
# ---------------------------------------------------------------------------


def configure_tldextract() -> dict[str, str]:
    """Warm the public-suffix cache once and hash the snapshot.

    Presidio's default email validation calls ``tldextract``. The cache is
    prepared before the run so no enquiry can trigger a network call. The
    snapshot hash is recorded; a later run with different suffix data must not
    silently reuse the numbers.
    """

    cache_dir = TLDEXTRACT_DIR
    cache_dir.mkdir(parents=True, exist_ok=True)
    os.environ["TLDEXTRACT_CACHE"] = str(cache_dir)
    import tldextract

    extractor = tldextract.TLDExtract(cache_dir=str(cache_dir))
    # Presidio's EmailRecognizer calls the module-level ``tldextract.extract``.
    # Point it at the pinned cache before the first enquiry so the recorded
    # snapshot is exactly the one the run used and no enquiry can fetch it.
    tldextract.extract = extractor  # type: ignore[assignment]
    extractor("example.org")
    snapshots = sorted((cache_dir / "publicsuffix.org-tlds").glob("*.tldextract.json"))
    if not snapshots:
        raise RuntimeError("tldextract: no cached public-suffix snapshot after warm-up")
    snapshot = snapshots[-1]
    return {
        "tldextract_version": getattr(tldextract, "__version__", "unknown"),
        "tldextract_cache_sha256": data.sha256_file(snapshot),
        "tldextract_cache_file": snapshot.name,
    }


class PresidioDefaults:
    """B1: Presidio as shipped, German spaCy pipeline, no custom recognizers."""

    def __init__(self) -> None:
        from presidio_analyzer import AnalyzerEngine
        from presidio_analyzer.nlp_engine import NlpEngineProvider

        configuration = {
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": "de", "model_name": "de_core_news_lg"}],
            "ner_model_configuration": {"labels_to_ignore": ["MISC"]},
        }
        nlp_engine = NlpEngineProvider(nlp_configuration=configuration).create_engine()
        self._analyzer = AnalyzerEngine(
            nlp_engine=nlp_engine,
            supported_languages=["de"],
            default_score_threshold=0.0,
        )

    def __call__(self, text: str) -> list[Span]:
        results = self._analyzer.analyze(
            text=text,
            language="de",
            entities=list(_B1_ENTITIES),
            score_threshold=0.0,
        )
        spans: list[Span] = []
        for result in results:
            entity = _PRESIDIO_TO_ENTITY.get(str(result.entity_type))
            if entity is None:
                continue
            if result.start < 0 or result.end > len(text) or result.start >= result.end:
                continue
            spans.append(
                Span(
                    start=result.start,
                    end=result.end,
                    type=entity,
                    text=text[result.start : result.end],
                    score=max(0.0, min(1.0, float(result.score))),
                    source="presidio",
                )
            )
        return spans


def build_baseline(key: str, profile: str) -> Baseline:
    """Build one system under test.

    B1 and B2 each load a 500 MB German pipeline, so the caller constructs them
    one at a time and releases the previous one; ``run_evaluation`` does that.
    """

    if key == "B0":
        return Baseline(
            key="B0",
            name="B0 — regex-only",
            description=(
                "The page try-it rule set: fixed patterns, no model, no name list and "
                "no PERSON or ORG rule."
            ),
            detector=baselines.detect_b0,
        )
    if key == "B1":
        return Baseline(
            key="B1",
            name="B1 — Presidio defaults + German spaCy",
            description=(
                "Presidio's default recognizer registry with the pinned German pipeline, "
                "no custom recognizers, no cleanup and no merge step."
            ),
            detector=PresidioDefaults(),
        )
    if key != "B2":
        raise ValueError(f"unknown baseline: {key}")

    from ai_privacy_filter.detect import detect

    return Baseline(
        key="B2",
        name="B2 — full configuration",
        description=(
            f"Product detector: German NER, Presidio email/URL, every German "
            f"recognizer, the shared merge rule and the frozen `{profile}` profile."
        ),
        detector=lambda text: list(detect(text, profile=profile)),  # type: ignore[arg-type]
    )


def build_baselines(profile: str) -> list[Baseline]:
    """Return B0, B1 and B2 in report order (used by tests and small checks)."""

    return [build_baseline(key, profile) for key in ("B0", "B1", "B2")]


@contextlib.contextmanager
def no_network() -> Iterator[None]:
    """Fail instead of reaching the network while scoring.

    This is the per-run proof for METHOD.md §3.2: the tldextract cache is warm,
    so a network attempt during detection is a bug, not a slow path.
    """

    original_connect = socket.socket.connect

    def refuse(self: socket.socket, address: object) -> None:
        raise RuntimeError(f"network access during detection is not allowed: {address!r}")

    socket.socket.connect = refuse  # type: ignore[method-assign]
    try:
        yield
    finally:
        socket.socket.connect = original_connect  # type: ignore[method-assign]


# ---------------------------------------------------------------------------
# Leak audit, restore and per-record scoring
# ---------------------------------------------------------------------------


def outbound_prompt(redacted_text: str) -> str:
    """Return the exact prompt string a backend would receive."""

    return PROMPT_PREFIX + redacted_text + PROMPT_SUFFIX


def _digits_only(value: str) -> str:
    return "".join(character for character in value if character in "0123456789")


def _value_occurs(value: str, entity_type: EntityType, haystack: str, digits: str) -> bool:
    needle = normalise_value(value)
    if needle and needle in haystack:
        return True
    if entity_type in (EntityType.PHONE, EntityType.IBAN):
        digits_only = _digits_only(value)
        return len(digits_only) >= 4 and digits_only in digits
    return False


def audit_leak(
    enquiries: Sequence[Enquiry], detector: Detector
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Audit the outbound prompt against gold values for one baseline."""

    leaked = 0
    leaked_types: Counter[str] = Counter()
    guard_blocks = 0
    guard_block_types: Counter[str] = Counter()
    missed_spans = 0
    partial_spans = 0
    residual_clusters: list[tuple[int, int]] = []
    records: list[dict[str, Any]] = []

    for enquiry in enquiries:
        predictions = detector(enquiry.text)
        redaction = redact(enquiry.text, predictions)
        verdict: GuardVerdict = guard(redaction.redacted_text, redaction.mapping)
        prompt = outbound_prompt(redaction.redacted_text)
        haystack = normalise_value(prompt)
        digits = _digits_only(prompt)

        case_types: list[str] = []
        for gold_span in enquiry.gold_spans:
            if _value_occurs(gold_span.text, gold_span.type, haystack, digits):
                case_types.append(gold_span.type.value)
        if case_types:
            leaked += 1
            leaked_types.update(case_types)
        if not verdict.ok:
            guard_blocks += 1
            guard_block_types.update(member.value for member in verdict.leaked_types)

        score = scoring.score_record(enquiry.text, list(enquiry.gold_spans), predictions)
        missed = sum(1 for outcome in score.gold if outcome.status == "missed")
        partial = sum(1 for outcome in score.gold if outcome.status == "partial")
        missed_spans += missed
        partial_spans += partial
        residual_clusters.append((score.uncovered_gold_chars, score.gold_chars))
        records.append(
            {
                "id": enquiry.id,
                "leaked": bool(case_types),
                "leaked_types": sorted(set(case_types)),
                "guard_blocked": not verdict.ok,
                "guard_block_types": sorted({member.value for member in verdict.leaked_types}),
                "missed_gold_spans": missed,
                "partial_gold_spans": partial,
            }
        )

    total = len(enquiries)
    low, high = wilson(leaked, total)
    residual_low, residual_high, skipped = bootstrap_ratio(residual_clusters)
    gold_chars = sum(cluster[1] for cluster in residual_clusters)
    uncovered = sum(cluster[0] for cluster in residual_clusters)
    payload = {
        "records": total,
        "leaked_enquiries": leaked,
        "leak_rate_pct": percent(leaked / total),
        "leak_wilson_pct": percent_interval(low, high),
        "leaked_types": dict(sorted(leaked_types.items())),
        "missed_gold_spans": missed_spans,
        "partial_gold_spans": partial_spans,
        "guard_blocks": guard_blocks,
        "guard_block_types": dict(sorted(guard_block_types.items())),
        "residual_gold_chars": {
            "uncovered": uncovered,
            "total": gold_chars,
            "rate_pct": percent(uncovered / gold_chars) if gold_chars else None,
            "bootstrap_pct": percent_interval(residual_low, residual_high) if gold_chars else None,
            "bootstrap_skipped": skipped,
            "bootstrap_resamples": BOOTSTRAP_RESAMPLES,
            "bootstrap_seed": BOOTSTRAP_SEED,
        },
        "prompt_envelope_sha256": PROMPT_ENVELOPE_SHA256,
    }
    return payload, {"records": records}


def restore_round_trip(
    enquiries: Sequence[Enquiry], detector: Detector
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Run ``restore(redact(text))`` and check byte-for-byte equality."""

    exact = 0
    failures: list[dict[str, str]] = []
    records: list[dict[str, Any]] = []
    for enquiry in enquiries:
        redaction = redact(enquiry.text, detector(enquiry.text))
        restored = restore(redaction.redacted_text, redaction.mapping)
        is_exact = restored.text == enquiry.text
        reason = None
        if not is_exact:
            groups: dict[str, set[str]] = {}
            for span in redaction.spans:
                groups.setdefault(normalise_value(enquiry.text[span.start : span.end]), set()).add(
                    enquiry.text[span.start : span.end]
                )
            if any(len(values) > 1 for values in groups.values()):
                reason = "normalisation variant"
            elif restored.unknown_placeholders:
                reason = "literal placeholder in source"
            else:
                reason = "other"
            failures.append({"id": enquiry.id, "reason": reason})
        else:
            exact += 1
        records.append({"id": enquiry.id, "exact": is_exact, "reason": reason})

    total = len(enquiries)
    low, high = wilson(exact, total)
    payload = {
        "records": total,
        "exact_round_trips": exact,
        "exact_pct": percent(exact / total),
        "exact_wilson_pct": percent_interval(low, high),
        "failures": failures,
        "draft_restoration": {
            "status": "not measured",
            "reason": "the fixed 40-enquiry backend run lands with T12",
        },
    }
    return payload, records


def _gold_spans_for(enquiry: Enquiry) -> list[Span]:
    return list(enquiry.gold_spans)


def score_synthetic(
    enquiries: Sequence[Enquiry], baseline: Baseline
) -> tuple[scoring.CorpusTotals, list[dict[str, Any]]]:
    """Score all synthetic test enquiries for one baseline."""

    totals = scoring.CorpusTotals()
    outcomes: list[dict[str, Any]] = []
    for enquiry in enquiries:
        score = totals.add(enquiry.text, _gold_spans_for(enquiry), baseline.detector(enquiry.text))
        outcomes.append({"id": enquiry.id, "score": score})
    return totals, outcomes


def _germeval_primary_spans(sentence: GermEvalSentence) -> list[Span]:
    spans: list[Span] = []
    for span in sentence.spans:
        if span.label in (GermEvalClass.PERSON, GermEvalClass.ORG) and span.entity_type is not None:
            spans.append(
                Span(
                    start=span.start,
                    end=span.end,
                    type=span.entity_type,
                    text=sentence.text[span.start : span.end],
                    score=1.0,
                    source="gold",
                )
            )
    return spans


def _germeval_char_gold(sentence: GermEvalSentence) -> list[tuple[int, int]]:
    wanted = {
        GermEvalClass.PERSON,
        GermEvalClass.ORG,
        GermEvalClass.LOCATION,
        GermEvalClass.OTHER,
    }
    return [(span.start, span.end) for span in sentence.spans if span.label in wanted]


def score_germeval(
    sentences: Sequence[GermEvalSentence],
    baseline: Baseline,
    *,
    keep_records: bool = False,
) -> tuple[scoring.CorpusTotals, dict[str, int], list[dict[str, Any]]]:
    """Score GermEval test: PERSON and ORG primary, LOCATION incidental only."""

    totals = scoring.CorpusTotals()
    location_gold = 0
    location_covered = 0
    outcomes: list[dict[str, Any]] = []
    for sentence in sentences:
        gold = _germeval_primary_spans(sentence)
        predictions = baseline.detector(sentence.text)
        score = totals.add(
            sentence.text,
            gold,
            predictions,
            gold_types=(EntityType.PERSON, EntityType.ORG),
            char_gold=_germeval_char_gold(sentence),
        )
        for span in sentence.spans:
            if span.label is GermEvalClass.LOCATION:
                location_gold += 1
                if scoring.fully_covered(span.start, span.end, predictions):
                    location_covered += 1
        if keep_records:
            outcomes.append({"id": sentence.id, "score": score})
    location = {
        "gold": location_gold,
        "incidental_covered": location_covered,
    }
    return totals, location, outcomes


# ---------------------------------------------------------------------------
# Payload builders
# ---------------------------------------------------------------------------


def _per_type_payload(
    totals: scoring.CorpusTotals,
    *,
    special_notes: dict[EntityType, str] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for entity in EntityType:
        counts = totals.by_type[entity]
        notes: list[str] = []
        if entity in (special_notes or {}):
            notes.append((special_notes or {})[entity])
        entry: dict[str, Any] = {
            "gold": counts.gold,
            "protected": counts.protected,
            "partial": counts.partial,
            "missed": counts.missed,
            "strict_protected": counts.strict_protected,
            "strict_partial": counts.strict_partial,
            "strict_missed": counts.strict_missed,
            "recall_pct": None,
            "recall_wilson_pct": None,
            "strict_recall_pct": None,
            "strict_recall_wilson_pct": None,
            "predicted": counts.predicted,
            "correct": counts.correct,
            "precision_pct": None,
            "precision_wilson_pct": None,
            "exact_correct": counts.exact,
            "exact_precision_pct": None,
            "exact_precision_wilson_pct": None,
        }
        if counts.gold:
            low, high = wilson(counts.protected, counts.gold)
            entry["recall_pct"] = percent(counts.protected / counts.gold)
            entry["recall_wilson_pct"] = percent_interval(low, high)
            strict_low, strict_high = wilson(counts.strict_protected, counts.gold)
            entry["strict_recall_pct"] = percent(counts.strict_protected / counts.gold)
            entry["strict_recall_wilson_pct"] = percent_interval(strict_low, strict_high)
        else:
            notes.append("not estimable (no gold spans)")
        if counts.predicted:
            low, high = wilson(counts.correct, counts.predicted)
            entry["precision_pct"] = percent(counts.correct / counts.predicted)
            entry["precision_wilson_pct"] = percent_interval(low, high)
            exact_low, exact_high = wilson(counts.exact, counts.predicted)
            entry["exact_precision_pct"] = percent(counts.exact / counts.predicted)
            entry["exact_precision_wilson_pct"] = percent_interval(exact_low, exact_high)
        else:
            notes.append("n/a (0 predictions)")
        entry["note"] = "; ".join(notes) if notes else None
        payload[entity.value] = entry
    return payload


def _baseline_payload(
    baseline: Baseline,
    totals: scoring.CorpusTotals,
    *,
    gold_types: Sequence[EntityType],
    special_notes: dict[EntityType, str] | None = None,
) -> dict[str, Any]:
    return {
        "key": baseline.key,
        "name": baseline.name,
        "description": baseline.description,
        "records": totals.records,
        "gold_spans": sum(totals.by_type[entity].gold for entity in gold_types),
        "predicted_spans": totals.predictions_total,
        "ignored_predictions": totals.ignored_predictions,
        "per_type": _per_type_payload(totals, special_notes=special_notes),
    }


def _overmask_payload(totals: scoring.CorpusTotals) -> dict[str, Any]:
    low, high = wilson(totals.enquiries_with_extra, totals.records)
    char_rate = (
        percent(totals.extra_chars / totals.replaced_chars) if totals.replaced_chars else None
    )
    if totals.replaced_chars:
        boot_low, boot_high, skipped = bootstrap_ratio(totals.overmask_clusters)
        bootstrap = percent_interval(boot_low, boot_high)
    else:
        bootstrap = None
        skipped = 0
    false_low, false_high = (
        wilson(totals.false_spans, totals.predictions_total)
        if totals.predictions_total
        else (0.0, 0.0)
    )
    return {
        "records": totals.records,
        "enquiries_with_extra": totals.enquiries_with_extra,
        "enquiry_rate_pct": percent(totals.enquiries_with_extra / totals.records),
        "enquiry_wilson_pct": percent_interval(low, high),
        "extra_chars": totals.extra_chars,
        "replaced_chars": totals.replaced_chars,
        "character_rate_pct": char_rate,
        "character_bootstrap_pct": bootstrap,
        "bootstrap_skipped": skipped,
        "false_spans": totals.false_spans,
        "predictions": totals.predictions_total,
        "false_span_share_pct": percent(totals.false_spans / totals.predictions_total)
        if totals.predictions_total
        else None,
        "false_span_wilson_pct": percent_interval(false_low, false_high)
        if totals.predictions_total
        else None,
        "extra_by_type": {
            entity.value: count for entity, count in totals.extra_by_predicted_type.items() if count
        },
    }


def _micro_primary(totals: scoring.CorpusTotals) -> dict[str, Any]:
    gold = totals.by_type[EntityType.PERSON].gold + totals.by_type[EntityType.ORG].gold
    protected = (
        totals.by_type[EntityType.PERSON].protected + totals.by_type[EntityType.ORG].protected
    )
    strict = (
        totals.by_type[EntityType.PERSON].strict_protected
        + totals.by_type[EntityType.ORG].strict_protected
    )
    low, high = wilson(protected, gold)
    strict_low, strict_high = wilson(strict, gold)
    return {
        "gold": gold,
        "protected": protected,
        "recall_pct": percent(protected / gold),
        "recall_wilson_pct": percent_interval(low, high),
        "strict_protected": strict,
        "strict_recall_pct": percent(strict / gold),
        "strict_recall_wilson_pct": percent_interval(strict_low, strict_high),
    }


# ---------------------------------------------------------------------------
# The stage itself
# ---------------------------------------------------------------------------


def run_evaluation(
    profile: str, *, synthetic_split: str = "test", germeval_split: str = "test"
) -> Bundle:
    """Compute every detection, leak and restore number once.

    The split arguments exist for the dev dry run and for tests; the test stage
    always uses the defaults, which are the only final-number splits.
    """

    enquiries = data.load_synthetic(synthetic_split)
    sentences = data.load_germeval(germeval_split)

    with no_network():
        synthetic_baselines: dict[str, Any] = {}
        germeval_baselines: dict[str, Any] = {}
        leak_baselines: dict[str, Any] = {}
        per_record_leak: dict[str, Any] = {}
        synthetic_records: list[dict[str, Any]] = []
        b1_records: list[dict[str, Any]] = []
        germeval_records: list[dict[str, Any]] = []
        b2_detector: Detector | None = None

        for key in ("B0", "B1", "B2"):
            baseline = build_baseline(key, profile)
            synthetic_totals, records = score_synthetic(enquiries, baseline)
            special_notes: dict[EntityType, str] = {}
            if baseline.key == "B0":
                special_notes[EntityType.PERSON] = (
                    "0% by construction (B0 has no PERSON or ORG rule)"
                )
                special_notes[EntityType.ORG] = "0% by construction (B0 has no PERSON or ORG rule)"
            synthetic_entry = _baseline_payload(
                baseline,
                synthetic_totals,
                gold_types=data.SYNTHETIC_GOLD_TYPES,
                special_notes=special_notes,
            )
            synthetic_entry["over_masking"] = _overmask_payload(synthetic_totals)
            synthetic_baselines[baseline.key] = synthetic_entry
            if baseline.key == "B2":
                synthetic_records = records
            elif baseline.key == "B1":
                b1_records = records

            germeval_totals, location, germeval_outcomes = score_germeval(
                sentences, baseline, keep_records=baseline.key == "B2"
            )
            if baseline.key == "B2":
                germeval_records = germeval_outcomes
            germeval_entry = _baseline_payload(
                baseline,
                germeval_totals,
                gold_types=(EntityType.PERSON, EntityType.ORG),
            )
            germeval_entry["micro_primary"] = _micro_primary(germeval_totals)
            germeval_entry["over_masking_secondary"] = _overmask_payload(germeval_totals)
            germeval_entry["location_incidental"] = {
                "gold": location["gold"],
                "covered": location["incidental_covered"],
                "coverage_pct": percent(location["incidental_covered"] / location["gold"])
                if location["gold"]
                else None,
                "coverage_wilson_pct": percent_interval(
                    *wilson(location["incidental_covered"], location["gold"])
                )
                if location["gold"]
                else None,
                "note": (
                    "LOCATION is not a detection target; its class recall is 0% by "
                    "construction and it is never folded into personal-data recall"
                ),
            }
            germeval_baselines[baseline.key] = germeval_entry

            leak_payload, leak_records = audit_leak(enquiries, baseline.detector)
            leak_baselines[baseline.key] = leak_payload
            per_record_leak[baseline.key] = leak_records["records"]
            if baseline.key == "B2":
                b2_detector = baseline.detector
            del baseline
            if key == "B1":
                gc.collect()

        if b2_detector is None:  # pragma: no cover - B2 is always in the loop
            raise RuntimeError("B2 baseline was not built")
        restore_payload, restore_records = restore_round_trip(enquiries, b2_detector)

    b2_outcomes = [{"id": record["id"], "score": record["score"]} for record in synthetic_records]
    b1_outcomes = [{"id": record["id"], "score": record["score"]} for record in b1_records]
    miss_patterns = report.miss_patterns(enquiries, b2_outcomes)
    miss_patterns_b1 = report.miss_patterns(enquiries, b1_outcomes)
    germeval_miss_categories = report.germeval_miss_categories(germeval_records)

    environment = data.environment()
    provenance = data.provenance(
        {
            "b2_profile": profile,
            "network": "blocked during scoring (after data download and cache warm-up)",
            "prompt_envelope_sha256": PROMPT_ENVELOPE_SHA256,
        }
    )
    provenance["environment"] = environment
    provenance.update(_tldextract_provenance)

    synthetic_payload = {
        "schema_version": SCHEMA_VERSION,
        "dataset": f"synthetic_{synthetic_split}",
        "records": len(enquiries),
        "gold_spans": sum(len(enquiry.gold_spans) for enquiry in enquiries),
        "types": [entity.value for entity in EntityType],
        "provenance": provenance,
        "baselines": synthetic_baselines,
        "miss_patterns": miss_patterns,
        "miss_patterns_b1": miss_patterns_b1,
    }
    germeval_payload = {
        "schema_version": SCHEMA_VERSION,
        "dataset": f"germeval_{germeval_split}",
        "sentences": len(sentences),
        "gold": {
            "PERSON": sum(1 for s in sentences for span in s.spans if span.label.value == "PERSON"),
            "ORG": sum(1 for s in sentences for span in s.spans if span.label.value == "ORG"),
            "LOCATION": sum(
                1 for s in sentences for span in s.spans if span.label.value == "LOCATION"
            ),
            "OTHER": sum(1 for s in sentences for span in s.spans if span.label.value == "OTHER"),
        },
        "provenance": provenance,
        "baselines": germeval_baselines,
        "miss_categories": germeval_miss_categories,
    }
    leak_payload = {
        "schema_version": SCHEMA_VERSION,
        "dataset": f"synthetic_{synthetic_split}",
        "records": len(enquiries),
        "provenance": provenance,
        "baselines": leak_baselines,
    }
    restore_payload["schema_version"] = SCHEMA_VERSION
    restore_payload["dataset"] = f"synthetic_{synthetic_split}"
    restore_payload["provenance"] = provenance

    scoring_files = {
        "scoring/leak.json": {"baselines": per_record_leak},
        "scoring/restore.json": {"records": restore_records},
        "scoring/overmask.json": {
            "records": [
                {
                    "id": record["id"],
                    "extra_chars": record["score"].extra_chars,
                    "replaced_chars": record["score"].replaced_chars,
                    "gold_chars": record["score"].gold_chars,
                    "uncovered_gold_chars": record["score"].uncovered_gold_chars,
                }
                for record in synthetic_records
            ]
        },
        "scoring/misses.json": {"b2": miss_patterns, "b1": miss_patterns_b1},
    }

    return Bundle(
        synthetic=synthetic_payload,
        germeval=germeval_payload,
        leak=leak_payload,
        restore=restore_payload,
        scoring_files=scoring_files,
    )


_tldextract_provenance: dict[str, str] = {}


def _render_outputs(bundle: Bundle) -> dict[str, bytes]:
    """Render every deterministic artifact as bytes."""

    summary = report.render_summary(
        bundle.synthetic, bundle.germeval, bundle.leak, bundle.restore
    ).encode("utf-8")
    return {
        "detection_synthetic.json": data.canonical_json(bundle.synthetic),
        "detection_germeval.json": data.canonical_json(bundle.germeval),
        "leak_rate.json": data.canonical_json(bundle.leak),
        "restore.json": data.canonical_json(bundle.restore),
        "SUMMARY.md": summary,
    }


def _metrics_payload(bundle: Bundle) -> dict[str, Any]:
    return {
        "detection": {
            "synthetic": bundle.synthetic,
            "germeval": bundle.germeval,
        },
        "leak": bundle.leak,
        "restore": bundle.restore,
    }


def _write_sha256sums() -> None:
    lines = []
    for path in DETECTION_FILES:
        lines.append(f"{data.sha256_file(path)}  {path.name}")
    lines.sort(key=lambda line: line.split("  ", 1)[1])
    SHA256SUMS.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _git_short_commit() -> str:
    completed = subprocess.run(
        ["git", "-C", str(data.REPO_ROOT), "rev-parse", "--short=7", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if completed.returncode != 0:
        raise RuntimeError("cannot read the current git commit")
    return completed.stdout.strip()


def _call_method_guard(
    run_id: str,
    *,
    rerun_reason: str | None,
    method_approval: str | None,
) -> dict[str, Any]:
    command = [
        sys.executable,
        "scripts/method_guard.py",
        "--mode",
        "test",
        "--stage",
        STAGE,
        "--run-id",
        run_id,
        "--root",
        str(data.REPO_ROOT),
    ]
    if rerun_reason:
        command += ["--rerun-reason", rerun_reason]
    if method_approval:
        command += ["--method-approval", method_approval]
    completed = subprocess.run(
        command,
        cwd=data.REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    if completed.returncode != 0:
        message = completed.stderr.strip() or "method guard refused the run"
        raise GuardRefusal(completed.returncode, message)
    return json.loads(completed.stdout)


class GuardRefusal(RuntimeError):
    """The method guard refused the test access; carries its exit code."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


def _append_rerun_row(
    *,
    run_id: str,
    commit: str,
    method_sha256: str,
    decision: str,
    reason: str | None,
    approval: str | None,
) -> None:
    path = data.RERUNS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(
            "# Evaluation reruns\n\n"
            "Append-only record of test-split access, written by `eval/detection.py` "
            "before reading a test record (METHOD.md §5.4). The `finished_at` cell stays "
            "`-` in the row written before the run; the completion log below records the "
            "end time as a separate append, so no historical row is ever rewritten.\n\n"
            "| # | finished_at | run_id | stage | commit | method | changed_since | reason | "
            "test_seen_before_change | approval |\n"
            "|---|---|---|---|---|---|---|---|---|---|\n\n"
            "## Completion log\n",
            encoding="utf-8",
        )
    text = path.read_text(encoding="utf-8")
    rows = sum(1 for line in text.splitlines() if line.startswith("| ") and line[2:3].isdigit())
    if decision == "first-run":
        changed = "n/a (first run of this stage)"
        seen = "no"
    elif decision == "new-stage":
        changed = "n/a (new stage, same method)"
        seen = "no"
    else:
        changed = "declared in the reason"
        seen = "yes"
    row = (
        f"| {rows + 1} | - | {run_id} | {STAGE} | {commit[:7]} | {method_sha256[:12]} | "
        f"{changed} | {reason or 'first run'} | {seen} | {approval or '-'} |\n"
    )
    text = path.read_text(encoding="utf-8")
    marker = "\n## Completion log"
    if marker in text:
        head, tail = text.split(marker, 1)
        text = head.rstrip("\n") + "\n" + row + marker + tail
    else:  # a hand-written file without the completion section
        text = text.rstrip("\n") + "\n" + row
    path.write_text(text, encoding="utf-8")


def _append_completion(run_id: str, status: str) -> None:
    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    with data.RERUNS_PATH.open("a", encoding="utf-8") as handle:
        handle.write(f"- {run_id} finished at {stamp} ({status})\n")
        handle.flush()
        os.fsync(handle.fileno())


def _write_run_record(path: Path, payload: dict[str, Any], *, status: str | None = None) -> None:
    if status is not None:
        payload["status"] = status
    data.write_json(path, payload)


def run_test_stage(
    *,
    rerun_reason: str | None,
    method_approval: str | None,
    run_id: str | None,
) -> int:
    """Execute the one permitted detection test run."""

    global _tldextract_provenance
    _tldextract_provenance = configure_tldextract()

    current_method = data.method_sha256()
    recorded = data.read_method_hash_file()
    if recorded is not None and recorded != current_method and not rerun_reason:
        print(
            "refusing to run: METHOD.md changed since results/METHOD_HASH was written; "
            "pass --rerun-reason (and --method-approval) for a documented rerun",
            file=sys.stderr,
        )
        return 3

    data.check_synthetic_hashes()
    data.ensure_germeval()

    config = data.load_config()
    if config.method_sha256 != current_method:
        print(
            "refusing to run: eval/configs.json was frozen under a different METHOD.md; "
            "re-run the dev tuning stage first",
            file=sys.stderr,
        )
        return 2

    started_at = datetime.now(UTC)
    identifier = run_id or (
        started_at.strftime("%Y%m%dT%H%M%SZ") + f"-{STAGE}-" + _git_short_commit()
    )
    try:
        guard_result = _call_method_guard(
            identifier,
            rerun_reason=rerun_reason,
            method_approval=method_approval,
        )
    except GuardRefusal as refusal:
        print(refusal, file=sys.stderr)
        return refusal.code

    data.record_method_hash()
    run_dir = data.RUNS_DIR / identifier
    run_dir.mkdir(parents=True, exist_ok=True)
    run_path = run_dir / "run.json"
    guard_commit = str(guard_result.get("commit", ""))
    environment = data.environment()
    run_record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "run_id": identifier,
        "mode": "test",
        "stage": STAGE,
        "status": "running",
        "method_sha256": current_method,
        "method_git_blob": guard_result.get("method_git_blob"),
        "commit": guard_commit,
        "dirty": False,
        "config_sha256": data.config_sha256(),
        "data_sha256": data.data_hashes(),
        "rerun_reason": rerun_reason,
        "method_approval": method_approval,
        "supersedes": None,
        "started_at": started_at.isoformat().replace("+00:00", "Z"),
        "finished_at": None,
        "command": [
            "python",
            "-m",
            "eval.detection",
            "--stage",
            STAGE,
            "--run-id",
            identifier,
        ],
        "environment": environment,
        "artifacts_sha256": {},
    }
    data.write_json(run_path, run_record)
    with run_path.open("a", encoding="utf-8") as handle:
        handle.flush()
        os.fsync(handle.fileno())

    decision = str(guard_result.get("decision", "first-run"))
    _append_rerun_row(
        run_id=identifier,
        commit=guard_commit,
        method_sha256=current_method,
        decision=decision,
        reason=rerun_reason,
        approval=method_approval,
    )

    try:
        bundle = run_evaluation(config.profile)
    except BaseException:
        run_record["finished_at"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        _write_run_record(run_path, run_record, status="aborted")
        _append_completion(identifier, "aborted")
        raise

    outputs = _render_outputs(bundle)
    for name, payload_bytes in outputs.items():
        (data.RESULTS_DIR / name).write_bytes(payload_bytes)
    (run_dir / "metrics.json").write_bytes(data.canonical_json(_metrics_payload(bundle)))
    (run_dir / "report.md").write_bytes(outputs["SUMMARY.md"])
    for relative, payload in bundle.scoring_files.items():
        destination = run_dir / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data.canonical_json(payload))
    _write_sha256sums()

    artifact_paths = [
        *DETECTION_FILES,
        run_dir / "metrics.json",
        run_dir / "report.md",
        *sorted(run_dir.glob("scoring/*.json")),
    ]
    run_record["artifacts_sha256"] = {
        path.relative_to(data.REPO_ROOT).as_posix(): data.sha256_file(path)
        for path in artifact_paths
    }
    run_record["finished_at"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    _write_run_record(run_path, run_record, status="complete")
    _append_completion(identifier, "complete")

    b2_synthetic = bundle.synthetic["baselines"]["B2"]["per_type"]["PERSON"]
    b2_leak = bundle.leak["baselines"]["B2"]
    print(
        f"detection complete: run {identifier}; "
        f"synthetic B2 PERSON recall {b2_synthetic['recall_pct']}%, "
        f"leak {b2_leak['leaked_enquiries']}/{b2_leak['records']}"
    )
    return 0


# ---------------------------------------------------------------------------
# Tuning on dev only
# ---------------------------------------------------------------------------


def run_tuning() -> int:
    """Choose the B2 profile on synthetic dev + GermEval dev (METHOD §4.2)."""

    global _tldextract_provenance
    _tldextract_provenance = configure_tldextract()
    data.check_synthetic_hashes()
    data.ensure_germeval(("dev",))
    current_method = data.method_sha256()
    enquiries = data.load_synthetic("dev")
    sentences = data.load_germeval("dev")

    def nine_values(baseline: Baseline) -> tuple[dict[str, float], dict[str, Any]]:
        synthetic_totals, _ = score_synthetic(enquiries, baseline)
        germeval_totals, _, _ = score_germeval(sentences, baseline)
        recalls: dict[str, float] = {}
        precisions: list[float] = []
        for entity in data.SYNTHETIC_GOLD_TYPES:
            counts = synthetic_totals.by_type[entity]
            recalls[f"synthetic/{entity.value}"] = (
                counts.protected / counts.gold if counts.gold else 0.0
            )
            precisions.append(counts.correct / counts.predicted if counts.predicted else 0.0)
        for entity in (EntityType.PERSON, EntityType.ORG):
            counts = germeval_totals.by_type[entity]
            recalls[f"germeval/{entity.value}"] = (
                counts.protected / counts.gold if counts.gold else 0.0
            )
            precisions.append(counts.correct / counts.predicted if counts.predicted else 0.0)
        overmask = synthetic_totals.enquiries_with_extra / synthetic_totals.records
        return recalls, {
            "overmask_enquiry_rate": overmask,
            "macro_precision": sum(precisions) / len(precisions),
            "synthetic": synthetic_totals,
            "germeval": germeval_totals,
        }

    results: list[dict[str, Any]] = []
    with no_network():
        for profile in data.load_candidates():
            baseline = build_baseline("B2", profile)
            recalls, extras = nine_values(baseline)
            results.append(
                {
                    "profile": profile,
                    "dev_recall": {key: percent(value) for key, value in recalls.items()},
                    "r_min": min(recalls.values()),
                    "r_min_pct": percent(min(recalls.values())),
                    "overmask_enquiry_rate": extras["overmask_enquiry_rate"],
                    "macro_precision": extras["macro_precision"],
                }
            )

    def rank(item: dict[str, Any]) -> tuple[float, float, float, int]:
        profile = str(item["profile"])
        return (
            -float(item["r_min"]),
            float(item["overmask_enquiry_rate"]),
            -float(item["macro_precision"]),
            0 if profile == "balanced" else 1,
        )

    winner = sorted(results, key=rank)[0]
    ties = [
        item
        for item in results
        if item is not winner
        and item["r_min"] == winner["r_min"]
        and item["overmask_enquiry_rate"] == winner["overmask_enquiry_rate"]
        and item["macro_precision"] == winner["macro_precision"]
    ]

    payload = {
        "schema_version": SCHEMA_VERSION,
        "method_sha256": current_method,
        "data_sha256": data.data_hashes(),
        "selection_rule": (
            "highest minimum of the nine dev recalls; ties by lower enquiry-level "
            "over-masking, higher macro precision, then balanced before strict"
        ),
        "candidates": [
            {
                "profile": item["profile"],
                "dev_recall_pct": item["dev_recall"],
                "r_min_pct": item["r_min_pct"],
                "overmask_enquiry_pct": percent(float(item["overmask_enquiry_rate"])),
                "macro_precision_pct": percent(float(item["macro_precision"])),
            }
            for item in results
        ],
        "winner": {
            "profile": winner["profile"],
            "r_min_pct": winner["r_min_pct"],
            "within_noise_of": [item["profile"] for item in ties],
        },
    }
    TUNING_DIR.mkdir(parents=True, exist_ok=True)
    data.write_json(TUNING_DIR / "candidates.json", payload)

    lines = [
        "# B2 profile selection (dev only)",
        "",
        f"Protocol hash: `{current_method}`.",
        "",
        "The selection rule is pre-registered in METHOD.md §4.2: highest minimum of the "
        "nine dev recalls (seven synthetic types, GermEval PERSON and ORG); ties by lower "
        "enquiry-level over-masking, higher macro precision, then `balanced` before `strict`.",
        "",
        "| candidate | r_min | over-masking (enquiries) | macro precision |",
        "|---|---|---|---|",
    ]
    for item in results:
        lines.append(
            f"| {item['profile']} | {item['r_min_pct']:.1f}% | "
            f"{percent(float(item['overmask_enquiry_rate'])):.1f}% | "
            f"{percent(float(item['macro_precision'])):.1f}% |"
        )
    lines += [
        "",
        "Per type and split:",
        "",
        "| candidate | " + " | ".join(results[0]["dev_recall"].keys()) + " |",
        "|---|" + "---|" * len(results[0]["dev_recall"]),
    ]
    for item in results:
        lines.append(
            f"| {item['profile']} | "
            + " | ".join(f"{value:.1f}%" for value in item["dev_recall"].values())
            + " |"
        )
    lines += [
        "",
        f"**Winner: `{winner['profile']}`** (r_min {winner['r_min_pct']:.1f}%).",
    ]
    if ties:
        lines.append(
            "The difference to "
            + ", ".join(f"`{item['profile']}`" for item in ties)
            + " was within noise on all nine values; the simpler candidate wins by the "
            "pre-registered tie-break."
        )
    (TUNING_DIR / "selection.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    config = data.Config(profile=str(winner["profile"]), method_sha256=current_method)
    data.write_json(data.CONFIG_PATH, config.to_dict())
    print(
        f"tuning complete: chose {winner['profile']} (r_min {winner['r_min_pct']}%); "
        f"wrote {data.CONFIG_PATH.relative_to(data.REPO_ROOT).as_posix()}"
    )
    return 0


# ---------------------------------------------------------------------------
# Reproduce
# ---------------------------------------------------------------------------


def _latest_detection_run() -> Path | None:
    candidates: list[tuple[str, Path]] = []
    if not data.RUNS_DIR.exists():
        return None
    for path in data.RUNS_DIR.glob("*/run.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if payload.get("stage") != STAGE or payload.get("status") != "complete":
            continue
        candidates.append((str(payload.get("started_at", "")), path.parent))
    if not candidates:
        return None
    return sorted(candidates)[-1][1]


def run_rerender() -> int:
    """Re-render the report from the latest run's stored metrics.

    Report wording can change without touching a measurement. This reads only
    ``metrics.json`` (no test record), rewrites ``SUMMARY.md``, the run's
    ``report.md`` and ``SHA256SUMS``, and refreshes the run record's artifact
    hashes. ``make reproduce`` still has to pass afterwards.
    """

    run_dir = _latest_detection_run()
    if run_dir is None:
        print("rerender: no complete detection run found", file=sys.stderr)
        return 2
    metrics_path = run_dir / "metrics.json"
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    detection = metrics["detection"]
    summary = report.render_summary(
        detection["synthetic"], detection["germeval"], metrics["leak"], metrics["restore"]
    )
    (data.RESULTS_DIR / "SUMMARY.md").write_text(summary, encoding="utf-8")
    (run_dir / "report.md").write_text(summary, encoding="utf-8")
    _write_sha256sums()

    run_path = run_dir / "run.json"
    record = json.loads(run_path.read_text(encoding="utf-8"))
    artifacts = [
        *DETECTION_FILES,
        run_dir / "metrics.json",
        run_dir / "report.md",
        *sorted(run_dir.glob("scoring/*.json")),
    ]
    record["artifacts_sha256"] = {
        path.relative_to(data.REPO_ROOT).as_posix(): data.sha256_file(path) for path in artifacts
    }
    data.write_json(run_path, record)
    print(f"rerender: rewrote SUMMARY.md and {run_dir.name}/report.md from stored metrics")
    return 0


def run_reproduce(*, rerun_reason: str | None) -> int:
    """Recompute every artifact and compare it byte for byte."""

    global _tldextract_provenance
    _tldextract_provenance = configure_tldextract()

    current_method = data.method_sha256()
    recorded = data.read_method_hash_file()
    if recorded is None:
        print(
            "reproduce: results/METHOD_HASH is missing; run the detection stage first",
            file=sys.stderr,
        )
        return 2
    if recorded != current_method and not rerun_reason:
        print(
            "reproduce: METHOD.md differs from results/METHOD_HASH; refusing to compare "
            "numbers produced under another protocol",
            file=sys.stderr,
        )
        return 3

    data.check_synthetic_hashes()
    data.ensure_germeval()

    config = data.load_config()
    if config.method_sha256 != current_method:
        print("reproduce: eval/configs.json does not match the current METHOD.md", file=sys.stderr)
        return 2

    bundle = run_evaluation(config.profile)
    outputs = _render_outputs(bundle)

    differences: list[str] = []
    for name, payload_bytes in outputs.items():
        path = data.RESULTS_DIR / name
        if not path.is_file() or path.read_bytes() != payload_bytes:
            differences.append(name)

    run_dir = _latest_detection_run()
    if run_dir is None:
        differences.append("results/runs/<run>/run.json")
    else:
        metrics_bytes = data.canonical_json(_metrics_payload(bundle))
        if (run_dir / "metrics.json").read_bytes() != metrics_bytes:
            differences.append(f"results/runs/{run_dir.name}/metrics.json")
        if (run_dir / "report.md").read_bytes() != outputs["SUMMARY.md"]:
            differences.append(f"results/runs/{run_dir.name}/report.md")
        for relative, payload in bundle.scoring_files.items():
            path = run_dir / relative
            if not path.is_file() or path.read_bytes() != data.canonical_json(payload):
                differences.append(f"results/runs/{run_dir.name}/{relative}")

    if SHA256SUMS.is_file():
        recorded_sums = data.read_sha256sums(SHA256SUMS)
        for path in DETECTION_FILES:
            if recorded_sums.get(path.name) != data.sha256_file(path):
                differences.append("SHA256SUMS")
                break
    else:
        differences.append("SHA256SUMS")

    if differences:
        print("reproduce: NOT deterministic; these files differ:", file=sys.stderr)
        for name in sorted(set(differences)):
            print(f"  {name}", file=sys.stderr)
        return 1

    print(
        "reproduce: bit-for-bit identical for "
        f"{len(outputs)} result files, SHA256SUMS and the latest run's metrics, "
        "report and scoring files"
    )
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Detection evaluation per METHOD.md.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--stage", choices=(STAGE,), help="run the one detection test stage")
    mode.add_argument("--tune", action="store_true", help="choose the B2 profile on dev only")
    mode.add_argument("--reproduce", action="store_true", help="rebuild and compare result files")
    mode.add_argument(
        "--rerender", action="store_true", help="rewrite the report from stored run metrics"
    )
    parser.add_argument("--run-id", default=None, help="immutable run id (default: generated)")
    parser.add_argument("--rerun-reason", default=None, help="required for a repeat of a stage")
    parser.add_argument(
        "--method-approval",
        default=None,
        help="captain approval reference when METHOD.md changed after test access",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.tune:
        return run_tuning()
    if args.reproduce:
        return run_reproduce(rerun_reason=args.rerun_reason)
    if args.rerender:
        return run_rerender()
    try:
        return run_test_stage(
            rerun_reason=args.rerun_reason,
            method_approval=args.method_approval,
            run_id=args.run_id,
        )
    except GuardRefusal as refusal:
        print(refusal, file=sys.stderr)
        return refusal.code


if __name__ == "__main__":
    raise SystemExit(main())

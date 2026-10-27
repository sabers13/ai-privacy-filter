"""Human-readable report and miss-pattern analysis.

The report is generated from the same in-memory numbers that are serialised to
JSON, so ``SUMMARY.md`` can never disagree with the result files. Every table is
preceded by a stable HTML-comment marker; ``tests/test_eval_results.py`` parses
those markers and checks each cell against the JSON.

GermEval is described by category only. No sentence, token run or entity value
from that corpus appears in a result file, this module's output or a marker.
Synthetic examples are allowed and are marked as such.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from typing import Any, Final

from ai_privacy_filter.models import Enquiry, EntityType
from eval import scoring
from eval.stats import format_interval, format_percent, percent_interval, wilson

_TITLE_RE: Final = re.compile(
    r"\b(?:Dr|Prof|Dipl|Ing|MBA|B\.A|B\.Eng|M\.Sc|Univ|Herr|Frau)\b", re.IGNORECASE
)
_PARTICLE_RE: Final = re.compile(r"\b(?:von|zu|van|de|del)\b", re.IGNORECASE)
_LEGAL_FORM_RE: Final = re.compile(
    r"\b(?:GmbH|AG|KG|OHG|GbR|UG|mbH|KGaA|e\.V\.|Co\.)\b", re.IGNORECASE
)

PATTERN_DESCRIPTIONS: Final[dict[str, str]] = {
    "person-with-title-or-suffix": (
        "salutation, academic title or name suffix sits inside the gold span but outside "
        "the detected name"
    ),
    "person-hyphenated-name": "a hyphenated given or family name is not recognised as a person",
    "person-name-particle": "a name with a von/zu/van particle is not recognised",
    "person-all-caps": "an all-caps name is not recognised",
    "person-single-token": "a lone first name appears without a surname cue",
    "person-many-tokens": "a long multi-token name is only partly recognised or missed",
    "person-name-not-recognised": "an ordinary two-token name is not recognised",
    "org-with-ampersand": "an organisation containing `&` is not recognised",
    "org-legal-form-not-anchored": "a legal form is present, but the organisation is not anchored",
    "org-not-recognised": "an organisation without a recognised legal form is missed",
    "email-not-matched": "the address shape is not matched",
    "phone-with-prefix-or-brackets": "a +49/0049 prefix or bracket form defeats the rule",
    "phone-not-matched": "the number shape is not matched",
    "iban-wrapped-across-lines": "the IBAN is wrapped across a line break",
    "iban-grouped-with-spaces": "the IBAN is grouped with spaces and not matched",
    "iban-not-matched": "the IBAN shape is not matched",
    "street-house-number-range": "the house number is a range (for example `2-5` or `8/8`)",
    "street-name-with-space": "the street name contains a space before the suffix",
    "street-abbreviated-suffix": "the street suffix is abbreviated (`str.`)",
    "street-not-matched": "the street shape is not matched",
    "postcode-city-not-matched": "the postcode/city pair is not matched",
}


def _pattern_key(entity: EntityType, status: str, text: str) -> str:
    prefix = "partial" if status == "partial" else "missed"
    if status == "partial":
        return f"partial-boundary-{entity.value.lower()}"
    if entity is EntityType.PERSON:
        if _TITLE_RE.search(text):
            return "person-with-title-or-suffix"
        if "-" in text:
            return "person-hyphenated-name"
        if _PARTICLE_RE.search(text):
            return "person-name-particle"
        letters = [character for character in text if character.isalpha()]
        if letters and text == text.upper():
            return "person-all-caps"
        if len(text.split()) == 1:
            return "person-single-token"
        if len(text.split()) >= 3:
            return "person-many-tokens"
        return "person-name-not-recognised"
    if entity is EntityType.ORG:
        if "&" in text:
            return "org-with-ampersand"
        if _LEGAL_FORM_RE.search(text):
            return "org-legal-form-not-anchored"
        return "org-not-recognised"
    if entity is EntityType.EMAIL:
        return "email-not-matched"
    if entity is EntityType.PHONE:
        if "+" in text or "(" in text:
            return "phone-with-prefix-or-brackets"
        return "phone-not-matched"
    if entity is EntityType.IBAN:
        if "\n" in text:
            return "iban-wrapped-across-lines"
        if " " in text:
            return "iban-grouped-with-spaces"
        return "iban-not-matched"
    if entity is EntityType.ADDRESS_STREET:
        tail = text.split()[-1] if text.split() else ""
        if "-" in tail or "/" in tail:
            return "street-house-number-range"
        if text.rstrip().endswith("str."):
            return "street-abbreviated-suffix"
        if " " in text.strip() and len(text.split()) > 2:
            return "street-name-with-space"
        return "street-not-matched"
    if entity is EntityType.POSTCODE_CITY:
        return "postcode-city-not-matched"
    return f"{prefix}-{entity.value.lower()}"


def _snippet(text: str, start: int, end: int, *, width: int = 36) -> str:
    before_raw = text[max(0, start - width) : start]
    after_raw = text[end : min(len(text), end + width)]
    before = " ".join(before_raw.split())
    value = " ".join(text[start:end].split())
    after = " ".join(after_raw.split())
    keep_before = " " if before_raw and before_raw[-1].isspace() and before else ""
    keep_after = " " if after_raw and after_raw[0].isspace() and after else ""
    prefix = "…" if start > width else ""
    suffix = "…" if end + width < len(text) else ""
    rendered = f"{prefix}{before}{keep_before}«{value}»{keep_after}{after}{suffix}"
    return rendered.replace("|", "\\|").replace("\n", " ")


def miss_patterns(
    enquiries: Sequence[Enquiry], outcomes: Sequence[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Return the ten most common B2 miss patterns, with synthetic examples only."""

    by_id = {enquiry.id: enquiry for enquiry in enquiries}
    counts: Counter[tuple[str, EntityType]] = Counter()
    examples: dict[tuple[str, EntityType], tuple[str, str]] = {}
    misses_by_type: Counter[EntityType] = Counter()

    for record in outcomes:
        score: scoring.RecordScore = record["score"]
        enquiry = by_id[record["id"]]
        for outcome in score.gold:
            if outcome.status == "protected":
                continue
            misses_by_type[outcome.entity_type] += 1
            text = enquiry.text[outcome.start : outcome.end]
            pattern = _pattern_key(outcome.entity_type, outcome.status, text)
            key = (pattern, outcome.entity_type)
            counts[key] += 1
            examples.setdefault(
                key, (enquiry.id, _snippet(enquiry.text, outcome.start, outcome.end))
            )

    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0][1].value, item[0][0]))
    rows: list[dict[str, Any]] = []
    for rank, ((pattern, entity), count) in enumerate(ranked[:10], start=1):
        total = misses_by_type[entity]
        example_id, example = examples[(pattern, entity)]
        rows.append(
            {
                "rank": rank,
                "pattern": pattern,
                "type": entity.value,
                "count": count,
                "share_of_type_misses_pct": round(count / total * 100.0, 1) if total else None,
                "description": PATTERN_DESCRIPTIONS.get(
                    pattern, "coverage stops at a span boundary"
                ),
                "example_id": example_id,
                "example": example,
            }
        )
    return rows


def germeval_miss_categories(outcomes: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Describe GermEval misses by category, never by sentence text."""

    counts: Counter[tuple[str, str]] = Counter()
    for record in outcomes:
        score: scoring.RecordScore = record["score"]
        for outcome in score.gold:
            if outcome.status == "protected":
                continue
            overlaps = set(outcome.overlap_types)
            if outcome.status == "missed":
                if not overlaps:
                    category = "no overlapping prediction"
                else:
                    category = "only a non-protecting type overlaps"
            elif outcome.entity_type.value in overlaps:
                category = "partial coverage, same type, boundary differs"
            elif {"PERSON", "ORG"} & overlaps:
                category = "partial coverage by the other name type"
            else:
                category = "partial coverage by another type"
            counts[(outcome.entity_type.value, category)] += 1
    return [
        {"gold_type": gold_type, "category": category, "count": count}
        for (gold_type, category), count in sorted(counts.items())
    ]


# ---------------------------------------------------------------------------
# Summary rendering
# ---------------------------------------------------------------------------


def _cell(value: float | None) -> str:
    return format_percent(value)


def _recall_row(entity: str, entry: dict[str, Any]) -> str:
    gold = entry["gold"]
    if not gold:
        return (
            f"| {entity} | 0 | 0 | not estimable | - | 0 | not estimable | "
            f"{entry['partial']} | {entry['missed']} |"
        )
    return (
        f"| {entity} | {gold} | {entry['protected']} | {_cell(entry['recall_pct'])} | "
        f"{format_interval(entry['recall_wilson_pct'])} | {entry['strict_protected']} | "
        f"{_cell(entry['strict_recall_pct'])} | {entry['partial']} | {entry['missed']} |"
    )


def _precision_row(entity: str, entry: dict[str, Any]) -> str:
    if not entry["predicted"]:
        return f"| {entity} | 0 | 0 | n/a (0 predictions) | - | 0 | n/a (0 predictions) |"
    return (
        f"| {entity} | {entry['predicted']} | {entry['correct']} | "
        f"{_cell(entry['precision_pct'])} | {format_interval(entry['precision_wilson_pct'])} | "
        f"{entry['exact_correct']} | {_cell(entry['exact_precision_pct'])} |"
    )


def _winner_line(
    baselines: dict[str, Any], entity: str, *, metric: str, primary: str = "B2"
) -> str:
    key = "recall_pct" if metric == "recall" else "precision_pct"
    values = {
        name: payload["per_type"][entity][key]
        for name, payload in baselines.items()
        if payload["per_type"][entity][key] is not None
    }
    if not values:
        return f"{entity}: no baseline produces a {metric} number"
    ordered = sorted(values.items(), key=lambda item: (-item[1], item[0]))
    best_value = ordered[0][1]
    top_names = [name for name, value in ordered if value == best_value]
    if len(top_names) > 1:
        if primary in top_names:
            others = ", ".join(name for name in top_names if name != primary)
            return f"{entity}: {primary} ties {others} at {best_value:.1f}%"
        others = ", ".join(top_names)
        primary_value = values.get(primary)
        lead = (
            f"{best_value - primary_value:.1f} points over {primary}"
            if primary_value is not None
            else f"ahead of {primary}"
        )
        return (
            f"{entity}: simpler baselines {others} tie at {best_value:.1f}%, "
            f"{lead} ({best_value:.1f}% vs {primary_value:.1f}%)"
        )
    best_name = top_names[0]
    runner_name, runner_value = (
        (ordered[1][0], ordered[1][1]) if len(ordered) > 1 else (primary, 0.0)
    )
    if best_name == primary:
        return (
            f"{entity}: {primary} leads {metric} by {best_value - runner_value:.1f} points "
            f"over {runner_name} ({best_value:.1f}% vs {runner_value:.1f}%)"
        )
    return (
        f"{entity}: simpler baseline {best_name} wins {metric} by "
        f"{best_value - runner_value:.1f} points over {runner_name} "
        f"({best_value:.1f}% vs {runner_value:.1f}%)"
    )


def _winner_paragraph(baselines: dict[str, Any], *, metric: str, entities: Sequence[str]) -> str:
    """One caption naming the winner for every type in a table group."""

    return " · ".join(_winner_line(baselines, entity, metric=metric) for entity in entities) + "."


def _answer_caught(synthetic: dict[str, Any]) -> str:
    baselines = synthetic["baselines"]
    per_type = baselines["B2"]["per_type"]
    estimable = [
        (entity, entry)
        for entity, entry in per_type.items()
        if entry["gold"] and entry["recall_pct"] is not None
    ]
    gold = sum(entry["gold"] for _, entry in estimable)
    protected = sum(entry["protected"] for _, entry in estimable)
    worst_entity, worst_entry = min(estimable, key=lambda item: item[1]["recall_pct"] or 0.0)
    wins = []
    for name in ("B0", "B1"):
        won = [
            entity
            for entity, entry in per_type.items()
            if entry["gold"]
            and baselines[name]["per_type"][entity]["recall_pct"] is not None
            and baselines[name]["per_type"][entity]["recall_pct"] > (entry["recall_pct"] or 0.0)
        ]
        if won:
            wins.append(f"{name} wins {', '.join(won)}")
    baseline_text = (" " + "; ".join(wins) + ".") if wins else " No simpler baseline wins a type."
    return (
        f"**1. Caught.** B2 protects {protected} of {gold} gold spans across the seven "
        f"estimable types ({protected / gold * 100.0:.1f}%, 95 % CI "
        f"{format_interval(_pooled_interval(estimable))}). The worst type is {worst_entity} "
        f"({worst_entry['protected']}/{worst_entry['gold']}, "
        f"{worst_entry['recall_pct']:.1f}%, 95 % CI "
        f"{format_interval(worst_entry['recall_wilson_pct'])}).{baseline_text}"
    )


def _pooled_interval(estimable: Sequence[tuple[str, dict[str, Any]]]) -> list[float]:
    gold = sum(entry["gold"] for _, entry in estimable)
    protected = sum(entry["protected"] for _, entry in estimable)
    return percent_interval(*wilson(protected, gold))


def _answer_leaked(leak: dict[str, Any]) -> str:
    b2 = leak["baselines"]["B2"]
    other = []
    for name in ("B0", "B1"):
        payload = leak["baselines"][name]
        other.append(f"{name} {payload['leaked_enquiries']}/{payload['records']}")
    types = ", ".join(f"{name} ({count})" for name, count in b2["leaked_types"].items())
    types_text = types or "no type"
    missed = b2["missed_gold_spans"]
    partial = b2["partial_gold_spans"]
    missed_text = "1 gold span was missed" if missed == 1 else f"{missed} gold spans were missed"
    partial_text = (
        "1 was only partly covered" if partial == 1 else f"{partial} were only partly covered"
    )
    return (
        f"**2. Leaked.** B2 leaked {b2['leaked_enquiries']} of {b2['records']} synthetic "
        f"test enquiries ({b2['leak_rate_pct']:.1f}%, 95 % CI "
        f"{format_interval(b2['leak_wilson_pct'])}, upper bound "
        f"{b2['leak_wilson_pct'][1]:.1f}%). {missed_text} and {partial_text}; leaked types: "
        f"{types_text}. The production guard blocked {b2['guard_blocks']} enquiries; the "
        f"difference between leaks and blocks is the missed-value path. Baselines: "
        f"{'; '.join(other)}."
    )


def _answer_overmasked(synthetic: dict[str, Any]) -> str:
    over = synthetic["baselines"]["B2"]["over_masking"]
    extra_by_type = over["extra_by_type"]
    worst = (
        max(extra_by_type.items(), key=lambda item: (item[1], item[0])) if extra_by_type else None
    )
    worst_text = f"{worst[0]} ({worst[1]} characters)" if worst else "no type"
    char_text = (
        f"{over['character_rate_pct']:.1f}% of replaced characters were outside a gold span"
        if over["character_rate_pct"] is not None
        else "no characters were replaced"
    )
    return (
        f"**3. Over-masked.** {over['enquiries_with_extra']} of {over['records']} synthetic "
        f"test enquiries had at least one extra character "
        f"({over['enquiry_rate_pct']:.1f}%, 95 % CI "
        f"{format_interval(over['enquiry_wilson_pct'])}); {over['extra_chars']} of "
        f"{over['replaced_chars']} replaced characters were extra, and {char_text} "
        f"(cluster bootstrap {format_interval(over['character_bootstrap_pct'])}). The worst "
        f"type is {worst_text}."
    )


def _answer_backend(restore: dict[str, Any]) -> str:
    draft = restore["draft_restoration"]
    return (
        "**4. Backend quality.** Not measured in this stage: the fixed 40-enquiry backend "
        f"run belongs to T12 and is currently {draft['status']} ({draft['reason']})."
    )


def _recall_table(baseline_name: str, payload: dict[str, Any]) -> list[str]:
    lines = [
        f"### {payload['name']}",
        "",
        payload["description"],
        "",
        f"<!-- table:synthetic-recall:{baseline_name} -->",
        "| type | gold | protected | recall | 95% CI | strict protected | strict recall | "
        "partial | missed |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for entity in EntityType:
        lines.append(_recall_row(entity.value, payload["per_type"][entity.value]))
    return lines


def _precision_table(baseline_name: str, payload: dict[str, Any]) -> list[str]:
    lines = [
        f"### {payload['name']}",
        "",
        f"<!-- table:synthetic-precision:{baseline_name} -->",
        "| type | predicted | correct | precision | 95% CI | exact | exact precision |",
        "|---|---|---|---|---|---|---|",
    ]
    for entity in EntityType:
        lines.append(_precision_row(entity.value, payload["per_type"][entity.value]))
    lines += [
        "",
        "Precision counts a prediction as correct only when its whole range is inside a "
        "compatible gold span; an over-long prediction therefore counts as over-masking, "
        "not as correct. A prediction split into two pieces inside one gold span counts "
        "twice, which is the stated cost of counting span occurrences. `exact` requires "
        "both boundaries to match.",
    ]
    return lines


def _germeval_table(baseline_name: str, payload: dict[str, Any]) -> list[str]:
    lines = [
        f"### {payload['name']}",
        "",
        f"<!-- table:germeval:{baseline_name} -->",
        "| type | gold | protected | recall | 95 % CI | strict protected | strict recall | "
        "precision | exact precision |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for entity in ("PERSON", "ORG"):
        entry = payload["per_type"][entity]
        lines.append(
            f"| {entity} | {entry['gold']} | {entry['protected']} | "
            f"{_cell(entry['recall_pct'])} | {format_interval(entry['recall_wilson_pct'])} | "
            f"{entry['strict_protected']} | {_cell(entry['strict_recall_pct'])} | "
            f"{_cell(entry['precision_pct'])} | {_cell(entry['exact_precision_pct'])} |"
        )
    micro = payload["micro_primary"]
    lines.append(
        f"| PERSON+ORG (micro) | {micro['gold']} | {micro['protected']} | "
        f"{_cell(micro['recall_pct'])} | {format_interval(micro['recall_wilson_pct'])} | "
        f"{micro['strict_protected']} | {_cell(micro['strict_recall_pct'])} | - | - |"
    )
    lines += [
        "",
        f"<!-- table:germeval-location:{baseline_name} -->",
        "| LOCATION gold | covered by any personal-data span | incidental coverage | 95 % CI |",
        "|---|---|---|---|",
        f"| {payload['location_incidental']['gold']} | "
        f"{payload['location_incidental']['covered']} | "
        f"{_cell(payload['location_incidental']['coverage_pct'])} | "
        f"{format_interval(payload['location_incidental']['coverage_wilson_pct'])} |",
        "",
        "LOCATION is not a detection target: `EntityType` has no LOCATION member and the "
        "detector never emits one, so its class recall is 0 % by construction. The incidental "
        "coverage above is informational and is never folded into personal-data recall. "
        "`OTHER`, `deriv` and `part` classes are not scored. A prediction split inside one "
        "gold span counts twice in precision.",
    ]
    return lines


def render_summary(
    synthetic: dict[str, Any],
    germeval: dict[str, Any],
    leak: dict[str, Any],
    restore: dict[str, Any],
) -> str:
    """Render the complete, deterministic ``SUMMARY.md``."""

    provenance = synthetic["provenance"]
    environment = provenance["environment"]
    baseline_names = ("B0", "B1", "B2")
    lines: list[str] = [
        "# Detection evaluation — synthetic test and GermEval 2014 test",
        "",
        f"Protocol: `METHOD.md`, SHA-256 `{provenance['method_sha256']}`. "
        f"B2 configuration: `{provenance['b2_profile']}` "
        f"(config SHA-256 `{provenance['config_sha256']}`).",
        "",
        f"Data: synthetic test, {synthetic['records']} enquiries, "
        f"{synthetic['gold_spans']} gold spans, SHA-256 "
        f"`{provenance['synthetic_test_sha256']}`; GermEval 2014 test, "
        f"{germeval['sentences']} sentences, manifest SHA-256 "
        f"`{provenance['germeval_manifest_sha256']}`.",
        "",
        "Environment: Python "
        f"{environment['python']}, presidio-analyzer {environment['presidio']}, "
        f"spaCy {environment['spacy']}, {environment['spacy_model']}, "
        f"{environment['platform']}. Network: {provenance['network']}.",
        "",
        "This is one test-split run at stage `detection`. The run id, commit, timestamps "
        "and artifact hashes are in `results/runs/<run-id>/run.json`; the rerun table is "
        "`docs/eval-reruns.md`. No number here is tuned on test data, and there are no pass "
        "thresholds: the filter is measured as it is.",
        "",
        "## The four answers",
        "",
        _answer_caught(synthetic),
        "",
        _answer_leaked(leak),
        "",
        _answer_overmasked(synthetic),
        "",
        _answer_backend(restore),
        "",
        "## Synthetic test — recall (primary, compatible-type coverage)",
        "",
        "A gold span is protected only when every character is inside a prediction of a "
        "compatible type; adjacent predictions may combine. `strict` ignores the "
        "PERSON/ORG and EMAIL/URL relaxations. B0 has no PERSON or ORG rule, so its 0 % "
        "there is by construction, not a measurement failure.",
        "",
    ]
    for name in baseline_names:
        lines += _recall_table(name, synthetic["baselines"][name])
        lines += [""]
    lines += [
        "Recall leaders (all three baselines): "
        + _winner_paragraph(
            synthetic["baselines"],
            metric="recall",
            entities=[
                member.value
                for member in EntityType
                if synthetic["baselines"]["B2"]["per_type"][member.value]["gold"]
            ],
        ),
        "",
        "## Synthetic test — precision",
        "",
    ]
    for name in baseline_names:
        lines += _precision_table(name, synthetic["baselines"][name])
        lines += [""]
    lines += [
        "Precision leaders (all three baselines): "
        + _winner_paragraph(
            synthetic["baselines"],
            metric="precision",
            entities=[
                member.value
                for member in EntityType
                if synthetic["baselines"]["B2"]["per_type"][member.value]["predicted"]
            ],
        ),
        "",
        "## GermEval 2014 test — PERSON and ORG",
        "",
        f"Each of the {germeval['sentences']} sentences is evaluated independently. Only the "
        "outer annotation column is used. No GermEval text is shown, stored in a result or "
        "sent to a model.",
        "",
    ]
    for name in baseline_names:
        lines += _germeval_table(name, germeval["baselines"][name])
        lines += [""]
    lines += [
        "Recall leaders (all three baselines): "
        + _winner_paragraph(germeval["baselines"], metric="recall", entities=["PERSON", "ORG"]),
        "",
    ]

    lines += [
        "## Leak rate (synthetic test, independent gold audit)",
        "",
        "The audit checks the exact prompt string against the gold values, not against the "
        "detector's mapping, so a missed value is caught even though the production guard "
        "cannot see it. Phone and IBAN values are also compared digit by digit (at least "
        "four digits). No leaking prompt is stored.",
        "",
        "<!-- table:leak -->",
        "| baseline | leaked | leak rate | 95 % CI | missed gold spans | guard blocks | "
        "residual chars | residual rate | bootstrap CI |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for name in baseline_names:
        payload = leak["baselines"][name]
        residual = payload["residual_gold_chars"]
        lines.append(
            f"| {name} | {payload['leaked_enquiries']} | "
            f"{payload['leak_rate_pct']:.1f}% | {format_interval(payload['leak_wilson_pct'])} | "
            f"{payload['missed_gold_spans']} | {payload['guard_blocks']} | "
            f"{residual['uncovered']} | {_cell(residual['rate_pct'])} | "
            f"{format_interval(residual['bootstrap_pct'])} |"
        )
    lines += [
        "",
        "Residual characters are gold characters not inside any compatible prediction, "
        "divided by all gold characters; the interval is a 10,000-resample enquiry-cluster "
        "bootstrap (seed 20261027). A guard block and a leak are different events: the guard "
        "only knows values the detector mapped.",
        "",
        "## Over-masking (synthetic test)",
        "",
        "Extra characters are inside a prediction but outside every gold span of any type. "
        "The enquiry-level interval is Wilson; the character-level interval is an "
        "enquiry-cluster bootstrap.",
        "",
        "<!-- table:overmask -->",
        "| baseline | enquiries with extra | enquiry rate | 95 % CI | extra chars | "
        "replaced chars | character rate | bootstrap CI | false spans | predictions |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for name in baseline_names:
        payload = synthetic["baselines"][name]["over_masking"]
        lines.append(
            f"| {name} | {payload['enquiries_with_extra']} | "
            f"{payload['enquiry_rate_pct']:.1f}% | "
            f"{format_interval(payload['enquiry_wilson_pct'])} | {payload['extra_chars']} | "
            f"{payload['replaced_chars']} | {_cell(payload['character_rate_pct'])} | "
            f"{format_interval(payload['character_bootstrap_pct'])} | "
            f"{payload['false_spans']} | {payload['predictions']} |"
        )
    lines += [
        "",
        "<!-- table:overmask-type -->",
        "| baseline | predicted type | extra characters |",
        "|---|---|---|",
    ]
    for name in baseline_names:
        extra = synthetic["baselines"][name]["over_masking"]["extra_by_type"]
        for entity, count in sorted(extra.items(), key=lambda item: (-item[1], item[0])):
            lines.append(f"| {name} | {entity} | {count} |")
    lines += [
        "",
        "## Restore round-trip (synthetic test, B2)",
        "",
        "`restore(redact(text)).text == text` byte for byte. Missed values stay in the "
        "text on both sides, so this measures redaction/restoration fidelity, not recall. "
        "A failure caused by the same value appearing with different casing or spacing is "
        "reported as `normalisation variant`; no value is printed.",
        "",
        "<!-- table:restore -->",
        "| exact round-trips | total | exactness | 95 % CI | failures |",
        "|---|---|---|---|---|",
        f"| {restore['exact_round_trips']} | {restore['records']} | "
        f"{restore['exact_pct']:.1f}% | {format_interval(restore['exact_wilson_pct'])} | "
        f"{len(restore['failures'])} |",
        "",
        "Draft restoration is per backend and belongs to the T12 run: "
        f"{restore['draft_restoration']['status']} ({restore['draft_restoration']['reason']}).",
        "",
        "## Ten most common miss patterns (synthetic test)",
        "",
        "Patterns are derived from B2 misses on the synthetic test split. Examples are "
        "synthetic corpus text, marked with «guillemets» around the gold value; they are "
        "fictional Faker values and templates, not real people or companies.",
        "",
        "<!-- table:miss-patterns -->",
        "| # | pattern | type | count | share of type misses | example (synthetic) |",
        "|---|---|---|---|---|---|",
    ]
    b2_patterns = synthetic["miss_patterns"]
    b1_patterns = synthetic.get("miss_patterns_b1", [])
    for row in b2_patterns:
        share = (
            f"{row['share_of_type_misses_pct']:.1f}%"
            if row["share_of_type_misses_pct"] is not None
            else "n/a"
        )
        lines.append(
            f"| {row['rank']} | {row['pattern']} | {row['type']} | {row['count']} | "
            f"{share} | {row['example_id']}: {row['example']} |"
        )
    if len(b2_patterns) < 10:
        pattern_word = "pattern" if len(b2_patterns) == 1 else "patterns"
        lines += [
            "",
            f"B2 produced {len(b2_patterns)} distinct miss {pattern_word} on this corpus, "
            "short of ten. To keep the ten-pattern deliverable without inventing product misses, "
            "the table below lists B1's ten most common miss patterns on the same synthetic "
            "records. They are an untuned baseline's failures, not B2's; they show where a "
            "simpler detector breaks, and no value in them is real.",
            "",
            "<!-- table:miss-patterns-b1 -->",
            "| # | pattern | type | count | share of type misses | example (synthetic) |",
            "|---|---|---|---|---|---|",
        ]
        for row in b1_patterns:
            share = (
                f"{row['share_of_type_misses_pct']:.1f}%"
                if row["share_of_type_misses_pct"] is not None
                else "n/a"
            )
            lines.append(
                f"| {row['rank']} | {row['pattern']} | {row['type']} | {row['count']} | "
                f"{share} | {row['example_id']}: {row['example']} |"
            )
    lines += [
        "",
        "## GermEval misses by category (no sentence quoted)",
        "",
        "GermEval misses are aggregated by gold type and coverage category. No sentence, "
        "token run or entity value from GermEval appears here or anywhere in `results/`.",
        "",
        "<!-- table:germeval-miss-categories -->",
        "| gold type | category | count |",
        "|---|---|---|",
    ]
    for row in germeval["miss_categories"]:
        lines.append(f"| {row['gold_type']} | {row['category']} | {row['count']} |")

    lines += [
        "",
        "## Limitations",
        "",
        "- Synthetic enquiries are template-generated German, not real customer mail.",
        "- 160 test enquiries and 40 triage enquiries are small.",
        "- GermEval is Wikipedia/news text, not enquiries, and has no EMAIL, PHONE or IBAN labels.",
        "- Wilson intervals treat spans as trials and may be optimistic; the cluster "
        "bootstrap is the sensitivity check.",
        "- Urgency is confounded with category in the synthetic corpus.",
        "- One sample per backend per enquiry; LLM output is not deterministic.",
        "- Latency is one machine and one network path, not a benchmark.",
        "- One reviewer scores the draft rubric.",
        "- There are no pass thresholds. These are descriptive measurements of "
        "pseudonymisation, not a compliance or legal claim. This is not legal advice.",
        "",
        "## Reproduction",
        "",
        "`make reproduce` re-verifies the GermEval download checksums, recomputes every "
        "number from the committed corpus and the verified download with the network "
        "blocked, and compares the result files byte for byte. The result files carry no "
        "timestamp, run id or commit, so a rebase cannot change them.",
        "",
    ]
    return "\n".join(lines)

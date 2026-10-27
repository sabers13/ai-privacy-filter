"""Placeholder mapping and restoration, against the frozen Span contract."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from ai_privacy_filter.models import EntityType, Span
from ai_privacy_filter.pseudonymise import (
    canonical_placeholder,
    normalise_value,
    placeholder_type,
    redact,
    restore,
)


def _span(
    text: str,
    needle: str,
    entity_type: EntityType,
    *,
    occurrence: int = 0,
    source: str = "unit-test",
) -> Span:
    """Return a Span for the n-th occurrence of ``needle`` in ``text``."""
    start = -1
    for _ in range(occurrence + 1):
        start = text.index(needle, start + 1)
    return Span(
        start=start,
        end=start + len(needle),
        type=entity_type,
        text=needle,
        score=0.9,
        source=source,
    )


def test_places_one_placeholder_per_span_in_source_order() -> None:
    text = "Max Müller schreibt an anna@example.org."
    spans = [
        _span(text, "Max Müller", EntityType.PERSON),
        _span(text, "anna@example.org", EntityType.EMAIL),
    ]
    result = redact(text, spans[::-1])
    assert result.redacted_text == "[PERSON_1] schreibt an [EMAIL_1]."
    assert result.mapping == {"[PERSON_1]": "Max Müller", "[EMAIL_1]": "anna@example.org"}


def test_numbers_placeholders_per_type_in_order_of_first_appearance() -> None:
    text = (
        "Bei Muster GmbH: Anna Müller, Erika Muster. Kontakt anna@example.org, erika@example.org."
    )
    spans = [
        _span(text, "Muster GmbH", EntityType.ORG),
        _span(text, "Anna Müller", EntityType.PERSON),
        _span(text, "Erika Muster", EntityType.PERSON),
        _span(text, "anna@example.org", EntityType.EMAIL),
        _span(text, "erika@example.org", EntityType.EMAIL),
    ]
    result = redact(text, spans)
    assert result.redacted_text == (
        "Bei [ORG_1]: [PERSON_1], [PERSON_2]. Kontakt [EMAIL_1], [EMAIL_2]."
    )
    assert list(result.mapping) == ["[ORG_1]", "[PERSON_1]", "[PERSON_2]", "[EMAIL_1]", "[EMAIL_2]"]


def test_repeated_value_reuses_one_placeholder() -> None:
    text = "Anna Müller ruft an. Danke, Anna Müller."
    spans = [
        _span(text, "Anna Müller", EntityType.PERSON, occurrence=0),
        _span(text, "Anna Müller", EntityType.PERSON, occurrence=1),
    ]
    result = redact(text, spans)
    assert result.redacted_text == "[PERSON_1] ruft an. Danke, [PERSON_1]."
    assert result.mapping == {"[PERSON_1]": "Anna Müller"}
    assert len(result.spans) == 2


def test_case_and_whitespace_variants_share_one_placeholder() -> None:
    text = "MAX  MÜLLER schrieb, später antwortete max müller."
    spans = [
        _span(text, "MAX  MÜLLER", EntityType.PERSON),
        _span(text, "max müller", EntityType.PERSON),
    ]
    result = redact(text, spans)
    assert result.redacted_text == "[PERSON_1] schrieb, später antwortete [PERSON_1]."
    assert result.mapping == {"[PERSON_1]": "MAX  MÜLLER"}


def test_mapping_keeps_the_first_written_form() -> None:
    text = "anna@example.org und ANNA@EXAMPLE.ORG"
    spans = [
        _span(text, "anna@example.org", EntityType.EMAIL),
        _span(text, "ANNA@EXAMPLE.ORG", EntityType.EMAIL),
    ]
    result = redact(text, spans)
    assert list(result.mapping.values()) == ["anna@example.org"]


def test_overlapping_spans_keep_the_earlier_longer_one() -> None:
    text = "Anna Müller"
    outer = _span(text, "Anna Müller", EntityType.PERSON)
    inner = _span(text, "Müller", EntityType.PERSON)
    for spans in ([outer, inner], [inner, outer]):
        result = redact(text, spans)
        assert result.redacted_text == "[PERSON_1]"
        assert result.spans == [outer]


def test_zero_length_whitespace_and_out_of_range_spans_are_ignored() -> None:
    text = "Anna Müller  "
    spans = [
        _span(text, "Anna Müller", EntityType.PERSON),
        Span(start=0, end=0, type=EntityType.PERSON, text="", score=0.5, source="unit-test"),
        Span(start=11, end=13, type=EntityType.PERSON, text="  ", score=0.5, source="unit-test"),
        Span(
            start=5, end=99, type=EntityType.PERSON, text="Müller  ", score=0.5, source="unit-test"
        ),
    ]
    result = redact(text, spans)
    assert result.redacted_text == "[PERSON_1]  "
    assert result.mapping == {"[PERSON_1]": "Anna Müller"}
    assert result.spans == [spans[0]]


def test_no_spans_leaves_text_untouched() -> None:
    result = redact("Nichts zu ersetzen.", [])
    assert result.redacted_text == "Nichts zu ersetzen."
    assert result.mapping == {}
    assert result.spans == []


def test_redacted_text_contains_no_original_value() -> None:
    text = "Rückruf an 0170 1234567, IBAN DE89 3704 0044 0532 0130 00."
    spans = [
        _span(text, "0170 1234567", EntityType.PHONE),
        _span(text, "DE89 3704 0044 0532 0130 00", EntityType.IBAN),
    ]
    result = redact(text, spans)
    for original in result.mapping.values():
        assert original not in result.redacted_text


def test_mapping_stays_out_of_the_result_repr() -> None:
    text = "Anna Müller"
    result = redact(text, [_span(text, text, EntityType.PERSON)])
    assert "mapping" not in repr(result)


def test_redact_does_not_log_values(caplog: pytest.LogCaptureFixture) -> None:
    text = "Anna Müller, 0170 1234567, anna@example.org"
    spans = [
        _span(text, "Anna Müller", EntityType.PERSON),
        _span(text, "0170 1234567", EntityType.PHONE),
        _span(text, "anna@example.org", EntityType.EMAIL),
    ]
    with caplog.at_level(logging.DEBUG):
        result = redact(text, spans)
    for value in result.mapping.values():
        assert value not in caplog.text


def test_restore_round_trip() -> None:
    text = "Sehr geehrte Damen und Herren,\n\nAnna Müller, 0170 1234567, anna@example.org."
    spans = [
        _span(text, "Anna Müller", EntityType.PERSON),
        _span(text, "0170 1234567", EntityType.PHONE),
        _span(text, "anna@example.org", EntityType.EMAIL),
    ]
    result = redact(text, spans)
    restored = restore(result.redacted_text, result.mapping)
    assert restored.text == text
    assert restored.unknown_placeholders == []


def test_restore_tolerates_case_and_spacing_around_placeholders() -> None:
    mapping = {"[PERSON_1]": "Anna Müller", "[ADDRESS_STREET_1]": "Hauptstraße 5"}
    draft = "Guten Tag [ person _ 1 ], ist [ADDRESS STREET_01] korrekt?\n\n[PERSON_1]"
    restored = restore(draft, mapping)
    assert restored.text == ("Guten Tag Anna Müller, ist Hauptstraße 5 korrekt?\n\nAnna Müller")
    assert restored.unknown_placeholders == []


def test_restore_leaves_unknown_placeholders_and_reports_each_once() -> None:
    mapping = {"[PERSON_1]": "Anna Müller"}
    draft = "Hallo [PERSON_1], [PERSON_2] und [ERFUNDEN_1], nochmals [PERSON_2]."
    restored = restore(draft, mapping)
    assert restored.text == "Hallo Anna Müller, [PERSON_2] und [ERFUNDEN_1], nochmals [PERSON_2]."
    assert restored.unknown_placeholders == ["[PERSON_2]", "[ERFUNDEN_1]"]


def test_restore_with_empty_mapping_reports_every_placeholder() -> None:
    restored = restore("[PERSON_1] und [ORG_1]", {})
    assert restored.text == "[PERSON_1] und [ORG_1]"
    assert restored.unknown_placeholders == ["[PERSON_1]", "[ORG_1]"]


def test_restore_does_not_log_values(caplog: pytest.LogCaptureFixture) -> None:
    mapping = {"[PERSON_1]": "Anna Müller"}
    with caplog.at_level(logging.DEBUG):
        restored = restore("Hallo [PERSON_1]", mapping)
    assert restored.text == "Hallo Anna Müller"
    assert "Anna Müller" not in caplog.text


def test_normalise_value() -> None:
    decomposed = "Mu\u0308ller"  # u + combining diaeresis
    assert normalise_value(decomposed) == "müller"
    assert normalise_value("  Anna \n\t Müller  ") == "anna müller"
    assert normalise_value("MÜLLER") == normalise_value("müller")


def test_canonical_placeholder() -> None:
    assert canonical_placeholder("[PERSON_1]") == "PERSON_1"
    assert canonical_placeholder("[ person _ 01 ]") == "PERSON_1"
    assert canonical_placeholder("ADDRESS STREET 2") == "ADDRESS_STREET_2"


def test_placeholder_type() -> None:
    assert placeholder_type("[PERSON_1]") == EntityType.PERSON
    assert placeholder_type("[ADDRESS_STREET_2]") == EntityType.ADDRESS_STREET
    assert placeholder_type("[INVENTED_1]") is None
    assert placeholder_type("not a placeholder") is None


CORPUS = Path(__file__).parent / "fixtures" / "synthetic_texts.json"
CORPUS_TEXTS: list[dict[str, object]] = json.loads(CORPUS.read_text(encoding="utf-8"))["texts"]


@pytest.mark.parametrize("record", CORPUS_TEXTS, ids=lambda record: record["id"])
def test_fixture_texts_round_trip(record: dict[str, object]) -> None:
    text = str(record["text"])
    raw_spans = record["spans"]
    assert isinstance(raw_spans, list)
    spans = [Span.model_validate(raw) for raw in raw_spans]
    result = redact(text, spans)
    restored = restore(result.redacted_text, result.mapping)
    assert restored.text == text
    assert restored.unknown_placeholders == []

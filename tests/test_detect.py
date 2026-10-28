"""Whole-pipeline detection over hand-written synthetic enquiries.

The synthetic corpus uses enquiries with the same shape (text plus
character-exact gold spans). These fixtures are written by hand and use
Faker-style German names only.
"""

from __future__ import annotations

import itertools
import json
import socket
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given
from hypothesis import settings as hypothesis_settings
from hypothesis import strategies as st

from ai_privacy_filter import detect as detect_module
from ai_privacy_filter.detect import _to_span, detect, merge_spans
from ai_privacy_filter.models import Enquiry, EntityType, Span

CASES: list[tuple[str, set[tuple[str, EntityType]]]] = [
    (
        "Sehr geehrte Frau Weber,\n\n"
        "mein Name ist Martin Koch. Sie erreichen mich unter +49 30 1234 5678\n"
        "oder per E-Mail an martin.koch@example.de.\n\n"
        "Mit freundlichen Grüßen\nMartin Koch\nBäckerei Weber GmbH",
        {
            ("Frau Weber", EntityType.PERSON),
            ("Martin Koch", EntityType.PERSON),
            ("+49 30 1234 5678", EntityType.PHONE),
            ("martin.koch@example.de", EntityType.EMAIL),
            ("Bäckerei Weber GmbH", EntityType.ORG),
        },
    ),
    (
        "Bitte liefern Sie an die Bäckerei Weber GmbH, Berliner Straße 5, 10115 Berlin.\n"
        "USt-IdNr. DE123456789. Fragen an orders@example.de oder https://example.de/kontakt.",
        {
            ("Bäckerei Weber GmbH", EntityType.ORG),
            ("Berliner Straße 5", EntityType.ADDRESS_STREET),
            ("10115 Berlin", EntityType.POSTCODE_CITY),
            ("DE123456789", EntityType.VAT_ID),
            ("orders@example.de", EntityType.EMAIL),
            ("https://example.de/kontakt", EntityType.URL),
        },
    ),
    (
        "Angebot an die Bäckerei Weber GmbH. Details: https://www.beispiel.example/angebot",
        {
            ("Bäckerei Weber GmbH", EntityType.ORG),
            ("https://www.beispiel.example/angebot", EntityType.URL),
        },
    ),
    (
        "Bewerbung von Anna Schmidt, geboren am 03.05.1990.\n"
        "IBAN DE89 3704 0044 0532 0130 00, Tel. 0170/123 45 67.",
        {
            ("Anna Schmidt", EntityType.PERSON),
            ("03.05.1990", EntityType.DATE_OF_BIRTH),
            ("DE89 3704 0044 0532 0130 00", EntityType.IBAN),
            ("0170/123 45 67", EntityType.PHONE),
        },
    ),
]


def test_hand_written_fixtures_find_every_expected_entity() -> None:
    for text, expected in CASES:
        found = {(span.text, span.type) for span in detect(text)}
        assert expected <= found, (text, expected - found)


@pytest.mark.parametrize(("text", "_expected"), CASES)
def test_span_offsets_slice_to_text(text: str, _expected: object) -> None:
    spans = detect(text)
    assert spans
    for span in spans:
        assert 0 <= span.start < span.end <= len(text)
        assert text[span.start : span.end] == span.text
        assert 0.0 <= span.score <= 1.0
        assert span.source


@pytest.mark.parametrize(("text", "_expected"), CASES)
def test_spans_are_sorted_and_disjoint(text: str, _expected: object) -> None:
    spans = detect(text)
    assert spans == sorted(spans, key=lambda span: (span.start, span.end))
    for left, right in itertools.pairwise(spans):
        assert left.end <= right.start, (left, right)


def test_empty_text_has_no_spans() -> None:
    assert detect("") == []


def test_unknown_profile_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown detection profile"):
        detect("Text", profile="paranoid")  # type: ignore[arg-type]


def test_profiles_differ_on_a_hyphenated_iban() -> None:
    text = "IBAN DE89-3704-0044-0532-0130-00"
    assert detect(text) == []
    assert [span.text for span in detect(text, profile="strict")] == ["DE89-3704-0044-0532-0130-00"]


def test_plain_dates_are_not_birth_dates() -> None:
    types = {span.type for span in detect("Rechnung vom 12.03.2025 über 1.250,00 EUR.")}
    assert EntityType.DATE_OF_BIRTH not in types


class _FakeResult:
    """Minimal stand-in for a Presidio RecognizerResult."""

    def __init__(self, entity_type: str, start: int, end: int, score: float = 0.9) -> None:
        self.entity_type = entity_type
        self.start = start
        self.end = end
        self.score = score
        self.recognition_metadata = {"recognizer_name": "Fake"}


def test_to_span_drops_unknown_entity_types() -> None:
    assert _to_span("Berlin", _FakeResult("LOCATION", 0, 6)) is None


def test_to_span_drops_invalid_bounds() -> None:
    assert _to_span("Herr Koch", _FakeResult("PERSON", 0, 99)) is None
    assert _to_span("Herr Koch", _FakeResult("PERSON", 3, 3)) is None
    assert _to_span("Herr Koch", _FakeResult("PERSON", -1, 4)) is None


def test_to_span_keeps_a_plain_person_name() -> None:
    span = _to_span("Herr Koch", _FakeResult("PERSON", 5, 9))
    assert span is not None
    assert span.text == "Koch"
    assert span.source == "Fake"


def test_to_span_strips_a_greeting_and_drops_a_forward_header() -> None:
    greeting = _to_span("Hallo Konstantin,", _FakeResult("PERSON", 0, 16))
    assert greeting is not None
    assert greeting.text == "Konstantin"
    header = "---------- Weitergeleitete Nachricht"
    assert _to_span(header, _FakeResult("PERSON", 0, len(header))) is None


def test_to_span_trims_ner_noise() -> None:
    person = _to_span("Max Mustermann\nStraße", _FakeResult("PERSON", 0, 18))
    assert person is not None
    assert person.text == "Max Mustermann"
    assert _to_span("info@example.de", _FakeResult("ORG", 0, 16)) is None
    assert _to_span("https://example.de", _FakeResult("PERSON", 0, 18)) is None
    url = _to_span("Siehe https://example.de.", _FakeResult("URL", 6, 24))
    assert url is not None
    assert url.text == "https://example.de"


def test_merge_of_nothing_is_empty() -> None:
    assert merge_spans([]) == []


def _span(
    start: int,
    end: int,
    entity_type: EntityType,
    score: float,
    source: str = "unit-test",
) -> Span:
    return Span(
        start=start,
        end=end,
        type=entity_type,
        text="x" * (end - start),
        score=score,
        source=source,
    )


def test_merge_prefers_the_longer_span_at_equal_score() -> None:
    short = _span(0, 4, EntityType.PERSON, 0.9)
    long = _span(0, 9, EntityType.ORG, 0.9)
    assert merge_spans([short, long]) == [long]


def test_merge_prefers_the_higher_score_even_when_shorter() -> None:
    short = _span(0, 4, EntityType.PERSON, 0.95)
    long = _span(0, 9, EntityType.ORG, 0.9)
    assert merge_spans([short, long]) == [short]


def test_merge_prefers_person_for_an_identical_span() -> None:
    person = _span(0, 5, EntityType.PERSON, 0.9)
    org = _span(0, 5, EntityType.ORG, 0.9)
    assert merge_spans([person, org]) == [person]


def test_merge_is_deterministic_for_every_input_order() -> None:
    spans = [
        _span(0, 6, EntityType.PERSON, 0.85),
        _span(2, 8, EntityType.ORG, 0.85),
        _span(4, 12, EntityType.ADDRESS_STREET, 0.9),
        _span(20, 24, EntityType.EMAIL, 0.9),
    ]
    expected = merge_spans(spans)
    for permutation in itertools.permutations(spans):
        assert merge_spans(permutation) == expected


def test_merge_keeps_adjacent_spans_and_sorts_by_position() -> None:
    first = _span(10, 20, EntityType.PHONE, 0.8)
    second = _span(0, 5, EntityType.PERSON, 0.8)
    assert merge_spans([first, second]) == [second, first]


def _random_span() -> st.SearchStrategy[Span]:
    return st.integers(min_value=0, max_value=100).flatmap(
        lambda start: st.builds(
            _span,
            start=st.just(start),
            end=st.integers(min_value=start + 1, max_value=110),
            entity_type=st.sampled_from(list(EntityType)),
            score=st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
        )
    )


@hypothesis_settings(max_examples=50, deadline=None)
@given(spans=st.lists(_random_span(), max_size=12))
def test_merge_output_never_overlaps(spans: list[Span]) -> None:
    merged = merge_spans(spans)
    for left, right in itertools.pairwise(merged):
        assert left.end <= right.start
    assert all(span in spans for span in merged)


_ALPHABET = st.sampled_from(list("abcäöüßÄÖÜ0123456789 .,;:/@-+()\n"))


@hypothesis_settings(
    max_examples=25,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)
@given(text=st.text(alphabet=_ALPHABET, max_size=120))
def test_offsets_property_for_arbitrary_text(text: str) -> None:
    for span in detect(text):
        assert text[span.start : span.end] == span.text


def test_dev_corpus_gold_spans_are_all_found_and_offsets_hold() -> None:
    """The dev split is the tuning input; every gold span is detected there.

    This is a dev regression gate, not an evaluation number: the test split is
    untouched until the evaluation task runs it once.
    """

    path = Path(__file__).resolve().parents[1] / "data" / "synthetic" / "dev.jsonl"
    records = [
        Enquiry.model_validate(json.loads(line))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(records) == 80
    for record in records:
        spans = detect(record.text)
        for span in spans:
            assert record.text[span.start : span.end] == span.text
        for left, right in itertools.pairwise(spans):
            assert left.end <= right.start
        gold = {(span.text, span.type) for span in record.gold_spans}
        found = {(span.text, span.type) for span in spans}
        assert gold <= found, (record.id, gold - found)


def test_detect_needs_no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    detect_module._analyzer.cache_clear()

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("detect opened a network socket")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    text = (
        "Mail an max.mustermann@example.de, IBAN DE89 3704 0044 0532 0130 00, "
        "siehe https://example.de/kontakt"
    )
    types = {span.type for span in detect(text)}
    assert {EntityType.EMAIL, EntityType.IBAN, EntityType.URL} <= types

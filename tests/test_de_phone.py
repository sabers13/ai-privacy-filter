"""German phone recognizer: positive and negative cases."""

from __future__ import annotations

import pytest
from support import balanced, strict, texts

from ai_privacy_filter.recognizers.de_phone import DePhoneRecognizer

BALANCED = DePhoneRecognizer(balanced())
STRICT = DePhoneRecognizer(strict())


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Rufen Sie +49 30 1234 5678 an.", "+49 30 1234 5678"),
        ("Erreichbar unter +49 (0) 30 1234-567.", "+49 (0) 30 1234-567"),
        ("Tel. 030/12345678", "030/12345678"),
        ("Tel. (030) 1234567", "(030) 1234567"),
        ("Mobil: 0170 123 45 67", "0170 123 45 67"),
        ("Fax 0049 30 1234567", "0049 30 1234567"),
        ("Nummer: 0 30 12345678", "0 30 12345678"),
        ("Festnetz 098 / 58 42 94 20", "098 / 58 42 94 20"),
        ("Festnetz 040 / 52 09 09 07", "040 / 52 09 09 07"),
    ],
)
def test_positive(text: str, expected: str) -> None:
    assert texts(BALANCED, text) == [expected]


@pytest.mark.parametrize(
    "text",
    [
        "Rechnung vom 12.03.2025.",
        "PLZ 10115 Berlin",
        "IBAN DE89 3704 0044 0532 0130 00",
        "USt-IdNr. DE 012 345 678",
        "Bestellnummer 1234567890",
        "Artikel 4711",
        "Rufnummer 030",
    ],
)
def test_negative(text: str) -> None:
    assert texts(BALANCED, text) == []


def test_strict_accepts_a_shorter_number() -> None:
    assert texts(BALANCED, "Tel. 030 123") == []
    assert texts(STRICT, "Tel. 030 123") == ["030 123"]


def test_international_number_scores_higher() -> None:
    international = BALANCED.analyze("+49 30 1234 5678", [])[0]
    national = BALANCED.analyze("030 12345678", [])[0]
    assert international.score > national.score


def test_ignores_other_entities() -> None:
    assert BALANCED.analyze("Tel. 030 12345678", ["IBAN"]) == []

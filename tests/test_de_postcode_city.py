"""Postcode-plus-city recognizer: range checks and capitalisation."""

from __future__ import annotations

import pytest
from support import balanced, texts

from ai_privacy_filter.recognizers.de_postcode_city import DePostcodeCityRecognizer

RECOGNIZER = DePostcodeCityRecognizer(balanced())


@pytest.mark.parametrize(
    "text, expected",
    [
        ("10115 Berlin", "10115 Berlin"),
        ("20259 Hamburg", "20259 Hamburg"),
        ("01067 Dresden", "01067 Dresden"),
        ("61348 Bad Homburg", "61348 Bad Homburg"),
        ("77114 Neunburg vorm Wald", "77114 Neunburg vorm Wald"),
        ("15560 Osterode am Harz", "15560 Osterode am Harz"),
        ("10115 Berlin-Mitte", "10115 Berlin-Mitte"),
        ("Adresse: 80331 München", "80331 München"),
    ],
)
def test_positive(text: str, expected: str) -> None:
    assert texts(RECOGNIZER, text) == [expected]


@pytest.mark.parametrize(
    "text",
    [
        "10115",
        "1011 Berlin",
        "10115 berlin",
        "00000 Berlin",
        "Artikel 1234 Berlin",
        "101159 Berlin",
    ],
)
def test_negative(text: str) -> None:
    assert texts(RECOGNIZER, text) == []

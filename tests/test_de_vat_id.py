"""German VAT ID recognizer: nine digits after the DE prefix."""

from __future__ import annotations

import pytest
from support import balanced, texts

from ai_privacy_filter.recognizers.de_vat_id import DeVatIdRecognizer

RECOGNIZER = DeVatIdRecognizer(balanced())


@pytest.mark.parametrize(
    "text, expected",
    [
        ("USt-IdNr.: DE123456789", "DE123456789"),
        ("Umsatzsteuer-ID DE 123 456 789", "DE 123 456 789"),
        ("VAT: de123456789", "de123456789"),
        ("Steuernummer DE123/456/789", "DE123/456/789"),
    ],
)
def test_positive(text: str, expected: str) -> None:
    assert texts(RECOGNIZER, text) == [expected]


@pytest.mark.parametrize(
    "text",
    [
        "DE12345678",
        "DE1234567890",
        "ATU12345678",
        "USt-IdNr.: DE 1234 5678",
        "Kundennummer: 123456789",
    ],
)
def test_negative(text: str) -> None:
    assert texts(RECOGNIZER, text) == []

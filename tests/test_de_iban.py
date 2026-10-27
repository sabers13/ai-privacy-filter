"""IBAN recognizer: checksum, separators and the profile switch."""

from __future__ import annotations

import pytest
from support import balanced, strict, texts

from ai_privacy_filter.recognizers.de_iban import (
    DeIbanRecognizer,
    checksum_ok,
    compact_iban,
)

BALANCED = DeIbanRecognizer(balanced())
STRICT = DeIbanRecognizer(strict())

DE_IBAN = "DE89 3704 0044 0532 0130 00"
DE_IBAN_COMPACT = "DE89370400440532013000"


@pytest.mark.parametrize(
    "text, expected",
    [
        (f"IBAN: {DE_IBAN}", DE_IBAN),
        (f"IBAN: {DE_IBAN_COMPACT}", DE_IBAN_COMPACT),
        ("Konto: DE44 5001 0517 5407 3249 31", "DE44 5001 0517 5407 3249 31"),
        ("IBAN DE12 9946 2200\n5881 9565 87", "DE12 9946 2200\n5881 9565 87"),
        ("IBAN GB82 WEST 1234 5698 7654 32", "GB82 WEST 1234 5698 7654 32"),
        ("iban de89370400440532013000", "de89370400440532013000"),
    ],
)
def test_positive(text: str, expected: str) -> None:
    assert texts(BALANCED, text) == [expected]


@pytest.mark.parametrize(
    "text",
    [
        "IBAN: DE89 3704 0044 0532 0130 01",
        "IBAN: DE89 3704 0044 0532 0130 0",
        "IBAN: AT12 3456 7890 1234 5678",
        "Kundennummer: DE8937040044053201300",
        "Bestellnummer 1234567890",
    ],
)
def test_negative(text: str) -> None:
    assert texts(BALANCED, text) == []


def test_strict_accepts_other_separators() -> None:
    hyphenated = "IBAN DE89-3704-0044-0532-0130-00"
    assert texts(BALANCED, hyphenated) == []
    assert texts(STRICT, hyphenated) == ["DE89-3704-0044-0532-0130-00"]


def test_checksum_and_normalisation() -> None:
    assert compact_iban(DE_IBAN) == DE_IBAN_COMPACT
    assert checksum_ok(DE_IBAN)
    assert not checksum_ok("DE89370400440532013001")
    assert not checksum_ok("DE89")

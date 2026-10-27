"""Date-of-birth recognizer: context required, calendars checked."""

from __future__ import annotations

import pytest
from support import balanced, texts

from ai_privacy_filter.recognizers.de_dob import DeDateOfBirthRecognizer

RECOGNIZER = DeDateOfBirthRecognizer(balanced())


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Geburtsdatum: 12.03.1985", "12.03.1985"),
        ("Ich bin geboren am 03.05.1990 in Leipzig.", "03.05.1990"),
        ("Geb. 03.05.90.", "03.05.90"),
        ("Geburtstag: 1. Januar 1990", "1. Januar 1990"),
        ("geb. 1985-03-12", "1985-03-12"),
        ("Mein Geburtsjahr: 1974", None),
    ],
)
def test_positive_only_when_a_birth_cue_is_near(text: str, expected: str | None) -> None:
    found = texts(RECOGNIZER, text)
    if expected is None:
        assert found == []
    else:
        assert found == [expected]


@pytest.mark.parametrize(
    "text",
    [
        "Rechnung vom 12.03.2025.",
        "Lieferung am 15.04.2025.",
        "Termin: 1. Januar 2026",
        "geb. am 31.02.1990",
        "geb. 12.13.1990",
        "geb. 12.03.1885",
    ],
)
def test_negative(text: str) -> None:
    assert texts(RECOGNIZER, text) == []


def test_context_must_be_close() -> None:
    text = "Geburtsdatum" + (" x" * 60) + " 12.03.1985"
    assert texts(RECOGNIZER, text) == []

"""First-name gazetteer: a pair or a cue in balanced, anything in strict."""

from __future__ import annotations

import pytest
from support import balanced, strict, texts

from ai_privacy_filter.recognizers.first_names import FIRST_NAMES, FirstNameRecognizer

BALANCED = FirstNameRecognizer(balanced())
STRICT = FirstNameRecognizer(strict())


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Anna Schmidt hat geschrieben.", ["Anna Schmidt"]),
        ("Ich heiße Julia und warte.", ["Julia"]),
        ("Hallo Petra,", ["Petra"]),
    ],
)
def test_positive_balanced(text: str, expected: list[str]) -> None:
    assert texts(BALANCED, text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "Julia kommt später.",
        "Mai 2025",
        "Mark und Bill",
        "Der Kontakt ist gut.",
    ],
)
def test_negative_balanced(text: str) -> None:
    assert texts(BALANCED, text) == []


def test_strict_accepts_a_lone_first_name() -> None:
    assert texts(STRICT, "Julia kommt später.") == ["Julia"]


def test_months_are_not_names() -> None:
    assert "mai" not in FIRST_NAMES
    assert "august" in FIRST_NAMES  # in the list, but excluded by the stoplist
    assert texts(STRICT, "August 2025") == []

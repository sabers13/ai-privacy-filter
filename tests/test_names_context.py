"""Names introduced by a salutation, a title, a signature or a form label."""

from __future__ import annotations

import pytest
from support import balanced, texts

from ai_privacy_filter.recognizers.names_context import NamesContextRecognizer

RECOGNIZER = NamesContextRecognizer(balanced())


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Herr Koch hat angerufen.", ["Herr Koch"]),
        ("Frau Weber hat angerufen.", ["Frau Weber"]),
        ("Sehr geehrter Herr Dr. Martin Koch,", ["Herr Dr. Martin Koch"]),
        ("Dr. Anna Schmidt aus Ulm", ["Dr. Anna Schmidt"]),
        ("Dr. Müller", ["Dr. Müller"]),
        ("Prof. Dr. med. Peter Meier", ["Prof. Dr. med. Peter Meier"]),
        ("Univ.Prof. Zdravko Keudel MBA.", ["Univ.Prof. Zdravko Keudel MBA."]),
        (
            "Herr Dipl.-Ing. Tassilo Pieper B.Eng.",
            ["Herr Dipl.-Ing. Tassilo Pieper B.Eng."],
        ),
        ("Mit freundlichen Grüßen\nSabine Weber", ["Sabine Weber"]),
        ("Viele Grüße\n\nJulia Becker", ["Julia Becker"]),
        ("Mit freundlichen Grüßen\nDr. Helma Keudel", ["Dr. Helma Keudel"]),
        ("Mein Name ist Julia Becker.", ["Julia Becker"]),
        ("Ich heiße Petra.", ["Petra"]),
        ("Name: Max Mustermann", ["Max Mustermann"]),
        ("Name: Frau Janna Schenk", ["Frau Janna Schenk"]),
        ("Ansprechpartner: Klaus Müller", ["Klaus Müller"]),
        ("Von: Romana Wieloch MBA.", ["Romana Wieloch MBA."]),
        ("An: Herrn Nikolaus Cichorius", ["Herrn Nikolaus Cichorius"]),
    ],
)
def test_positive(text: str, expected: list[str]) -> None:
    assert texts(RECOGNIZER, text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "Die Bäckerei Weber GmbH",
        "Mit freundlichen Grüßen\nDie Geschäftsführung",
        "Sehr geehrte Damen und Herren",
        "Name: bericht.pdf",
        "Rechnung bitte an:\nHildegard-Juncken-Ring 8",
    ],
)
def test_negative(text: str) -> None:
    assert texts(RECOGNIZER, text) == []


def test_a_salutation_without_a_name_is_not_a_person() -> None:
    assert texts(RECOGNIZER, "Herr und Frau Weber") == ["Frau Weber"]

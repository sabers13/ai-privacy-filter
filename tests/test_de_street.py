"""Street-plus-number recognizer: suffixes, compounds and negatives."""

from __future__ import annotations

import pytest
from support import balanced, texts

from ai_privacy_filter.recognizers.de_street import DeStreetRecognizer

RECOGNIZER = DeStreetRecognizer(balanced())


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Berliner Straße 5", "Berliner Straße 5"),
        ("Dussen vanstr. 80/40", "Dussen vanstr. 80/40"),
        ("Karl Heinz-Hörle-Platz 89/15", "Karl Heinz-Hörle-Platz 89/15"),
        ("Hauptstraße 12a", "Hauptstraße 12a"),
        ("Lindenallee 3", "Lindenallee 3"),
        ("Bahnhofstr. 7", "Bahnhofstr. 7"),
        ("Hauptstrasse 9", "Hauptstrasse 9"),
        ("Gartenweg 3-5", "Gartenweg 3-5"),
        ("Anka-Freudenberger-Platz 84/81", "Anka-Freudenberger-Platz 84/81"),
        ("Hans-Theo-Bähr-Straße 102", "Hans-Theo-Bähr-Straße 102"),
        ("Am Marktplatz 7", "Marktplatz 7"),
        ("Wohnhaft in der Schillerstraße 12.", "Schillerstraße 12"),
    ],
)
def test_positive(text: str, expected: str) -> None:
    assert texts(RECOGNIZER, text) == [expected]


@pytest.mark.parametrize(
    "text",
    [
        "Berliner Straße",
        "Straße 5",
        "Der Weg ist weit.",
        "Wir treffen uns am Ring.",
        "Hauptstraße ohne Nummer",
    ],
)
def test_negative(text: str) -> None:
    assert texts(RECOGNIZER, text) == []

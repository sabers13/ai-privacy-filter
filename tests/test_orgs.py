"""Company names anchored on a German legal form."""

from __future__ import annotations

import pytest
from support import balanced, texts

from ai_privacy_filter.recognizers.orgs import OrgLegalFormRecognizer

RECOGNIZER = OrgLegalFormRecognizer(balanced())


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Bäckerei Weber GmbH", "Bäckerei Weber GmbH"),
        ("Die Bäckerei Weber GmbH liefert.", "Bäckerei Weber GmbH"),
        ("Kontakt: Bäckerei Weber GmbH", "Bäckerei Weber GmbH"),
        ("Weber GmbH & Co. KG", "Weber GmbH & Co. KG"),
        ("Gute GmbH & Co. KGaA", "Gute GmbH & Co. KGaA"),
        ("Koch und Partner GmbH", "Koch und Partner GmbH"),
        ("Müller & Söhne GmbH", "Müller & Söhne GmbH"),
        ("Müller e.K.", "Müller e.K."),
        ("Sportverein Eintracht e.V.", "Sportverein Eintracht e.V."),
        ("SAP SE", "SAP SE"),
        ("Die Weber AG", "Weber AG"),
    ],
)
def test_positive(text: str, expected: str) -> None:
    assert texts(RECOGNIZER, text) == [expected]


@pytest.mark.parametrize(
    "text",
    [
        "GmbH",
        "Die GmbH",
        "Wir sind eine GmbH.",
        "Weber",
        "Die Geschäftsführung",
    ],
)
def test_negative(text: str) -> None:
    assert texts(RECOGNIZER, text) == []

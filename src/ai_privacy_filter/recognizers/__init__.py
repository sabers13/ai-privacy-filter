"""Recognizer registry for the German detection engine.

``build_recognizers`` returns the full set used by
:func:`ai_privacy_filter.detect.detect`: spaCy's German NER for person and
organisation names, Presidio's email and URL patterns, and the German pattern
recognizers in this package.
"""

from __future__ import annotations

from presidio_analyzer import EntityRecognizer
from presidio_analyzer.predefined_recognizers import SpacyRecognizer

from ai_privacy_filter.recognizers.base import (
    GermanRecognizer,
    Profile,
    Settings,
    settings_for,
)
from ai_privacy_filter.recognizers.builtins import DeEmailRecognizer, build_url_recognizer
from ai_privacy_filter.recognizers.de_dob import DeDateOfBirthRecognizer
from ai_privacy_filter.recognizers.de_iban import DeIbanRecognizer
from ai_privacy_filter.recognizers.de_phone import DePhoneRecognizer
from ai_privacy_filter.recognizers.de_postcode_city import DePostcodeCityRecognizer
from ai_privacy_filter.recognizers.de_street import DeStreetRecognizer
from ai_privacy_filter.recognizers.de_vat_id import DeVatIdRecognizer
from ai_privacy_filter.recognizers.first_names import FirstNameRecognizer
from ai_privacy_filter.recognizers.names_context import NamesContextRecognizer
from ai_privacy_filter.recognizers.orgs import OrgLegalFormRecognizer

__all__ = [
    "ENTITY_NAMES",
    "GermanRecognizer",
    "Profile",
    "Settings",
    "build_recognizers",
    "settings_for",
]

ENTITY_NAMES: tuple[str, ...] = (
    "PERSON",
    "ORGANIZATION",
    "ORG",
    "EMAIL",
    "URL",
    "PHONE",
    "IBAN",
    "ADDRESS_STREET",
    "POSTCODE_CITY",
    "VAT_ID",
    "DATE_OF_BIRTH",
)
"""Entity names requested from Presidio.

``ORGANIZATION`` is Presidio's name for the spaCy ``ORG`` label; detection maps
it to :attr:`EntityType.ORG`.
"""


def build_recognizers(settings: Settings) -> list[EntityRecognizer]:
    """Return every recognizer for ``settings``.

    spaCy's NER recognizer reports its own score for a person or organisation;
    the custom recognizers below use the shared settings bag.
    """

    return [
        SpacyRecognizer(
            supported_language="de",
            supported_entities=["PERSON", "ORGANIZATION"],
        ),
        DeEmailRecognizer(),
        build_url_recognizer(),
        DePhoneRecognizer(settings),
        DeIbanRecognizer(settings),
        DePostcodeCityRecognizer(settings),
        DeStreetRecognizer(settings),
        DeVatIdRecognizer(settings),
        DeDateOfBirthRecognizer(settings),
        NamesContextRecognizer(settings),
        FirstNameRecognizer(settings),
        OrgLegalFormRecognizer(settings),
    ]

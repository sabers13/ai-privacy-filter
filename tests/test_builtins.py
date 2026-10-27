"""Presidio's email and URL recognizers, with an offline email check."""

from __future__ import annotations

import tldextract
from presidio_analyzer.predefined_recognizers import UrlRecognizer
from support import texts

from ai_privacy_filter.recognizers.builtins import (
    DeEmailRecognizer,
    _valid_email,
    build_url_recognizer,
)

EMAIL = DeEmailRecognizer()
URL = build_url_recognizer()


def test_email_positive() -> None:
    text = "Schreiben Sie an max.mustermann@example.de."
    assert texts(EMAIL, text) == ["max.mustermann@example.de"]


def test_email_negative() -> None:
    assert texts(EMAIL, "Keine Adresse, nur Text.") == []


def test_email_validation_is_structural() -> None:
    assert _valid_email("a@b.de")
    assert _valid_email("first.last@sub.example.de")
    assert not _valid_email("a@b")
    assert not _valid_email("a@-b.de")
    assert not _valid_email("a@b.d")
    assert not _valid_email("nope")


def test_email_does_not_call_tldextract(monkeypatch) -> None:
    def boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("email validation must not touch tldextract")

    monkeypatch.setattr(tldextract, "extract", boom)
    assert texts(EMAIL, "Mail: max.mustermann@example.de") == ["max.mustermann@example.de"]


def test_url_positive() -> None:
    text = "Besuchen Sie https://example.de/kontakt oder example.de"
    assert texts(URL, text) == ["https://example.de/kontakt", "example.de"]


def test_url_on_a_reserved_domain_is_not_cut_short() -> None:
    text = "Angebot: https://www.beispiel.example/angebot"
    assert texts(URL, text) == ["https://www.beispiel.example/angebot"]


def test_academic_suffix_is_not_a_url() -> None:
    assert texts(URL, "Christa-Maria Knappe B.Sc.") == []


def test_title_fragment_is_not_a_url() -> None:
    assert texts(URL, "Univ.Prof. Zdravko Keudel") == []


def test_url_uses_presidio_patterns() -> None:
    assert isinstance(URL, UrlRecognizer)
    assert all(pattern.score >= 0.8 for pattern in URL.patterns)

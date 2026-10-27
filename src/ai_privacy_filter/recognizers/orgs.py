"""German company names ending in a legal form.

spaCy's German NER recognises some organisations but misses many short trade
names ("Bäckerei Weber GmbH"). The legal form (GmbH, AG, KG, UG, e.K., GbR,
e.V., mbH) is the reliable signal, so this recognizer anchors on it and walks
back over the capitalised words that make up the company name.

The walk stops at a line break, at a lower-case word and at a small list of
sentence words ("Die", "Wir", "Kontakt"), so a sentence subject is not pulled
into the span.
"""

from __future__ import annotations

import re

from presidio_analyzer import RecognizerResult
from presidio_analyzer.nlp_engine import NlpArtifacts

from ai_privacy_filter.recognizers.base import GermanRecognizer, Settings

ENTITY = "ORG"

MAX_NAME_WORDS = 5

_LEGAL = re.compile(
    r"(?<![\w])(?P<legal>"
    r"GmbH(?:\s*&\s*Co\.?\s*(?:KGaA|KG))?"
    r"|KGaA"
    r"|PartG\s*mbB"
    r"|UG(?:\s*\(haftungsbeschränkt\))?"
    r"|e\.\s?K\."
    r"|e\.\s?V\."
    r"|GbR"
    r"|OHG"
    r"|mbH"
    r"|AG"
    r"|KG"
    r"|SE"
    r")(?![\w])"
)

_CONNECTORS: frozenset[str] = frozenset({"und", "&", "+"})

_WORD_CHARS: frozenset[str] = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÜabcdefghijklmnopqrstuvwxyzäöüß0123456789&.'-"
)

# Capitalised words that start a sentence, not a company name.
_STOPWORDS: frozenset[str] = frozenset(
    {
        "der",
        "die",
        "das",
        "den",
        "dem",
        "des",
        "ein",
        "eine",
        "einer",
        "einem",
        "einen",
        "eines",
        "wir",
        "ich",
        "sie",
        "ihr",
        "ihre",
        "ihrem",
        "ihren",
        "unser",
        "unsere",
        "unserem",
        "unseren",
        "bei",
        "von",
        "vom",
        "zu",
        "zum",
        "zur",
        "an",
        "auf",
        "aus",
        "mit",
        "und",
        "oder",
        "für",
        "fuer",
        "kontakt",
        "firma",
        "ansprechpartner",
        "ansprechpartnerin",
        "sehr",
        "geehrte",
        "geehrter",
        "hallo",
        "guten",
        "tag",
        "betreff",
        "rechnung",
        "angebot",
        "bestellung",
        "kunde",
        "kundin",
        "lieferung",
        "zahlung",
        "grüße",
        "grüßen",
    }
)


class OrgLegalFormRecognizer(GermanRecognizer):
    """Recognise a company name that ends in a German legal form."""

    entity = ENTITY

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings, name="OrgLegalFormRecognizer", context_words=())

    def _name_start(self, text: str, legal_start: int) -> int | None:
        """Walk back over the company-name words before the legal form."""

        position = legal_start
        start: int | None = None
        for _ in range(MAX_NAME_WORDS):
            end = position
            while end > 0 and text[end - 1] in " \t":
                end -= 1
            if end == position or end == 0:
                # No word on the same line before the legal form.
                return start
            word_start = end
            while word_start > 0 and text[word_start - 1] in _WORD_CHARS:
                word_start -= 1
            word = text[word_start:end]
            if not word:
                return start
            if not word[0].isupper():
                # "Koch und Partner GmbH" and "Müller & Söhne GmbH" keep the
                # connector only when a name word sits in front of it.
                if word.casefold() in _CONNECTORS and word_start > 0:
                    position = word_start
                    continue
                return start
            if word.casefold() in _STOPWORDS:
                return start
            start = word_start
            position = word_start
        return start

    def analyze(
        self,
        text: str,
        entities: list[str],
        nlp_artifacts: NlpArtifacts | None = None,
    ) -> list[RecognizerResult]:
        """Return organisation spans built from a legal form and its name words."""

        if not self.accepts(entities):
            return []
        results: list[RecognizerResult] = []
        for match in _LEGAL.finditer(text):
            start = self._name_start(text, match.start())
            if start is None:
                continue
            results.append(
                self.result(
                    start,
                    match.end(),
                    0.9,
                    "German company name with a legal form",
                )
            )
        return results

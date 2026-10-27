"""German street names with a house number.

The suffix list follows the task brief: Straße/Strasse, Str., Weg, Allee,
Platz, Gasse, Ring and Damm, as a separate word (``Berliner Straße``) or as a
compound (``Hauptstraße``). A street without a house number is not an address
for this filter and is left alone.

The synthetic corpus has two extra shapes. A house number may carry a second
number ("84/81", "3-5"), and some street names have a capitalised word in
front of a compound ("Karl Heinz-Hörle-Platz", "Jacobi Jäckelallee"). One such
word is carried into the span unless it is an article or a preposition.
"""

from __future__ import annotations

import re

from presidio_analyzer import RecognizerResult
from presidio_analyzer.nlp_engine import NlpArtifacts

from ai_privacy_filter.recognizers.base import GermanRecognizer, Settings

ENTITY = "ADDRESS_STREET"

CONTEXT_WORDS = (
    "adresse",
    "anschrift",
    "straße",
    "strasse",
    "wohnhaft",
    "wohnt",
    "wohne",
    "lieferung",
    "lieferadresse",
    "rechnungsadresse",
)

_SUFFIX = (
    r"[Ss]traße"
    r"|[Ss]trasse"
    r"|[Ss]tr\."
    r"|[Ww]eg"
    r"|[Aa]llee"
    r"|[Pp]latz"
    r"|[Gg]asse"
    r"|[Rr]ing"
    r"|[Dd]amm"
)

_HOUSE_NUMBER = r"\s+\d{1,4}[A-Za-z]?(?:\s*[/\-\u2013]\s*\d{1,4})?"

_PATTERNS = (
    # "Berliner Straße 5", "Hauptstraße 12a"
    re.compile(
        rf"(?<![\w])(?P<street>[A-ZÄÖÜ][A-Za-zÄÖÜäöüß.-]*(?:\s+)?(?:{_SUFFIX})"
        rf"{_HOUSE_NUMBER})(?![\w])"
    ),
    # "Dussen vanstr. 80/40": a capitalised name, then a lower-case compound.
    re.compile(
        rf"(?<![\w])(?P<street>[A-ZÄÖÜ][A-Za-zÄÖÜäöüß.-]+[ \t]+[a-zäöüß]+"
        rf"(?:{_SUFFIX}){_HOUSE_NUMBER})(?![\w])"
    ),
)

# Words that cannot start a street name, so they are not carried into the span.
_PRECEDING_STOPWORDS: frozenset[str] = frozenset(
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
        "unser",
        "unsere",
        "bei",
        "von",
        "vom",
        "zu",
        "zum",
        "zur",
        "am",
        "an",
        "im",
        "in",
        "ob",
        "vorm",
        "vor",
        "auf",
        "aus",
        "mit",
        "und",
        "oder",
        "für",
        "fuer",
        "guten",
        "tag",
        "hallo",
        "adresse",
        "anschrift",
        "lieferung",
        "rechnung",
        "sehr",
        "geehrte",
        "geehrter",
    }
)
_WORD_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÜabcdefghijklmnopqrstuvwxyzäöüß.-")


class DeStreetRecognizer(GermanRecognizer):
    """Recognise a German street name followed by a house number."""

    entity = ENTITY

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings, name="DeStreetRecognizer", context_words=CONTEXT_WORDS)

    def analyze(
        self,
        text: str,
        entities: list[str],
        nlp_artifacts: NlpArtifacts | None = None,
    ) -> list[RecognizerResult]:
        """Return street-and-number spans."""

        if not self.accepts(entities):
            return []
        results: list[RecognizerResult] = []
        matches = [match for pattern in _PATTERNS for match in pattern.finditer(text)]
        for match in sorted(matches, key=lambda item: item.start()):
            start = self._with_preceding_word(text, match.start("street"))
            score = self.boosted(0.85, text, start, match.end(), boost=0.1)
            results.append(
                self.result(
                    start,
                    match.end("street"),
                    score,
                    "German street name with house number",
                )
            )
        return results

    def _with_preceding_word(self, text: str, start: int) -> int:
        """Include one capitalised word before a compound street name.

        The synthetic corpus builds some streets as "Karl Heinz-Hörle-Platz" or
        "Jacobi Jäckelallee". A preceding article or preposition is not carried
        in.
        """

        if start == 0 or text[start - 1] not in " \t":
            return start
        end = start - 1
        while end > 0 and text[end - 1] in " \t":
            end -= 1
        word_start = end
        while word_start > 0 and text[word_start - 1] in _WORD_CHARS:
            word_start -= 1
        word = text[word_start:end]
        if not word or not word[0].isupper() or word.casefold() in _PRECEDING_STOPWORDS:
            return start
        # Only a compound street name (suffix attached to the last word) takes
        # the preceding word; "Berliner Straße" already starts at its name.
        return word_start

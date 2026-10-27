"""German first names from Faker's ``de_DE`` provider.

The list is the one Faker uses to generate synthetic German names (MIT licence,
see ``THIRD_PARTY.md``). A first name alone is weak evidence — many are also
ordinary words — so the balanced profile only reports it next to a surname or a
name cue. The strict profile reports a lone first name too.
"""

from __future__ import annotations

import re

from faker.providers.person.de_DE import Provider as GermanPersonProvider
from presidio_analyzer import RecognizerResult
from presidio_analyzer.nlp_engine import NlpArtifacts

from ai_privacy_filter.recognizers.base import GermanRecognizer, Settings

ENTITY = "PERSON"

CONTEXT_WORDS = (
    "name",
    "vorname",
    "heiße",
    "heisse",
    "kontakt",
    "ansprechpartner",
    "ansprechpartnerin",
    "absender",
    "unterschrift",
    "hallo",
    "grüße",
    "grüssen",
    "grüßen",
)

FIRST_NAMES: frozenset[str] = frozenset(
    name.casefold() for name in GermanPersonProvider.first_names
)

# Months, weekdays and common homographs that are first names in the Faker list
# but far more often ordinary words. Balanced mode already needs a cue; this
# keeps the strict profile from redacting "Mai" and "Mark".
_NOT_NAMES: frozenset[str] = frozenset(
    {
        "mai",
        "juni",
        "juli",
        "august",
        "montag",
        "dienstag",
        "mittwoch",
        "donnerstag",
        "freitag",
        "samstag",
        "sonntag",
        "januar",
        "februar",
        "märz",
        "april",
        "september",
        "oktober",
        "november",
        "dezember",
        "mark",
        "bill",
        "rose",
        "kai",
        "will",
        "jan",
        "tom",
        "tim",
        "ben",
        "max",
    }
)

_TOKEN = re.compile(r"(?<![\w])[A-ZÄÖÜ][a-zäöüß]+(?:-[A-ZÄÖÜ][a-zäöüß]+)*(?![\w])")
_SECOND_TOKEN = re.compile(r"[ \t]+[A-ZÄÖÜ][a-zäöüß]+(?:-[A-ZÄÖÜ][a-zäöüß]+)*")


class FirstNameRecognizer(GermanRecognizer):
    """Recognise German first names from the Faker gazetteer."""

    entity = ENTITY

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings, name="FirstNameRecognizer", context_words=CONTEXT_WORDS)

    def analyze(
        self,
        text: str,
        entities: list[str],
        nlp_artifacts: NlpArtifacts | None = None,
    ) -> list[RecognizerResult]:
        """Return first-name spans, using a neighbouring surname as evidence."""

        if not self.accepts(entities):
            return []
        results: list[RecognizerResult] = []
        for match in _TOKEN.finditer(text):
            token = match.group(0)
            folded = token.casefold()
            if folded not in FIRST_NAMES or folded in _NOT_NAMES:
                continue
            second = _SECOND_TOKEN.match(text, match.end())
            if second is not None and second.group(0).split()[0].casefold() not in _NOT_NAMES:
                # "Anna Schmidt": the neighbouring capitalised word is treated
                # as a surname and part of the span.
                score = 0.7 if self.settings.allow_single_first_name else 0.65
                results.append(
                    self.result(
                        match.start(),
                        second.end(),
                        score,
                        "German first name followed by a capitalised surname",
                    )
                )
                continue
            if not self.settings.allow_single_first_name:
                if self.context_hit(text, match.start(), match.end()) is None:
                    continue
                score = 0.6
                explanation = "German first name near a name cue"
            else:
                score = 0.45
                explanation = "German first name from the gazetteer (strict profile)"
            results.append(self.result(match.start(), match.end(), score, explanation))
        return results

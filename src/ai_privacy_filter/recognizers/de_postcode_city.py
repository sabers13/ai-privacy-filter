"""German postcode plus city: five digits followed by a capitalised place name.

The postcode range is checked (01067 to 99998) so order numbers and article
numbers do not pass. The city part is one to three capitalised words, which
covers ``Berlin``, ``Bad Homburg`` and ``Berlin-Mitte`` without swallowing the
next sentence.
"""

from __future__ import annotations

import re

from presidio_analyzer import RecognizerResult
from presidio_analyzer.nlp_engine import NlpArtifacts

from ai_privacy_filter.recognizers.base import GermanRecognizer, Settings

ENTITY = "POSTCODE_CITY"

CONTEXT_WORDS = (
    "plz",
    "postleitzahl",
    "adresse",
    "anschrift",
    "wohnhaft",
    "wohnt",
    "wohne",
    "ansässig",
    "ansaessig",
    "sitz",
)

MIN_POSTCODE = 1067
MAX_POSTCODE = 99998

_CITY_WORD = r"[A-ZÄÖÜ][a-zäöüß]+"
_CITY_CONNECTOR = r"(?:am|an|der|im|vorm|vor|bei|ob|a\.\s?d\.)"
_PATTERN = re.compile(
    rf"(?<!\d)(?P<plz>\d{{5}})[ \t]+"
    rf"(?P<city>{_CITY_WORD}(?:[ -](?:{_CITY_WORD}|{_CITY_CONNECTOR})){{0,3}})(?![\w])"
)


class DePostcodeCityRecognizer(GermanRecognizer):
    """Recognise a German five-digit postcode with its city."""

    entity = ENTITY

    def __init__(self, settings: Settings) -> None:
        super().__init__(
            settings,
            name="DePostcodeCityRecognizer",
            context_words=CONTEXT_WORDS,
        )

    def analyze(
        self,
        text: str,
        entities: list[str],
        nlp_artifacts: NlpArtifacts | None = None,
    ) -> list[RecognizerResult]:
        """Return postcode-and-city spans inside the German postcode range."""

        if not self.accepts(entities):
            return []
        results: list[RecognizerResult] = []
        for match in _PATTERN.finditer(text):
            postcode = int(match.group("plz"))
            if not MIN_POSTCODE <= postcode <= MAX_POSTCODE:
                continue
            score = self.boosted(0.8, text, match.start(), match.end(), boost=0.1)
            results.append(
                self.result(
                    match.start(),
                    match.end(),
                    score,
                    f"German postcode and city ({postcode:05d})",
                )
            )
        return results

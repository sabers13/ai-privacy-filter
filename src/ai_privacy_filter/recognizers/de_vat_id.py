"""German VAT ID (Umsatzsteuer-Identifikationsnummer): ``DE`` plus nine digits.

Separators between digit groups are tolerated because people write
``DE 123 456 789``. Exactly nine digits are required, so a ten-digit number or
a short code does not pass.
"""

from __future__ import annotations

import re

from presidio_analyzer import RecognizerResult
from presidio_analyzer.nlp_engine import NlpArtifacts

from ai_privacy_filter.recognizers.base import GermanRecognizer, Settings

ENTITY = "VAT_ID"

CONTEXT_WORDS = (
    "ust",
    "ust-id",
    "ust-idnr",
    "ust.-idnr",
    "umsatzsteuer",
    "umsatzsteuer-id",
    "vat",
    "steuer",
    "steuernummer",
)

_PATTERN = re.compile(r"(?<![\w])(?P<vat>(?i:de)[\s./-]?\d(?:[\s./-]?\d){8})(?![\d])")


class DeVatIdRecognizer(GermanRecognizer):
    """Recognise a German VAT ID with exactly nine digits after the prefix."""

    entity = ENTITY

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings, name="DeVatIdRecognizer", context_words=CONTEXT_WORDS)

    def analyze(
        self,
        text: str,
        entities: list[str],
        nlp_artifacts: NlpArtifacts | None = None,
    ) -> list[RecognizerResult]:
        """Return VAT ID spans."""

        if not self.accepts(entities):
            return []
        results: list[RecognizerResult] = []
        for match in _PATTERN.finditer(text):
            score = self.boosted(0.8, text, match.start(), match.end(), boost=0.15)
            results.append(
                self.result(
                    match.start("vat"),
                    match.end("vat"),
                    score,
                    "German VAT ID (DE plus nine digits)",
                )
            )
        return results

"""German phone numbers.

Covers the forms a German enquiry actually uses: ``+49`` and ``0049``
international prefixes, national ``0``-prefixed numbers, spaces, slashes and
hyphens between groups, and an area code in parentheses.

Dates are the main look-alike, so ``.`` is deliberately not a separator: with
dots allowed, ``12.03.1985`` would read as a phone number. Anything that
survives the pattern is normalised and checked for a plausible number of
significant digits.
"""

from __future__ import annotations

import re

from presidio_analyzer import RecognizerResult
from presidio_analyzer.nlp_engine import NlpArtifacts

from ai_privacy_filter.recognizers.base import GermanRecognizer, Settings

ENTITY = "PHONE"

CONTEXT_WORDS = (
    "telefon",
    "telefonisch",
    "tel.",
    "handy",
    "mobil",
    "mobilfunk",
    "fax",
    "rufnummer",
    "nummer",
    "erreichbar",
    "anrufen",
    "kontakt",
)

_SEP = r"[\s/-]"
_AREA = r"\d{1,5}"
_SUBSCRIBER = r"\d(?:[\s/-]?\d){2,}"

PATTERN = re.compile(
    r"(?<![\w+])"
    r"(?P<number>"
    rf"(?:\+49|0049)(?:{_SEP}*\(\s*0\s*\))?{_SEP}*{_AREA}{_SEP}*{_SUBSCRIBER}"
    r"|"
    rf"\(\s*0{_AREA}\s*\){_SEP}*{_SUBSCRIBER}"
    r"|"
    rf"0{_SEP}*{_AREA}{_SEP}*{_SUBSCRIBER}"
    r")"
    r"(?![\w])"
)

MAX_SIGNIFICANT_DIGITS = 12


def _significant_digits(raw: str) -> str | None:
    """Drop the country code and trunk zero, or return ``None`` when foreign."""

    digits = re.sub(r"\D", "", raw)
    international = raw.lstrip().startswith("+") or digits.startswith("0049")
    if international:
        digits = digits[4:] if digits.startswith("0049") else digits[2:]
        digits = digits[1:] if digits.startswith("0") else digits
        return digits or None
    if not digits.startswith("0"):
        return None
    return digits[1:] or None


class DePhoneRecognizer(GermanRecognizer):
    """Recognise German phone numbers with a plausible digit count."""

    entity = ENTITY

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings, name="DePhoneRecognizer", context_words=CONTEXT_WORDS)

    def analyze(
        self,
        text: str,
        entities: list[str],
        nlp_artifacts: NlpArtifacts | None = None,
    ) -> list[RecognizerResult]:
        """Return phone spans whose significant digits fit a German number."""

        if not self.accepts(entities):
            return []
        results: list[RecognizerResult] = []
        for match in PATTERN.finditer(text):
            raw = match.group("number")
            # A VAT ID such as "DE 012 345 678" is not a phone number.
            if text[max(0, match.start() - 4) : match.start()].strip().upper().endswith("DE"):
                continue
            significant = _significant_digits(raw)
            if significant is None:
                continue
            if not self.settings.phone_min_digits <= len(significant) <= MAX_SIGNIFICANT_DIGITS:
                continue
            international = raw.lstrip().startswith(("+49", "0049"))
            base = 0.9 if international else 0.78
            score = self.boosted(base, text, match.start(), match.end(), boost=0.1)
            pattern = "international" if international else "national"
            results.append(
                self.result(
                    match.start("number"),
                    match.end("number"),
                    score,
                    f"German phone number ({pattern}, {len(significant)} significant digits)",
                )
            )
        return results

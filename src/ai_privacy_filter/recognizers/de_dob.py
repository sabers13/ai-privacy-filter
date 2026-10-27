"""Dates of birth — only when the text also says it is a birth date.

A bare date is not personal data this filter needs: delivery dates, deadlines
and invoice dates are everyday business text. The recognizer therefore fires
only when a birth-date cue (``geb.``, ``geboren``, ``Geburtsdatum``,
``Geburtstag``) sits within the context window.

Three notations are covered: ``12.03.1985``, ``1985-03-12`` and
``12. März 1985``.
"""

from __future__ import annotations

import re
from datetime import date

from presidio_analyzer import RecognizerResult
from presidio_analyzer.nlp_engine import NlpArtifacts

from ai_privacy_filter.recognizers.base import GermanRecognizer, Settings

ENTITY = "DATE_OF_BIRTH"

CONTEXT_WORDS = (
    "geb.",
    "geboren",
    "geburtsdatum",
    "geburtstag",
    "geburtsjahr",
    "dob",
)

MONTHS = (
    "Januar",
    "Februar",
    "März",
    "April",
    "Mai",
    "Juni",
    "Juli",
    "August",
    "September",
    "Oktober",
    "November",
    "Dezember",
)

_NUMERIC = re.compile(
    r"(?<!\d)(?P<day>\d{1,2})[./](?P<month>\d{1,2})[./](?P<year>\d{4}|\d{2})(?!\d)"
)
_ISO = re.compile(r"(?<!\d)(?P<year>\d{4})-(?P<month>\d{1,2})-(?P<day>\d{1,2})(?!\d)")
_LONG = re.compile(
    rf"(?<!\d)(?P<day>\d{{1,2}})\.?\s*(?P<month>{'|'.join(MONTHS)})\s+(?P<year>\d{{4}})(?!\d)"
)


def _two_digit_year(two_digits: int) -> int:
    """Expand a two-digit year: 85 becomes 1985, 05 becomes 2005."""

    current = date.today().year % 100
    return 1900 + two_digits if two_digits > current else 2000 + two_digits


def _valid_date(day: int, month: int, year: int) -> bool:
    """Return whether ``day``/``month``/``year`` is a real, plausible birth date."""

    if not 1900 <= year <= date.today().year:
        return False
    try:
        date(year, month, day)
    except ValueError:
        return False
    return True


class DeDateOfBirthRecognizer(GermanRecognizer):
    """Recognise a date only when birth-date context is present."""

    entity = ENTITY

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings, name="DeDateOfBirthRecognizer", context_words=CONTEXT_WORDS)

    def analyze(
        self,
        text: str,
        entities: list[str],
        nlp_artifacts: NlpArtifacts | None = None,
    ) -> list[RecognizerResult]:
        """Return birth-date spans that pass the calendar checks."""

        if not self.accepts(entities):
            return []
        candidates: list[tuple[int, int, int, int, int]] = []
        for match in _NUMERIC.finditer(text):
            year_text = match.group("year")
            year = int(year_text)
            if len(year_text) == 2:
                year = _two_digit_year(year)
            candidates.append(
                (
                    match.start(),
                    match.end(),
                    int(match.group("day")),
                    int(match.group("month")),
                    year,
                )
            )
        for match in _ISO.finditer(text):
            candidates.append(
                (
                    match.start(),
                    match.end(),
                    int(match.group("day")),
                    int(match.group("month")),
                    int(match.group("year")),
                )
            )
        for match in _LONG.finditer(text):
            candidates.append(
                (
                    match.start(),
                    match.end(),
                    int(match.group("day")),
                    MONTHS.index(match.group("month")) + 1,
                    int(match.group("year")),
                )
            )

        results: list[RecognizerResult] = []
        seen: set[tuple[int, int]] = set()
        for start, end, day, month, year in candidates:
            if (start, end) in seen:
                continue
            seen.add((start, end))
            if not _valid_date(day, month, year):
                continue
            if self.context_hit(text, start, end) is None:
                continue
            score = self.boosted(0.85, text, start, end, boost=0.1)
            results.append(
                self.result(start, end, score, "German date of birth with birth-date context")
            )
        return results

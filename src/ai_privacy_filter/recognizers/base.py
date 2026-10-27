"""German recognizers and shared helpers for the Presidio analyzer.

Every recognizer here is local: compiled patterns or a bundled gazetteer, no
model download and no network. ``detect`` registers them with a Presidio
``AnalyzerEngine`` so results share one span shape and one merge step.

Context words are matched in the recognizers themselves instead of Presidio's
lemma-based enhancer. Two reasons: German inflection makes lemma matching hard
to predict from a test, and some entities (a date of birth, a first name) must
only fire when context is present, which a score boost cannot express.
"""

# mypy: disable-error-code="no-untyped-call"
# Presidio ships type hints but leaves several constructors unannotated. The
# calls below are audited by the unit tests; the rest of the package stays
# under strict mypy.

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import ClassVar, Literal

from presidio_analyzer import AnalysisExplanation, EntityRecognizer, RecognizerResult

Profile = Literal["strict", "balanced"]
"""Detection profile. ``strict`` favours recall, ``balanced`` accepts fewer false positives."""

DEFAULT_CONTEXT_WINDOW = 60
"""Characters searched on each side of a match for context words."""


@dataclass(frozen=True, slots=True)
class Settings:
    """Per-profile knobs shared by the recognizers.

    ``score_threshold`` filters results in the analyzer. ``phone_min_digits``
    counts significant digits, that is after dropping the country code and the
    national trunk zero. ``iban_separators`` lists the characters a formatted
    IBAN may contain between groups. ``allow_single_first_name`` lets the
    gazetteer fire on a lone first name, which only the strict profile wants.
    """

    profile: Profile = "balanced"
    score_threshold: float = 0.4
    phone_min_digits: int = 6
    iban_separators: str = " "
    allow_single_first_name: bool = False
    context_window: int = DEFAULT_CONTEXT_WINDOW


def settings_for(profile: Profile) -> Settings:
    """Return the settings for ``profile``.

    ``strict`` lowers the threshold, accepts shorter phone numbers, allows
    hyphen and dot separators inside IBANs and lets a lone first name through.
    """

    if profile == "strict":
        return Settings(
            profile="strict",
            score_threshold=0.25,
            phone_min_digits=5,
            iban_separators=" .-",
            allow_single_first_name=True,
        )
    if profile == "balanced":
        return Settings()
    raise ValueError(f"unknown detection profile: {profile!r}")


def compile_context(words: Sequence[str]) -> re.Pattern[str] | None:
    """Compile context words into one case-insensitive, word-anchored pattern.

    A word may grow suffixes ("telefon" also matches "Telefonnummer"), which
    keeps German compounds in reach without listing every inflection.
    """

    if not words:
        return None
    alternatives = "|".join(sorted((re.escape(word) for word in words), key=len, reverse=True))
    return re.compile(rf"(?<!\w)(?:{alternatives})\w*", re.IGNORECASE)


class GermanRecognizer(EntityRecognizer):
    """Base class for a single-entity German recognizer.

    Subclasses set :attr:`entity` and implement :meth:`analyze`. The base
    handles the Presidio wiring, the settings bag and context-word matching.
    """

    entity: ClassVar[str]

    def __init__(
        self,
        settings: Settings,
        *,
        name: str,
        context_words: Sequence[str] = (),
    ) -> None:
        super().__init__(
            supported_entities=[self.entity],
            name=name,
            supported_language="de",
        )
        self.settings = settings
        self.recognizer_name = name
        self._context = compile_context(context_words)

    def load(self) -> None:
        """Nothing to load: recognizers are patterns and in-memory data."""

    def accepts(self, entities: Sequence[str]) -> bool:
        """Return whether the caller asked for this recognizer's entity."""

        return not entities or self.entity in entities

    def context_hit(self, text: str, start: int, end: int) -> str | None:
        """Return the first context word near ``start``..``end``, if any."""

        if self._context is None:
            return None
        window = self.settings.context_window
        match = self._context.search(text, max(0, start - window), min(len(text), end + window))
        return match.group(0) if match else None

    def boosted(self, score: float, text: str, start: int, end: int, *, boost: float) -> float:
        """Add ``boost`` when a context word is nearby, capped at 1.0."""

        if self.context_hit(text, start, end) is None:
            return score
        return min(1.0, score + boost)

    def result(
        self,
        start: int,
        end: int,
        score: float,
        explanation: str,
    ) -> RecognizerResult:
        """Build a Presidio result tagged with this recognizer's name and id."""

        return RecognizerResult(
            entity_type=self.entity,
            start=start,
            end=end,
            score=max(0.0, min(1.0, score)),
            analysis_explanation=AnalysisExplanation(
                recognizer=self.recognizer_name,
                original_score=score,
                textual_explanation=explanation,
            ),
            recognition_metadata={
                RecognizerResult.RECOGNIZER_NAME_KEY: self.recognizer_name,
                RecognizerResult.RECOGNIZER_IDENTIFIER_KEY: self.id,
            },
        )

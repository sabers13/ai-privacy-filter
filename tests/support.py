"""Synthetic text and deterministic stand-ins shared by the T08 tests.

The detector stand-in provides fixed spans, so service and CLI tests do not load
the German model or depend on detection quality. The classifier calls the real
fake backend, so every test exercises the shipped backend and the frozen
``TRIAGE_SCHEMA``; the triage prompt itself is still a stub until T12. All text
here is synthetic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

from presidio_analyzer import EntityRecognizer, RecognizerResult

from ai_privacy_filter.backends.base import LLMBackend
from ai_privacy_filter.models import Category, EntityType, Span, TriageResult, Urgency
from ai_privacy_filter.pipeline import Detector, Pipeline
from ai_privacy_filter.recognizers.base import Settings, settings_for
from ai_privacy_filter.triage import TRIAGE_SCHEMA

TEXT: Final = (
    "Guten Tag, mein Name ist Erika Mustermann. "
    "Sie erreicht mich unter erika.mustermann@example.org oder 0170 1234567."
)

ENTITIES: Final[tuple[tuple[str, EntityType], ...]] = (
    ("Erika Mustermann", EntityType.PERSON),
    ("erika.mustermann@example.org", EntityType.EMAIL),
    ("0170 1234567", EntityType.PHONE),
)


def detect_entities(text: str) -> list[Span]:
    """Return one span per known synthetic entity found in ``text``."""
    spans: list[Span] = []
    for needle, entity_type in ENTITIES:
        start = text.find(needle)
        if start < 0:
            continue
        spans.append(
            Span(
                start=start,
                end=start + len(needle),
                type=entity_type,
                text=needle,
                score=0.99,
                source="test-detector",
            )
        )
    return spans


def first_occurrence_only(text: str) -> list[Span]:
    """Detect entities but leave every repeat in place, to trip the guard."""
    return [span for span in detect_entities(text) if text.find(span.text) == span.start]


class SpyClassifier:
    """Deterministic classifier stand-in that records what it was asked to send."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, text: str, backend: LLMBackend) -> TriageResult:
        self.calls.append(text)
        result = backend.complete_json(f"Klassifiziere diese Anfrage:\n{text}", TRIAGE_SCHEMA, 30)
        parsed = result.parsed
        if result.error_kind is not None or parsed is None:
            raise AssertionError("the fake backend never fails")
        return TriageResult(
            category=Category(str(parsed["category"])),
            urgency=Urgency(str(parsed["urgency"])),
            reply_draft=str(parsed["reply_draft"]),
            backend=backend.name,
            model=result.model,
            latency_ms=result.latency_ms,
        )


class FakeClock:
    """Manually advanced monotonic clock, so expiry tests never sleep."""

    def __init__(self, now: float = 1_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@dataclass
class Harness:
    """A pipeline wired to the stand-ins, plus the spy for assertions."""

    pipeline: Pipeline
    classifier: SpyClassifier = field(default_factory=SpyClassifier)

    @classmethod
    def build(cls, detector: Detector = detect_entities) -> Harness:
        classifier = SpyClassifier()
        return cls(
            pipeline=Pipeline(detector=detector, classifier=classifier),
            classifier=classifier,
        )


# Helpers for the T02 recognizer tests.


def analyze(recognizer: EntityRecognizer, text: str) -> list[RecognizerResult]:
    """Run one recognizer over ``text`` without a full Presidio engine."""

    return recognizer.analyze(text, [])


def texts(recognizer: EntityRecognizer, text: str) -> list[str]:
    """Return the matched substrings of one recognizer."""

    return [text[result.start : result.end] for result in analyze(recognizer, text)]


def balanced() -> Settings:
    """Settings of the default profile."""

    return settings_for("balanced")


def strict() -> Settings:
    """Settings of the recall-first profile."""

    return settings_for("strict")

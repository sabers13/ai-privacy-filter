"""Personal-data detection.

The engine is Presidio's ``AnalyzerEngine`` with a German spaCy pipeline
(``de_core_news_lg``) and the local recognizers in
:mod:`ai_privacy_filter.recognizers`. Detection runs entirely on this machine:
no model download, no network call and no external service.

Two profiles exist. ``balanced`` is the default and aims at a low false-positive
rate for ambiguous evidence (a lone first name, a short phone number).
``strict`` lowers the threshold and accepts more shapes, trading over-masking
for recall. See :func:`settings_for` for the exact differences.
"""

# mypy: disable-error-code="no-untyped-call"
# Presidio ships type hints but leaves constructors such as AnalyzerEngine's
# without a return annotation. Their behaviour is covered by the detection
# tests; the rest of the module is strict.

from __future__ import annotations

from collections.abc import Iterable
from functools import lru_cache

from presidio_analyzer import AnalyzerEngine, RecognizerRegistry, RecognizerResult
from presidio_analyzer.nlp_engine import NlpEngine, NlpEngineProvider

from ai_privacy_filter.models import EntityType, Span
from ai_privacy_filter.recognizers import (
    ENTITY_NAMES,
    Profile,
    build_recognizers,
    settings_for,
)

LANGUAGE = "de"
SPACY_MODEL = "de_core_news_lg"

NLP_CONFIGURATION = {
    "nlp_engine_name": "spacy",
    "models": [{"lang_code": LANGUAGE, "model_name": SPACY_MODEL}],
    # MISC would only produce a "not mapped" warning; detection does not use it.
    "ner_model_configuration": {"labels_to_ignore": ["MISC"]},
}

_ENTITY_TYPES: dict[str, EntityType] = {
    "PERSON": EntityType.PERSON,
    "ORGANIZATION": EntityType.ORG,
    "ORG": EntityType.ORG,
    "EMAIL": EntityType.EMAIL,
    "EMAIL_ADDRESS": EntityType.EMAIL,
    "URL": EntityType.URL,
    "PHONE": EntityType.PHONE,
    "PHONE_NUMBER": EntityType.PHONE,
    "IBAN": EntityType.IBAN,
    "IBAN_CODE": EntityType.IBAN,
    "ADDRESS_STREET": EntityType.ADDRESS_STREET,
    "POSTCODE_CITY": EntityType.POSTCODE_CITY,
    "VAT_ID": EntityType.VAT_ID,
    "DATE_OF_BIRTH": EntityType.DATE_OF_BIRTH,
}

# For equal score and length, a more specific entity wins: an email beats the
# URL inside it, an IBAN beats a phone-like digit group, and a person mention
# with a salutation beats an organisation label on exactly the same text.
_TYPE_PRIORITY: dict[EntityType, int] = {
    entity: index
    for index, entity in enumerate(
        (
            EntityType.EMAIL,
            EntityType.IBAN,
            EntityType.URL,
            EntityType.VAT_ID,
            EntityType.PHONE,
            EntityType.ADDRESS_STREET,
            EntityType.POSTCODE_CITY,
            EntityType.DATE_OF_BIRTH,
            EntityType.PERSON,
            EntityType.ORG,
        )
    )
}


@lru_cache(maxsize=1)
def _nlp_engine() -> NlpEngine:
    """Return the shared German spaCy engine (one model load for both profiles)."""

    provider = NlpEngineProvider(nlp_configuration=NLP_CONFIGURATION)
    return provider.create_engine()


@lru_cache(maxsize=2)
def _analyzer(profile: Profile) -> AnalyzerEngine:
    """Build (once per profile) and return the Presidio analyzer."""

    settings = settings_for(profile)
    registry = RecognizerRegistry(supported_languages=[LANGUAGE])
    for recognizer in build_recognizers(settings):
        registry.add_recognizer(recognizer)
    return AnalyzerEngine(
        registry=registry,
        nlp_engine=_nlp_engine(),
        supported_languages=[LANGUAGE],
        default_score_threshold=settings.score_threshold,
    )


def merge_spans(spans: Iterable[Span]) -> list[Span]:
    """Merge overlapping spans deterministically.

    Spans are ranked by score, then by length, then by entity priority, start
    offset and source. The greedy pass keeps the best-ranked span and drops
    every span that overlaps one already kept. The result is sorted by position,
    so callers see entities in the order they appear in the text.
    """

    ranked = sorted(
        (span for span in spans if span.start < span.end),
        key=lambda span: (
            -span.score,
            -(span.end - span.start),
            _TYPE_PRIORITY[span.type],
            span.start,
            span.end,
            span.source,
        ),
    )
    kept: list[Span] = []
    for span in ranked:
        if any(span.start < other.end and other.start < span.end for other in kept):
            continue
        kept.append(span)
    return sorted(kept, key=lambda span: (span.start, span.end, span.type.value))


_GREETINGS = ("hallo ", "hi ", "hey ", "moin ", "guten tag ")

# Words that mark a forwarded-mail header, not a person name. spaCy tags
# "---------- Weitergeleitete Nachricht" as a person in forwarded mails.
_HEADER_WORDS = frozenset(
    {
        "weitergeleitete",
        "weitergeleiteter",
        "ursprüngliche",
        "ursprünglicher",
        "nachricht",
        "betreff",
        "gesendet",
        "datum",
    }
)


def _clean_bounds(text: str, start: int, end: int, entity: EntityType) -> tuple[int, int] | None:
    """Trim a span to its own line and drop NER output that is not a name.

    spaCy occasionally tags a URL, an email address or a forwarded-mail header
    as a person or an organisation, it sometimes includes the greeting word
    ("Hallo Konstantin"), and it sometimes runs a name across a line break in a
    form. None of that belongs in the span.
    """

    if entity in (EntityType.PERSON, EntityType.ORG):
        newline = text.find("\n", start, end)
        if newline != -1:
            end = newline
        while start < end and not text[start].isalnum():
            start += 1
        snippet = text[start:end]
        if entity is EntityType.PERSON:
            lowered = snippet.casefold()
            for greeting in _GREETINGS:
                if lowered.startswith(greeting):
                    start += len(greeting)
                    snippet = text[start:end]
                    break
            words = snippet.casefold().split()
            if any(word in _HEADER_WORDS for word in words[:2]):
                return None
        if "://" in snippet or "@" in snippet or snippet.casefold().startswith("www."):
            return None
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    if entity in (EntityType.EMAIL, EntityType.URL):
        # Sentence punctuation sits outside the link.
        while end > start and text[end - 1] in ".,;:!?":
            end -= 1
    if start >= end:
        return None
    return start, end


def _to_span(text: str, result: object) -> Span | None:
    """Convert one Presidio result into a contract :class:`Span`."""

    entity_type = getattr(result, "entity_type", None)
    start = getattr(result, "start", None)
    end = getattr(result, "end", None)
    score = getattr(result, "score", None)
    if not isinstance(entity_type, str) or not isinstance(start, int) or not isinstance(end, int):
        return None
    if not isinstance(score, (int, float)):
        return None
    entity = _ENTITY_TYPES.get(entity_type)
    if entity is None:
        return None
    if start < 0 or end > len(text) or start >= end:
        return None
    bounds = _clean_bounds(text, start, end, entity)
    if bounds is None:
        return None
    start, end = bounds
    metadata = getattr(result, "recognition_metadata", None) or {}
    source = (
        metadata.get(RecognizerResult.RECOGNIZER_NAME_KEY) if isinstance(metadata, dict) else None
    )
    if not isinstance(source, str) or not source:
        source = "presidio"
    return Span(
        start=start,
        end=end,
        type=entity,
        text=text[start:end],
        score=max(0.0, min(1.0, float(score))),
        source=source,
    )


def detect(text: str, *, profile: Profile = "balanced") -> list[Span]:
    """Return personal-data spans in ``text``.

    ``profile="strict"`` favours recall, ``profile="balanced"`` accepts fewer
    false positives. Detect is local: it must not touch the network.
    """

    settings = settings_for(profile)
    if not text:
        return []
    results = _analyzer(profile).analyze(
        text=text,
        language=LANGUAGE,
        entities=list(ENTITY_NAMES),
        score_threshold=settings.score_threshold,
    )
    spans = [span for result in results if (span := _to_span(text, result)) is not None]
    return merge_spans(spans)

"""The full pipeline: detect -> pseudonymise -> guard -> model -> restore.

The HTTP service and the CLI share this module instead of wiring the steps
themselves, so the guard cannot be skipped by a caller that forgets it.

Detection and triage are injected: :func:`default_detector` and
:func:`default_classifier` call T02's ``detect.detect`` and T06's
``triage.classify``, and tests pass deterministic stand-ins while those tasks are
still stubs. A stub upstream raises :class:`PipelineNotReadyError` with the task
that owns it, so the service can answer 501 instead of a traceback.

Privacy: the mapping produced by pseudonymisation stays in this object and in the
ticket store. Nothing here logs a value; only lengths, counts and entity types
are ever logged by callers.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Final

from pydantic import Field

from ai_privacy_filter import detect, triage
from ai_privacy_filter.backends.base import LLMBackend
from ai_privacy_filter.guard import require_clean
from ai_privacy_filter.models import ContractModel, EntityType, RedactionResult, Span, TriageResult
from ai_privacy_filter.pseudonymise import redact

Detector = Callable[[str], Sequence[Span]]
Classifier = Callable[[str, LLMBackend], TriageResult]

DETECTION_OWNER: Final = "detection lands with T02"
TRIAGE_OWNER: Final = "the triage pipeline lands with T06/T12"


class PipelineNotReadyError(RuntimeError):
    """Raised when a pipeline leg is still a stub that owns a later task."""


class ProcessResult(ContractModel):
    """Outcome of one full pipeline run.

    ``mapping`` is the in-memory placeholder mapping. It is excluded from
    ``model_dump`` and from ``repr`` so a response body or a log line cannot
    carry original values; the service stores it in a ticket instead.
    """

    triage: TriageResult
    redacted_text: str
    spans_summary: dict[str, int]
    mapping: dict[str, str] = Field(default_factory=dict, repr=False, exclude=True)


def summarise_spans(spans: Sequence[Span]) -> dict[str, int]:
    """Return entity-type counts in ``EntityType`` order, for logs and responses."""
    counts: dict[str, int] = {}
    for span in spans:
        counts[span.type.value] = counts.get(span.type.value, 0) + 1
    return {member.value: counts[member.value] for member in EntityType if member.value in counts}


def default_detector(text: str) -> Sequence[Span]:
    """Run the local detector. T02 owns the implementation."""
    return detect.detect(text)


def default_classifier(text: str, backend: LLMBackend) -> TriageResult:
    """Classify already-pseudonymised ``text``. T06/T12 own the implementation."""
    return triage.classify(text, backend)


class Pipeline:
    """Detect, pseudonymise, guard, classify, and keep the mapping in memory."""

    def __init__(
        self,
        *,
        detector: Detector | None = None,
        classifier: Classifier | None = None,
    ) -> None:
        self.detector: Detector = detector if detector is not None else default_detector
        self.classifier: Classifier = classifier if classifier is not None else default_classifier

    def redact_text(self, text: str) -> RedactionResult:
        """Detect personal data in ``text`` and replace it with placeholders."""
        return redact(text, self._detect(text))

    def process(self, text: str, backend: LLMBackend) -> ProcessResult:
        """Run the whole pipeline for one enquiry.

        The guard runs before the model call and raises
        :class:`ai_privacy_filter.guard.GuardBlockedError` instead of letting a
        leak reach ``backend``.
        """
        redaction = self.redact_text(text)
        require_clean(redaction.redacted_text, redaction.mapping)
        answer = self._classify(redaction.redacted_text, backend)
        return ProcessResult(
            triage=answer,
            redacted_text=redaction.redacted_text,
            spans_summary=summarise_spans(redaction.spans),
            mapping=dict(redaction.mapping),
        )

    def _detect(self, text: str) -> Sequence[Span]:
        try:
            return self.detector(text)
        except NotImplementedError as exc:
            raise PipelineNotReadyError(DETECTION_OWNER) from exc

    def _classify(self, text: str, backend: LLMBackend) -> TriageResult:
        try:
            return self.classifier(text, backend)
        except NotImplementedError as exc:
            raise PipelineNotReadyError(TRIAGE_OWNER) from exc


def default_pipeline() -> Pipeline:
    """Build the pipeline the service and the CLI use outside tests."""
    return Pipeline()

"""Contracts shared by every part of ai_privacy_filter.

The models in this module are the frozen interface between detection,
pseudonymisation, the guard, the LLM backends, the HTTP service and the page
export. They are intentionally small. Later tasks add behaviour, not shape.

Privacy notes
-------------
* :class:`RedactionResult.mapping` holds original values in memory only. The
  field is excluded from ``repr`` so an accidental log or traceback does not
  print personal data.
* :class:`GuardVerdict` reports entity *types* only, never leaked values.
* Nothing here writes to disk, a log or the network.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator


class ContractModel(BaseModel):
    """Base class for contract models: frozen, strict about unknown fields."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class EntityType(StrEnum):
    """Entity types the filter detects and pseudonymises."""

    PERSON = "PERSON"
    ORG = "ORG"
    EMAIL = "EMAIL"
    PHONE = "PHONE"
    IBAN = "IBAN"
    ADDRESS_STREET = "ADDRESS_STREET"
    POSTCODE_CITY = "POSTCODE_CITY"
    URL = "URL"
    VAT_ID = "VAT_ID"
    DATE_OF_BIRTH = "DATE_OF_BIRTH"


class Category(StrEnum):
    """Triage category for an enquiry."""

    QUOTE_REQUEST = "QUOTE_REQUEST"
    SUPPORT = "SUPPORT"
    JOB_APPLICATION = "JOB_APPLICATION"
    PARTNERSHIP = "PARTNERSHIP"
    OTHER = "OTHER"


class Urgency(StrEnum):
    """Triage urgency for an enquiry."""

    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"


class Span(ContractModel):
    """One detected personal-data span inside a source text.

    ``text`` is expected to equal ``source_text[start:end]``. The model does not
    enforce that because it never sees the source text; detection tests assert
    it instead.
    """

    start: int = Field(ge=0, description="Inclusive character offset in the source text.")
    end: int = Field(ge=0, description="Exclusive character offset in the source text.")
    type: EntityType
    text: str
    score: float = Field(ge=0.0, le=1.0)
    source: str = Field(min_length=1, description="Recognizer or backend that produced the span.")

    @field_validator("end")
    @classmethod
    def _end_not_before_start(cls, value: int, info: ValidationInfo) -> int:
        start = info.data.get("start")
        if isinstance(start, int) and value < start:
            raise ValueError("end must be greater than or equal to start")
        return value


class RedactionResult(ContractModel):
    """Result of replacing personal data with placeholders.

    ``mapping`` maps placeholder to original value for this message only. It is
    kept in memory, never logged and never included in a response body.
    """

    redacted_text: str
    mapping: dict[str, str] = Field(
        default_factory=dict,
        repr=False,
        description="Placeholder -> original value. In memory only; never log or persist.",
    )
    spans: list[Span] = Field(default_factory=list)


class RestoreResult(ContractModel):
    """Result of putting original values back into a generated draft."""

    text: str
    unknown_placeholders: list[str] = Field(default_factory=list)


class GuardVerdict(ContractModel):
    """Outbound leak check. Reports types only, never values."""

    ok: bool
    leaked_types: list[EntityType] = Field(default_factory=list)

    @field_validator("leaked_types")
    @classmethod
    def _ok_means_no_leaks(cls, value: list[EntityType], info: ValidationInfo) -> list[EntityType]:
        ok = info.data.get("ok")
        if ok is True and value:
            raise ValueError("ok=True is incompatible with leaked_types")
        return value


class BackendResult(ContractModel):
    """Raw result of one LLM backend call.

    On success ``error_kind`` is ``None`` and ``parsed`` holds the validated JSON
    object. On failure ``error_kind`` names the failure and ``parsed`` may be
    ``None``. ``usage`` is provider-reported token usage when available.
    """

    text: str
    parsed: dict[str, Any] | None = None
    model: str | None = None
    latency_ms: int = Field(ge=0)
    usage: dict[str, Any] | None = None
    error_kind: str | None = None


class TriageResult(ContractModel):
    """Final triage answer returned to the caller."""

    category: Category
    urgency: Urgency
    reply_draft: str
    backend: str = Field(min_length=1)
    model: str | None = None
    latency_ms: int = Field(ge=0)


class Enquiry(ContractModel):
    """One synthetic enquiry record used for evaluation and demos."""

    id: str = Field(min_length=1)
    split: Literal["dev", "test"]
    style: str = Field(min_length=1)
    text: str
    gold_spans: list[Span] = Field(default_factory=list)
    gold_category: Category
    gold_urgency: Urgency


class PageExample(ContractModel):
    """One example in the page's ``examples.json`` array."""

    id: str = Field(min_length=1)
    original: str
    redacted: str
    reply_draft_redacted: str
    reply_draft_restored: str
    spans: list[Span] = Field(default_factory=list)
    misses: list[Span] = Field(default_factory=list)
    category: Category
    urgency: Urgency
    backend: str = Field(min_length=1)
    mock: bool | None = Field(
        default=None,
        description="True for mock page data, False for real results.",
    )


class DetectionResults(ContractModel):
    """Detection numbers for the page's ``results.json``."""

    germeval: dict[str, Any]
    synthetic: dict[str, Any]


class TriageResults(ContractModel):
    """Triage numbers for the page's ``results.json``."""

    per_backend: dict[str, Any]


class PageMeta(ContractModel):
    """Provenance of a results file."""

    version: str
    commit: str
    generated_at: datetime
    sha256: str
    mock: bool | None = None


class PageResults(ContractModel):
    """Contents of the page's ``results.json`` file."""

    detection: DetectionResults
    triage: TriageResults
    meta: PageMeta


PageExamples = list[PageExample]
"""Contents of the page's ``examples.json`` file."""

"""FastAPI service for the AI Privacy Filter.

Endpoints
---------
``GET /health``
    Liveness probe: status and package version. No personal data, no backend probe.

``POST /redact`` ``{text}`` -> ``{redacted_text, spans_summary, ticket, expires_in_s}``
    Pseudonymises ``text`` and returns a short-lived ticket standing in for the
    placeholder mapping. The mapping itself never leaves the process.

``POST /restore`` ``{ticket, text}`` -> ``{text, unknown_placeholders}``
    Puts the original values back into a draft. A ticket that is unknown or
    older than :data:`TICKET_TTL_S` answers 410 Gone.

``POST /process`` ``{text, backend?}`` -> ``{triage, redacted_text, spans_summary}``
    Full pipeline. ``redacted_text`` is exactly what was sent to the model, so a
    reviewer can see what left the machine. A guard failure answers 422 with
    entity types only and no model call is made.

Privacy invariants
------------------
* Logs carry lengths, entity counts and backend names only. Request bodies,
  redacted texts, mappings and entity values are never logged.
* The mapping is kept in a process-local :class:`TicketStore` and expires.
* CORS is deliberately not enabled: the service is meant for loopback use.
* Default bind is ``127.0.0.1:8765``; ``AIPF_BIND=host:port`` overrides it for a
  container that has to reach the service from outside.

The pipeline is wired against the frozen contracts. If a leg is missing from
a build, ``/redact`` and ``/process`` answer 501 with a plain reason; tests
inject deterministic stand-ins.
"""

from __future__ import annotations

import hashlib
import logging
import os
from collections.abc import Mapping
from typing import Final

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import Field

from ai_privacy_filter import __version__
from ai_privacy_filter.backends.registry import get_backend
from ai_privacy_filter.guard import GuardBlockedError
from ai_privacy_filter.models import ContractModel, RestoreResult, TriageResult
from ai_privacy_filter.pipeline import (
    Pipeline,
    PipelineNotReadyError,
    default_pipeline,
    summarise_spans,
)
from ai_privacy_filter.pseudonymise import restore
from ai_privacy_filter.tickets import DEFAULT_TTL_S, TicketStore

logger = logging.getLogger(__name__)

DEFAULT_HOST: Final = "127.0.0.1"
DEFAULT_PORT: Final = 8765
TICKET_TTL_S: Final = DEFAULT_TTL_S
MAX_TEXT_CHARS: Final = 20_000
BIND_ENV: Final = "AIPF_BIND"
E2E_MODE_ENV: Final = "AIPF_E2E_MODE"

BAD_REQUEST: Final = 400
UNPROCESSABLE: Final = 422
GONE: Final = 410
NOT_IMPLEMENTED: Final = 501


class HealthResponse(ContractModel):
    """Answer of ``GET /health``."""

    status: str
    version: str


class RedactRequest(ContractModel):
    """Body of ``POST /redact``."""

    text: str = Field(min_length=1, max_length=MAX_TEXT_CHARS)


class RedactResponse(ContractModel):
    """Answer of ``POST /redact``: the pseudonymised text and a ticket for it."""

    redacted_text: str
    spans_summary: dict[str, int]
    ticket: str = Field(min_length=1)
    expires_in_s: int = Field(gt=0)


class RestoreRequest(ContractModel):
    """Body of ``POST /restore``."""

    ticket: str = Field(min_length=1)
    text: str = Field(min_length=1, max_length=MAX_TEXT_CHARS)


class ProcessRequest(ContractModel):
    """Body of ``POST /process``."""

    text: str = Field(min_length=1, max_length=MAX_TEXT_CHARS)
    backend: str = Field(default="fake", min_length=1)


class ProcessResponse(ContractModel):
    """Answer of ``POST /process``: the triage result plus what was sent out."""

    triage: TriageResult
    redacted_text: str
    spans_summary: dict[str, int]


def resolve_bind(env: Mapping[str, str] | None = None) -> tuple[str, int]:
    """Return the ``(host, port)`` to bind, honouring ``AIPF_BIND=host:port``.

    The default is loopback only. A container deployment sets
    ``AIPF_BIND=0.0.0.0:8765`` (or the port it publishes) so the service is
    reachable from outside its network namespace.
    """
    values = os.environ if env is None else env
    raw = values.get(BIND_ENV, "").strip()
    if not raw:
        return DEFAULT_HOST, DEFAULT_PORT
    host_text, separator, port_text = raw.rpartition(":")
    if not separator:
        raise ValueError(f"{BIND_ENV} must look like host:port, not {raw!r}")
    host = host_text.strip() or DEFAULT_HOST
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    try:
        port = int(port_text)
    except ValueError as exc:
        raise ValueError(f"{BIND_ENV} port is not a number: {port_text!r}") from exc
    if not 1 <= port <= 65535:
        raise ValueError(f"{BIND_ENV} port is out of range: {port}")
    return host, port


def create_app(
    *,
    pipeline: Pipeline | None = None,
    tickets: TicketStore | None = None,
) -> FastAPI:
    """Build the service app with ``/health``, ``/redact``, ``/restore``, ``/process``.

    ``pipeline`` and ``tickets`` are injection points for tests; production uses
    the real detector, the real classifier and a 15-minute ticket store.
    """
    service = pipeline if pipeline is not None else default_pipeline()
    store = tickets if tickets is not None else TicketStore(ttl_s=TICKET_TTL_S)
    app = FastAPI(
        title="AI Privacy Filter",
        version=__version__,
        summary="Pseudonymise personal data before text reaches an LLM.",
        redoc_url=None,
    )
    app.state.pipeline = service
    app.state.tickets = store
    app.add_exception_handler(RequestValidationError, _validation_error_response)

    @app.get("/health", response_model=HealthResponse)
    def health_endpoint() -> HealthResponse:
        return HealthResponse(status="ok", version=__version__)

    @app.post("/redact", response_model=RedactResponse)
    def redact_endpoint(body: RedactRequest) -> RedactResponse:
        try:
            redaction = service.redact_text(body.text)
        except PipelineNotReadyError as exc:
            raise HTTPException(status_code=NOT_IMPLEMENTED, detail=str(exc)) from exc
        ticket = store.issue(redaction.mapping)
        summary = summarise_spans(redaction.spans)
        logger.info("redact: chars=%d spans=%d", len(body.text), len(redaction.spans))
        return RedactResponse(
            redacted_text=redaction.redacted_text,
            spans_summary=summary,
            ticket=ticket.id,
            expires_in_s=ticket.expires_in_s,
        )

    @app.post("/restore", response_model=RestoreResult)
    def restore_endpoint(body: RestoreRequest) -> RestoreResult:
        mapping = store.mapping(body.ticket)
        if mapping is None:
            logger.info("restore: ticket rejected")
            raise HTTPException(status_code=GONE, detail="unknown or expired ticket")
        restored = restore(body.text, mapping)
        logger.info(
            "restore: chars=%d unknown_placeholders=%d",
            len(body.text),
            len(restored.unknown_placeholders),
        )
        return restored

    @app.post("/process", response_model=ProcessResponse)
    def process_endpoint(body: ProcessRequest) -> ProcessResponse:
        try:
            backend = get_backend(body.backend)
        except ValueError as exc:
            logger.info("process: unknown backend name")
            raise HTTPException(status_code=BAD_REQUEST, detail="unknown backend") from exc
        try:
            outcome = service.process(body.text, backend)
        except PipelineNotReadyError as exc:
            raise HTTPException(status_code=NOT_IMPLEMENTED, detail=str(exc)) from exc
        except GuardBlockedError as exc:
            leaked = [member.value for member in exc.verdict.leaked_types]
            logger.warning("process: guard blocked %d entity type(s)", len(leaked))
            raise HTTPException(
                status_code=UNPROCESSABLE,
                detail={"reason": "outbound guard", "leaked_types": leaked},
            ) from exc
        logger.info(
            "process: backend=%s chars=%d spans=%d latency_ms=%d",
            outcome.triage.backend,
            len(body.text),
            sum(outcome.spans_summary.values()),
            outcome.triage.latency_ms,
        )
        if os.environ.get(E2E_MODE_ENV) == "1":
            # End-to-end proof only: the hash lets the harness tie the exact
            # outbound string to the gate log without logging any text.
            outbound_hash = hashlib.sha256(outcome.redacted_text.encode("utf-8")).hexdigest()
            logger.info("process: outbound_sha256=%s", outbound_hash)
        return ProcessResponse(
            triage=outcome.triage,
            redacted_text=outcome.redacted_text,
            spans_summary=outcome.spans_summary,
        )

    return app


async def _validation_error_response(request: Request, exc: Exception) -> JSONResponse:
    """Answer 422 without echoing the request body back.

    FastAPI's default validation error includes the offending ``input`` value.
    For this service that would put personal data into an error body, so only the
    location, message and error type are returned.
    """
    del request
    detail: list[dict[str, object]] = []
    if isinstance(exc, RequestValidationError):
        for error in exc.errors():
            detail.append(
                {
                    "loc": [str(part) for part in error.get("loc", ())],
                    "msg": str(error.get("msg", "invalid value")),
                    "type": str(error.get("type", "value_error")),
                }
            )
    return JSONResponse(status_code=UNPROCESSABLE, content={"detail": detail})

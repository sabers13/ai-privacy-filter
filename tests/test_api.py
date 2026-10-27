"""HTTP service tests: every endpoint, the guard, expiry, logs and CORS.

The service is exercised through ``TestClient`` against the fake backend, with a
deterministic detector and classifier stand-in because T02 and T06 are still
stubs on ``main``. All text is synthetic.
"""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient
from support import ENTITIES, TEXT, FakeClock, Harness

from ai_privacy_filter import api, pipeline
from ai_privacy_filter.api import MAX_TEXT_CHARS, TICKET_TTL_S, create_app, resolve_bind
from ai_privacy_filter.backends.base import LLMBackend
from ai_privacy_filter.models import Category, EntityType, Span, TriageResult, Urgency
from ai_privacy_filter.pipeline import Pipeline, ProcessResult
from ai_privacy_filter.tickets import TicketStore


def _client(harness: Harness, tickets: TicketStore | None = None) -> TestClient:
    return TestClient(create_app(pipeline=harness.pipeline, tickets=tickets))


def _redact(client: TestClient, text: str = TEXT) -> dict[str, object]:
    response = client.post("/redact", json={"text": text})
    assert response.status_code == 200, response.text
    payload: dict[str, object] = response.json()
    return payload


def test_health_reports_status_and_version() -> None:
    with _client(Harness.build()) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": api.__version__}


def test_redact_replaces_values_and_returns_a_ticket(harness: Harness) -> None:
    with _client(harness) as client:
        payload = _redact(client)

    redacted = str(payload["redacted_text"])
    assert "[PERSON_1]" in redacted
    assert "[EMAIL_1]" in redacted
    assert "[PHONE_1]" in redacted
    for needle, _ in ENTITIES:
        assert needle not in redacted
    assert payload["spans_summary"] == {"PERSON": 1, "EMAIL": 1, "PHONE": 1}
    assert isinstance(payload["ticket"], str) and payload["ticket"]
    assert payload["expires_in_s"] == int(TICKET_TTL_S)
    assert "mapping" not in payload


def test_ticket_lifetime_is_fifteen_minutes() -> None:
    assert TICKET_TTL_S == 15 * 60
    assert TicketStore().ttl_s == 15 * 60


def test_restore_puts_originals_back(harness: Harness) -> None:
    with _client(harness) as client:
        redacted = _redact(client)
        response = client.post(
            "/restore",
            json={"ticket": redacted["ticket"], "text": redacted["redacted_text"]},
        )
    assert response.status_code == 200
    assert response.json() == {"text": TEXT, "unknown_placeholders": []}


def test_restore_round_trips_a_draft_reply(harness: Harness) -> None:
    with _client(harness) as client:
        ticket = _redact(client)["ticket"]
        response = client.post(
            "/restore",
            json={"ticket": ticket, "text": "Guten Tag [PERSON_1], danke für Ihre Nachricht."},
        )
    assert response.status_code == 200
    assert response.json()["text"] == "Guten Tag Erika Mustermann, danke für Ihre Nachricht."


def test_restore_reports_invented_placeholders(harness: Harness) -> None:
    with _client(harness) as client:
        ticket = _redact(client)["ticket"]
        response = client.post(
            "/restore", json={"ticket": ticket, "text": "[PERSON_1] und [ACME_1]"}
        )
    assert response.status_code == 200
    assert response.json() == {
        "text": "Erika Mustermann und [ACME_1]",
        "unknown_placeholders": ["[ACME_1]"],
    }


def test_unknown_ticket_is_gone(harness: Harness) -> None:
    with _client(harness) as client:
        response = client.post("/restore", json={"ticket": "not-a-ticket", "text": "Hallo"})
    assert response.status_code == 410
    assert response.json() == {"detail": "unknown or expired ticket"}


def test_expired_ticket_is_gone(harness: Harness, clock: FakeClock) -> None:
    store = TicketStore(ttl_s=900, clock=clock)
    with _client(harness, store) as client:
        ticket = _redact(client)["ticket"]
        clock.advance(900)
        response = client.post("/restore", json={"ticket": ticket, "text": "[PERSON_1]"})
    assert response.status_code == 410
    assert len(store) == 0


def test_process_returns_triage_plus_what_left_the_machine(harness: Harness) -> None:
    with _client(harness) as client:
        response = client.post("/process", json={"text": TEXT})
    assert response.status_code == 200, response.text
    payload = response.json()
    triage = payload["triage"]
    assert triage["backend"] == "fake"
    assert triage["model"] == "fake-deterministic-v1"
    assert triage["category"] in {
        "QUOTE_REQUEST",
        "SUPPORT",
        "JOB_APPLICATION",
        "PARTNERSHIP",
        "OTHER",
    }
    assert triage["urgency"] in {"LOW", "NORMAL", "HIGH"}
    assert triage["latency_ms"] >= 0
    assert payload["spans_summary"] == {"PERSON": 1, "EMAIL": 1, "PHONE": 1}
    redacted = payload["redacted_text"]
    for needle, _ in ENTITIES:
        assert needle not in redacted
    assert "mapping" not in payload
    # The model wrote its draft against placeholders only.
    assert "[PERSON_1]" in triage["reply_draft"]
    assert harness.classifier.calls and all(
        needle not in harness.classifier.calls[0] for needle, _ in ENTITIES
    )


def test_process_guard_failure_is_422_without_a_model_call(blocked_harness: Harness) -> None:
    text = "Erika Mustermann schreibt. Erneut: Erika Mustermann."
    with _client(blocked_harness) as client:
        response = client.post("/process", json={"text": text})
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["reason"] == "outbound guard"
    assert detail["leaked_types"] == ["PERSON"]
    assert "Erika" not in response.text
    assert blocked_harness.classifier.calls == []


def test_process_rejects_unknown_backend(harness: Harness) -> None:
    with _client(harness) as client:
        response = client.post("/process", json={"text": TEXT, "backend": "nope"})
    assert response.status_code == 400
    assert response.json() == {"detail": "unknown backend"}
    assert harness.classifier.calls == []


def test_process_default_backend_is_fake(harness: Harness) -> None:
    with _client(harness) as client:
        response = client.post("/process", json={"text": TEXT})
    assert response.status_code == 200
    assert response.json()["triage"]["backend"] == "fake"


def test_stub_pipeline_answers_501_naming_the_owner() -> None:
    def not_ready_detector(text: str) -> list[Span]:
        raise NotImplementedError("stub")

    def not_ready_classifier(text: str, backend: LLMBackend) -> TriageResult:
        raise NotImplementedError("stub")

    app = create_app(
        pipeline=Pipeline(detector=not_ready_detector, classifier=not_ready_classifier)
    )
    with TestClient(app) as client:
        redact_response = client.post("/redact", json={"text": TEXT})
        process_response = client.post("/process", json={"text": TEXT})
    assert redact_response.status_code == 501
    assert "T02" in redact_response.json()["detail"]
    assert process_response.status_code == 501
    assert "T02" in process_response.json()["detail"]


def test_default_app_wires_the_real_detector_and_classifier() -> None:
    app = create_app()
    service: Pipeline = app.state.pipeline
    assert service.detector is pipeline.default_detector
    assert service.classifier is pipeline.default_classifier
    assert app.state.tickets.ttl_s == TICKET_TTL_S


def test_cors_is_off(harness: Harness) -> None:
    origin = {"Origin": "http://example.org"}
    with _client(harness) as client:
        response = client.post("/redact", json={"text": TEXT}, headers=origin)
        preflight = client.options(
            "/redact", headers={**origin, "Access-Control-Request-Method": "POST"}
        )
    assert "access-control-allow-origin" not in response.headers
    assert "access-control-allow-origin" not in preflight.headers


def test_validation_error_does_not_echo_the_body(harness: Harness) -> None:
    too_long = "Erika Mustermann " + "x" * MAX_TEXT_CHARS
    with _client(harness) as client:
        response = client.post("/redact", json={"text": too_long})
    assert response.status_code == 422
    assert "Erika" not in response.text
    assert "x" * 50 not in response.text
    assert response.json()["detail"][0]["loc"] == ["body", "text"]


def test_requests_reject_unknown_fields(harness: Harness) -> None:
    with _client(harness) as client:
        response = client.post("/redact", json={"text": TEXT, "mapping": {}})
    assert response.status_code == 422


def test_empty_text_is_rejected(harness: Harness) -> None:
    with _client(harness) as client:
        response = client.post("/redact", json={"text": ""})
    assert response.status_code == 422


def test_logs_carry_lengths_and_counts_only(
    harness: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    with (
        caplog.at_level(logging.INFO, logger="ai_privacy_filter.api"),
        _client(harness) as client,
    ):
        _redact(client)
        client.post("/process", json={"text": TEXT})
        client.post("/restore", json={"ticket": "not-a-ticket", "text": TEXT})
    messages = [record.getMessage() for record in caplog.records]
    assert any(message.startswith("redact: chars=") for message in messages)
    assert any(message.startswith("process: backend=fake chars=") for message in messages)
    for message in messages:
        for needle, _ in ENTITIES:
            assert needle not in message
        assert "[PERSON_1]" not in message


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({}, ("127.0.0.1", 8765)),
        ({"AIPF_BIND": ""}, ("127.0.0.1", 8765)),
        ({"AIPF_BIND": "0.0.0.0:9000"}, ("0.0.0.0", 9000)),
        ({"AIPF_BIND": ":9000"}, ("127.0.0.1", 9000)),
        ({"AIPF_BIND": "[::1]:8766"}, ("::1", 8766)),
    ],
)
def test_resolve_bind(env: dict[str, str], expected: tuple[str, int]) -> None:
    assert resolve_bind(env) == expected


@pytest.mark.parametrize("raw", ["8765", "0.0.0.0:", "0.0.0.0:0", "0.0.0.0:70000", "h:p"])
def test_resolve_bind_rejects_bad_values(raw: str) -> None:
    with pytest.raises(ValueError):
        resolve_bind({"AIPF_BIND": raw})


def test_spans_summary_is_ordered_and_deduplicated(harness: Harness) -> None:
    text = "Erika Mustermann und Erika Mustermann"
    spans = [
        Span(
            start=0, end=16, type=EntityType.PERSON, text="Erika Mustermann", score=0.9, source="t"
        ),
        Span(
            start=21,
            end=37,
            type=EntityType.PERSON,
            text="Erika Mustermann",
            score=0.9,
            source="t",
        ),
    ]
    with _client(Harness.build(lambda _: spans)) as client:
        payload = _redact(client, text)
    assert payload["spans_summary"] == {"PERSON": 2}
    assert payload["redacted_text"] == "[PERSON_1] und [PERSON_1]"


def test_process_result_never_dumps_the_mapping() -> None:
    result = ProcessResult(
        triage=TriageResult(
            category=Category.SUPPORT,
            urgency=Urgency.NORMAL,
            reply_draft="Guten Tag [PERSON_1]",
            backend="fake",
            model=None,
            latency_ms=1,
        ),
        redacted_text="Guten Tag [PERSON_1]",
        spans_summary={"PERSON": 1},
        mapping={"[PERSON_1]": "Erika Mustermann"},
    )
    assert "mapping" not in result.model_dump()
    assert "mapping" not in result.model_dump_json()
    assert "Erika Mustermann" not in repr(result)

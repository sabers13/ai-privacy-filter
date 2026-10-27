"""The fake backend is deterministic, schema-valid and offline."""

from __future__ import annotations

import json

from ai_privacy_filter.backends.fake import FakeBackend
from ai_privacy_filter.models import Category, Urgency
from ai_privacy_filter.triage import TRIAGE_SCHEMA

PROMPT = "Bitte um ein Angebot für [PERSON_1] bei [ORG_1]."


def test_same_prompt_same_result() -> None:
    backend = FakeBackend()
    first = backend.complete_json(PROMPT, TRIAGE_SCHEMA, 5)
    second = backend.complete_json(PROMPT, TRIAGE_SCHEMA, 5)
    assert first == second


def test_answer_is_schema_valid() -> None:
    result = FakeBackend().complete_json(PROMPT, TRIAGE_SCHEMA, 5)
    assert result.error_kind is None
    assert result.parsed is not None
    assert set(result.parsed) == set(TRIAGE_SCHEMA["required"])
    assert result.parsed["category"] in {member.value for member in Category}
    assert result.parsed["urgency"] in {member.value for member in Urgency}
    assert isinstance(result.parsed["reply_draft"], str)
    assert result.parsed["reply_draft"]


def test_text_matches_parsed_json() -> None:
    result = FakeBackend().complete_json(PROMPT, TRIAGE_SCHEMA, 5)
    assert json.loads(result.text) == result.parsed


def test_placeholder_from_prompt_is_reused() -> None:
    parsed = FakeBackend().complete_json(PROMPT, TRIAGE_SCHEMA, 5).parsed
    assert parsed is not None
    assert "[PERSON_1]" in parsed["reply_draft"]


def test_different_prompts_can_differ() -> None:
    backend = FakeBackend()
    first = backend.complete_json("Anfrage A", TRIAGE_SCHEMA, 5)
    second = backend.complete_json("Anfrage B", TRIAGE_SCHEMA, 5)
    assert (first.parsed, first.latency_ms) != (second.parsed, second.latency_ms)


def test_backend_metadata_and_usage() -> None:
    backend = FakeBackend()
    assert backend.name == "fake"
    assert backend.is_available() is True
    result = backend.complete_json(PROMPT, TRIAGE_SCHEMA, 5)
    assert result.model == "fake-deterministic-v1"
    assert result.latency_ms >= 0
    assert result.usage is not None
    assert result.usage["completion_tokens"] > 0


def test_fake_ignores_timeout_and_schema_arguments() -> None:
    backend = FakeBackend()
    first = backend.complete_json(PROMPT, {}, 1)
    second = backend.complete_json(PROMPT, {"type": "object"}, 600)
    assert first == second

"""No-network proof for the local filter path.

This module runs with sockets disabled, both through the module marker and when
pytest is invoked with ``--disable-socket``. It walks the whole local path over
the synthetic fixture corpus: gold spans from the ``Span`` contract, then
redaction, the guard, the fake model and restoration. Any accidental socket use
raises ``SocketBlockedError`` and fails the module.

The detector is T02's; it is called only through ``detect.detect`` and runs
with the same blocked sockets.
"""

from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Any

import pytest
import pytest_socket

from ai_privacy_filter import detect
from ai_privacy_filter.backends.fake import FakeBackend
from ai_privacy_filter.guard import GuardBlockedError, guard, require_clean
from ai_privacy_filter.models import Span
from ai_privacy_filter.pseudonymise import redact, restore
from ai_privacy_filter.triage import TRIAGE_SCHEMA

pytestmark = pytest.mark.disable_socket

CORPUS = Path(__file__).parent / "fixtures" / "synthetic_texts.json"
RECORDS: list[dict[str, Any]] = json.loads(CORPUS.read_text(encoding="utf-8"))["texts"]


def _spans(record: dict[str, Any]) -> list[Span]:
    return [Span.model_validate(raw) for raw in record["spans"]]


@pytest.mark.filterwarnings("ignore:A test tried to use socket")
def test_sockets_are_actually_blocked() -> None:
    """Prove the harness blocks sockets, so the rest of this module is evidence."""
    with pytest.raises(pytest_socket.SocketBlockedError):
        socket.socket()
    with pytest.raises(pytest_socket.SocketBlockedError):
        socket.create_connection(("example.org", 443), timeout=1)


def test_pseudonymise_guard_restore_run_with_sockets_disabled() -> None:
    assert len(RECORDS) == 50
    for record in RECORDS:
        text: str = record["text"]
        result = redact(text, _spans(record))
        verdict = guard(result.redacted_text, result.mapping)
        assert verdict.ok, record["id"]
        restored = restore(result.redacted_text, result.mapping)
        assert restored.text == text, record["id"]
        assert restored.unknown_placeholders == [], record["id"]


def test_guard_blocks_leaks_with_sockets_disabled() -> None:
    record = RECORDS[0]
    result = redact(record["text"], _spans(record))
    leaked_value = next(iter(result.mapping.values()))
    assert guard(f"Rückruf bei {leaked_value}", result.mapping).ok is False
    with pytest.raises(GuardBlockedError):
        require_clean(f"Rückruf bei {leaked_value}", result.mapping)


def test_full_local_path_with_the_fake_model_needs_no_network() -> None:
    record = next(item for item in RECORDS if item["id"] == "syn-01-01")
    result = redact(record["text"], _spans(record))
    require_clean(result.redacted_text, result.mapping)

    answer = FakeBackend().complete_json(result.redacted_text, TRIAGE_SCHEMA, 5)
    assert answer.error_kind is None
    assert answer.parsed is not None

    reply = str(answer.parsed["reply_draft"])
    restored = restore(reply, result.mapping)
    assert restored.unknown_placeholders == []
    assert any(value in restored.text for value in result.mapping.values())


def test_detect_path_runs_with_sockets_disabled() -> None:
    """The detector leg of the proof: T02's engine runs with sockets off."""
    record = RECORDS[0]
    text: str = record["text"]
    spans = detect.detect(text)
    assert spans, "detection should find personal data in the first synthetic text"
    gold_texts = {raw["text"] for raw in record["spans"]}
    assert gold_texts & {span.text for span in spans}
    for span in spans:
        assert text[span.start : span.end] == span.text

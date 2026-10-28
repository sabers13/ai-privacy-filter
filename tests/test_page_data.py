"""Page data files follow the shared schemas."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from ai_privacy_filter.evaluation import page_data
from ai_privacy_filter.models import (
    Category,
    Enquiry,
    PageExample,
    PageResults,
    Span,
    Urgency,
)

EXAMPLES_JSON = """
[
  {
    "id": "example-001",
    "original": "Guten Tag, bitte senden Sie ein Angebot.",
    "redacted": "Guten Tag, bitte senden Sie ein Angebot.",
    "reply_draft_redacted": "Guten Tag [PERSON_1], wir melden uns.",
    "reply_draft_restored": "Guten Tag [PERSON_1], wir melden uns.",
    "spans": [],
    "misses": [],
    "category": "QUOTE_REQUEST",
    "urgency": "NORMAL",
    "backend": "fake",
    "mock": true
  }
]
"""

RESULTS_JSON = """
{
  "detection": {"germeval": {"person_recall": 0.0}, "synthetic": {}},
  "triage": {"per_backend": {"fake": {"category_accuracy": 0.0}}},
  "meta": {
    "version": "0.1.0",
    "commit": "0000000",
    "generated_at": "2026-10-27T00:00:00Z",
    "sha256": "0000000000000000000000000000000000000000000000000000000000000000",
    "mock": true
  }
}
"""


def test_examples_json_parses() -> None:
    examples = [PageExample.model_validate(item) for item in json.loads(EXAMPLES_JSON)]
    assert examples[0].category is Category.QUOTE_REQUEST
    assert examples[0].urgency is Urgency.NORMAL
    assert examples[0].mock is True


def test_results_json_parses() -> None:
    results = PageResults.model_validate_json(RESULTS_JSON)
    assert results.detection.germeval["person_recall"] == 0.0
    assert "fake" in results.triage.per_backend
    assert results.meta.mock is True


# --- page export helpers ---------------------------------------------------


def _enquiry(
    identifier: str,
    text: str,
    *,
    category: Category = Category.QUOTE_REQUEST,
    urgency: Urgency = Urgency.NORMAL,
    spans: list[Span] | None = None,
) -> Enquiry:
    return Enquiry(
        id=identifier,
        split="test",
        style="test",
        text=text,
        gold_spans=spans or [],
        gold_category=category,
        gold_urgency=urgency,
    )


def _store(
    root: Path,
    backend: str,
    enquiry_id: str,
    *,
    status: str = "ok",
    draft: str = "Danke, wir melden uns.",
    category: str = "QUOTE_REQUEST",
    urgency: str = "NORMAL",
    extra: dict[str, Any] | None = None,
) -> None:
    payload: dict[str, Any] = {
        "status": status,
        "parsed": {
            "category": category,
            "urgency": urgency,
            "reply_draft": draft,
        },
    }
    if extra:
        payload.update(extra)
    target = root / "results" / "runs" / backend / "responses" / f"{enquiry_id}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload), encoding="utf-8")


def _span(start: int, end: int, kind: str, text: str) -> Span:
    return Span(start=start, end=end, type=kind, text=text, score=1.0, source="gold")


def test_uncovered_gold_spans_covers_compatible_partial_and_invalid() -> None:
    text = "Mail an test@example.com"
    gold = [_span(8, 24, "EMAIL", "test@example.com")]
    assert (
        page_data.uncovered_gold_spans(text, gold, [_span(8, 24, "EMAIL", "test@example.com")])
        == []
    )
    assert page_data.uncovered_gold_spans(text, gold, [_span(8, 24, "PERSON", "test@example.com")])
    assert page_data.uncovered_gold_spans(text, gold, [_span(8, 15, "EMAIL", "test@ex")])
    assert page_data.uncovered_gold_spans(text, gold, [_span(8, 999, "EMAIL", "test@example.com")])
    assert page_data.uncovered_gold_spans(text, gold, [_span(9, 9, "EMAIL", "")])


def test_example_for_rejects_incomplete_payloads() -> None:
    enquiry = _enquiry("p-1", "Mail an test@example.com")
    assert page_data._example_for(enquiry, {"parsed": "no"}, backend="fake") is None
    assert (
        page_data._example_for(
            enquiry,
            {"parsed": {"reply_draft": " ", "category": "OTHER", "urgency": "LOW"}},
            backend="fake",
        )
        is None
    )
    assert (
        page_data._example_for(
            enquiry,
            {"parsed": {"reply_draft": "x", "category": "NOPE", "urgency": "LOW"}},
            backend="fake",
        )
        is None
    )
    assert (
        page_data._example_for(
            enquiry,
            {"parsed": {"reply_draft": "x", "category": "OTHER", "urgency": "NOPE"}},
            backend="fake",
        )
        is None
    )


def test_select_examples_prefers_a_pattern_id_and_fills_every_category(tmp_path: Path) -> None:
    enquiries: list[Enquiry] = []
    for category in Category:
        for position in range(3):
            identifier = f"{category.value.lower()}-{position}"
            # A gold email on a text without an email is a deliberate miss.
            spans = [_span(0, 5, "EMAIL", "xxxxx")] if identifier == "support-0" else []
            enquiries.append(
                _enquiry(identifier, "Kein Kontakt hier.", category=category, spans=spans)
            )
            _store(tmp_path, "fake", identifier)

    selected, notes, misses = page_data.select_examples(
        enquiries, root=tmp_path, backend="fake", pattern_ids=["support-0"]
    )
    assert len(selected) == 15
    assert sum(1 for enquiry in selected if enquiry.gold_category is Category.SUPPORT) == 3
    assert "support-0" in {enquiry.id for enquiry in selected}
    assert misses["support-0"]
    assert any("detection miss" in note for note in notes)


def test_select_examples_reports_when_no_miss_exists(tmp_path: Path) -> None:
    enquiries = [
        _enquiry(f"quote-{position}", f"Angebot Nummer {position} bitte.") for position in range(4)
    ]
    for enquiry in enquiries:
        _store(tmp_path, "fake", enquiry.id)
    selected, notes, _ = page_data.select_examples(enquiries, root=tmp_path, backend="fake")
    assert len(selected) == 4
    assert any("no detection miss" in note for note in notes)


def test_build_examples_restores_placeholders(tmp_path: Path) -> None:
    enquiry = _enquiry("mail-1", "Bitte antworten an test@example.com")
    _store(tmp_path, "fake", enquiry.id, draft="Wir schreiben an [EMAIL_1].")
    examples, notes = page_data.build_examples([enquiry], root=tmp_path, backend="fake")
    assert len(examples) == 1
    assert examples[0]["redacted"] == "Bitte antworten an [EMAIL_1]"
    assert examples[0]["reply_draft_restored"] == "Wir schreiben an test@example.com."
    assert examples[0]["mock"] is False
    assert notes


def test_build_examples_skips_a_missing_response(tmp_path: Path) -> None:
    enquiry = _enquiry("mail-2", "Bitte antworten an test@example.com")
    examples, _ = page_data.build_examples([enquiry], root=tmp_path, backend="fake")
    assert examples == []


def test_build_examples_from_demos_handles_ok_and_blocked(tmp_path: Path) -> None:
    rows = [
        _enquiry("demo-ok", "Mail an test@example.com"),
        _enquiry("demo-blocked", "Mail an test@example.com"),
    ]
    corpus = tmp_path / "data" / "synthetic"
    corpus.mkdir(parents=True)
    (corpus / "test.jsonl").write_text(
        "\n".join(row.model_dump_json() for row in rows) + "\n", encoding="utf-8"
    )
    output = tmp_path / "results" / "page"
    output.mkdir(parents=True)
    (output / "demos.json").write_text(
        json.dumps(
            {
                "backend": "fake",
                "items": [
                    {"id": "demo-ok", "backend": "fake"},
                    {"id": "demo-blocked"},
                    {"id": "unknown"},
                    "not-an-object",
                ],
            }
        ),
        encoding="utf-8",
    )
    responses = output / "responses"
    responses.mkdir()
    (responses / "demo-ok.json").write_text(
        json.dumps(
            {
                "status": "ok",
                "parsed": {
                    "category": "QUOTE_REQUEST",
                    "urgency": "NORMAL",
                    "reply_draft": "Danke, [EMAIL_1].",
                },
            }
        ),
        encoding="utf-8",
    )
    (responses / "demo-blocked.json").write_text(
        json.dumps({"status": "blocked"}), encoding="utf-8"
    )

    examples, notes = page_data.build_examples_from_demos(tmp_path, output)
    assert [example["id"] for example in examples] == ["demo-ok", "demo-blocked"]
    assert examples[1]["backend"] == "guard-blocked"
    assert examples[1]["reply_draft_restored"] == ""
    assert any("fewer than two" in note for note in notes)


def test_build_examples_from_demos_requires_items(tmp_path: Path) -> None:
    output = tmp_path / "results" / "page"
    output.mkdir(parents=True)
    (output / "demos.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError):
        page_data.build_examples_from_demos(tmp_path, output)


def test_load_detection_accepts_a_directory_a_pair_and_a_single_file(tmp_path: Path) -> None:
    payload_syn = {"synthetic": {"a": 1}, "germeval": {"b": 2}}
    pair = tmp_path / "pair"
    pair.mkdir()
    (pair / "detection_synthetic.json").write_text(json.dumps({"synthetic": 1}), encoding="utf-8")
    (pair / "detection_germeval.json").write_text(json.dumps({"germeval": 2}), encoding="utf-8")
    assert page_data.load_detection(tmp_path, pair) == {
        "synthetic": {"synthetic": 1},
        "germeval": {"germeval": 2},
    }

    combined = tmp_path / "combined.json"
    combined.write_text(json.dumps(payload_syn), encoding="utf-8")
    assert page_data.load_detection(tmp_path, combined) == {
        "synthetic": {"a": 1},
        "germeval": {"b": 2},
    }

    single = tmp_path / "detection_germeval.json"
    single.write_text(json.dumps({"germeval": 2}), encoding="utf-8")
    (tmp_path / "detection_synthetic.json").write_text(
        json.dumps({"synthetic": 1}), encoding="utf-8"
    )
    assert page_data.load_detection(tmp_path, single) == {
        "synthetic": {"synthetic": 1},
        "germeval": {"germeval": 2},
    }

    missing = tmp_path / "missing"
    missing.mkdir()
    with pytest.raises(FileNotFoundError):
        page_data.load_detection(tmp_path, missing / "nope.json")


def test_load_detection_scans_results_and_reports_missing(tmp_path: Path) -> None:
    (tmp_path / "results").mkdir()
    with pytest.raises(FileNotFoundError):
        page_data.load_detection(tmp_path)
    (tmp_path / "results" / "detection_synthetic.json").write_text(
        json.dumps({"synthetic": 1}), encoding="utf-8"
    )
    (tmp_path / "results" / "detection_germeval.json").write_text(
        json.dumps({"germeval": 2}), encoding="utf-8"
    )
    assert page_data.load_detection(tmp_path)["germeval"] == {"germeval": 2}


def test_build_results_hashes_the_payload_and_marks_it_real(tmp_path: Path) -> None:
    run_dir = tmp_path / "results" / "runs" / "run"
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(
        json.dumps({"commit": "abc1234", "mode": "test"}), encoding="utf-8"
    )
    payload = page_data.build_results(
        root=tmp_path,
        run_dir=run_dir,
        triage_metrics={"per_backend": {"fake": {"calls": 1}}},
        detection={"synthetic": {"s": 1}, "germeval": {"g": 2}},
    )
    assert payload["meta"]["commit"] == "abc1234"
    assert payload["meta"]["mock"] is False
    assert payload["triage"]["per_backend"]["fake"]["calls"] == 1


def test_export_page_requires_a_test_run_and_metrics(tmp_path: Path) -> None:
    run_dir = tmp_path / "results" / "runs" / "run"
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(json.dumps({"mode": "dev"}), encoding="utf-8")
    with pytest.raises(ValueError):
        page_data.export_page(
            root=tmp_path,
            run_dir=run_dir,
            backend="fake",
            detection_path=None,
            output_dir=tmp_path / "out",
        )

    (run_dir / "run.json").write_text(json.dumps({"mode": "test"}), encoding="utf-8")
    with pytest.raises(FileNotFoundError):
        page_data.export_page(
            root=tmp_path,
            run_dir=run_dir,
            backend="fake",
            detection_path=None,
            output_dir=tmp_path / "out",
        )


def test_example_ids_keeps_page_order() -> None:
    assert page_data.example_ids([{"id": "a"}, {"id": "b"}, {"nope": 1}]) == ["a", "b", "None"]

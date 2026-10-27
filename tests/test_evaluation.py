"""Evaluation helpers: statistics, subset, locks, triage metrics and page data."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from ai_privacy_filter.evaluation import page_data
from ai_privacy_filter.evaluation.corpus import (
    load_split,
    rubric_sample,
    select_triage_subset,
)
from ai_privacy_filter.evaluation.locks import DirectoryLock, LockTimeout
from ai_privacy_filter.evaluation.stats import (
    latency_summary,
    median,
    nearest_rank_p95,
    wilson,
)
from ai_privacy_filter.evaluation.triage_eval import (
    _placeholder_metrics,
    compute_metrics,
    reproduce,
    run_triage,
)
from ai_privacy_filter.models import Category, Enquiry, Span, Urgency
from ai_privacy_filter.pseudonymise import redact

REPO = Path(__file__).resolve().parents[1]


def test_wilson_interval_is_the_score_interval() -> None:
    result = wilson(0, 160)
    assert result["k"] == 0 and result["n"] == 160
    assert result["rate"] == 0.0
    assert 0.0 <= result["lower"] < result["upper"] < 0.05
    assert wilson(0, 0) == {"k": 0, "n": 0, "rate": None, "lower": None, "upper": None}
    with pytest.raises(ValueError):
        wilson(2, 1)


def test_median_and_nearest_rank_p95() -> None:
    assert median([]) is None
    assert median([3, 1, 2]) == 2
    assert median([1, 2, 3, 4]) == 2.5
    assert nearest_rank_p95([]) is None
    assert nearest_rank_p95(list(range(1, 21))) == 19
    summary = latency_summary([100, 200, 300])
    assert summary == {"n": 3, "median": 200.0, "min": 100.0, "max": 300.0, "p95": 300.0}


def test_subset_is_the_fixed_40_and_rubric_is_every_fourth() -> None:
    test_rows = load_split(REPO / "data" / "synthetic" / "test.jsonl", "test")
    subset = select_triage_subset(test_rows)
    assert len(subset) == 40
    assert len({row.id for row in subset}) == 40
    assert [row.id for row in rubric_sample(subset)] == [row.id for row in subset][0::4]


def test_directory_lock_releases_and_times_out(tmp_path: Path) -> None:
    lock = DirectoryLock("aipf-lock-test", root=tmp_path, timeout_s=0.0)
    lock.acquire()
    assert lock.path.is_dir()
    lock.release()
    assert not lock.path.exists()

    held = DirectoryLock("aipf-lock-test", root=tmp_path, timeout_s=0.0)
    held.acquire()
    try:
        with pytest.raises(LockTimeout):
            DirectoryLock("aipf-lock-test", root=tmp_path, timeout_s=0.01, poll_s=0.01).acquire()
    finally:
        held.release()


def _tiny_enquiry(identifier: str, text: str, category: Category) -> Enquiry:
    return Enquiry(
        id=identifier,
        split="dev",
        style="formal_letter",
        text=text,
        gold_spans=[],
        gold_category=category,
        gold_urgency=Urgency.NORMAL,
    )


def test_uncovered_gold_spans_uses_compatible_types() -> None:
    text = "Guten Tag Anna Muster, mail an a@example.com"
    person = Span(start=10, end=21, type="PERSON", text="Anna Muster", score=1.0, source="gold")
    email = Span(start=31, end=44, type="EMAIL", text="a@example.com", score=1.0, source="gold")
    predicted = [Span(start=10, end=21, type="ORG", text="Anna Muster", score=1.0, source="test")]
    misses = page_data.uncovered_gold_spans(text, [person, email], predicted)
    assert [span.type for span in misses] == ["EMAIL"]


def test_select_examples_chooses_three_per_category(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path
    rows: list[Enquiry] = []
    for category in Category:
        for _position in range(4):
            identifier = f"syn-dev-{len(rows) + 1:04d}"
            rows.append(_tiny_enquiry(identifier, f"Anfrage {identifier}", category))
    for enquiry in rows:
        target = root / "results" / "runs" / "fake" / "responses" / f"{enquiry.id}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(
                {
                    "status": "ok",
                    "parsed": {
                        "category": enquiry.gold_category.value,
                        "urgency": "NORMAL",
                        "reply_draft": "Guten Tag [PERSON_1], danke.",
                    },
                }
            ),
            encoding="utf-8",
        )

    def fake_detection(enquiry: Enquiry) -> tuple[list[Span], list[Span]]:
        if enquiry.id in {"syn-dev-0001", "syn-dev-0005"}:
            miss = Span(
                start=0,
                end=7,
                type="PERSON",
                text="Anfrage",
                score=1.0,
                source="gold",
            )
            return [], [miss]
        return [], []

    monkeypatch.setattr(page_data, "detection_for", fake_detection)
    selected, notes, _ = page_data.select_examples(rows, root=root, backend="fake")
    assert len(selected) == 15
    counts = {category: 0 for category in Category}
    for enquiry in selected:
        counts[enquiry.gold_category] += 1
    assert all(value == 3 for value in counts.values())
    assert any("deliberate" in note for note in notes)


def test_build_results_validates_the_page_contract(tmp_path: Path) -> None:
    run_dir = tmp_path / "results" / "runs" / "run-triage-abc1234"
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(
        json.dumps({"run_id": run_dir.name, "commit": "a" * 40, "mode": "test"}),
        encoding="utf-8",
    )
    results = page_data.build_results(
        root=tmp_path,
        run_dir=run_dir,
        triage_metrics={"per_backend": {"fake": {"available": True}}},
        detection={"synthetic": {"recall": 0.5}, "germeval": {"recall": 0.4}},
    )
    assert results["meta"]["mock"] is False
    assert results["meta"]["commit"] == "a" * 40
    assert results["detection"]["synthetic"]["recall"] == 0.5


def test_load_detection_reads_the_page_shape(tmp_path: Path) -> None:
    path = tmp_path / "metrics.json"
    path.write_text(
        json.dumps({"detection": {"synthetic": {"a": 1}, "germeval": {"b": 2}, "extra": 3}}),
        encoding="utf-8",
    )
    block = page_data.load_detection(tmp_path, path)
    assert block == {"synthetic": {"a": 1}, "germeval": {"b": 2}}


def test_load_detection_reads_the_t11_pair(tmp_path: Path) -> None:
    (tmp_path / "results").mkdir()
    (tmp_path / "results" / "detection_synthetic.json").write_text(
        json.dumps({"dataset": "synthetic_test", "baselines": {"B2": {}}}), encoding="utf-8"
    )
    (tmp_path / "results" / "detection_germeval.json").write_text(
        json.dumps({"dataset": "germeval_test", "baselines": {"B2": {}}}), encoding="utf-8"
    )
    block = page_data.load_detection(tmp_path)
    assert block["synthetic"]["dataset"] == "synthetic_test"
    assert block["germeval"]["dataset"] == "germeval_test"


def _make_dev_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    (root / "data" / "synthetic").mkdir(parents=True)
    shutil.copy(REPO / "METHOD.md", root / "METHOD.md")
    shutil.copy(REPO / "scripts" / "method_guard.py", root / "scripts" / "method_guard.py")
    shutil.copy(REPO / "data" / "synthetic" / "dev.jsonl", root / "data" / "synthetic")
    shutil.copy(REPO / "data" / "synthetic" / "test.jsonl", root / "data" / "synthetic")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-q",
            "-m",
            "fixture",
        ],
        cwd=root,
        check=True,
    )
    return root


def test_run_triage_dev_writes_responses_and_reproduces(tmp_path: Path) -> None:
    root = _make_dev_repo(tmp_path)
    run_dir, summary = run_triage(
        root=root,
        backend_names=["fake"],
        mode="dev",
        run_id="20261027T000000Z-triage-abc1234",
        rerun_reason=None,
        method_approval=None,
        timeout_s=5,
        resume=False,
        limit=3,
        warmup=False,
        command=["run_triage", "--mode", "dev"],
    )
    record = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert record["status"] == "complete"
    assert record["subset"]["count"] == 3
    assert summary["backends"]["fake"]["calls"] == 3
    metrics = json.loads((run_dir / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["per_backend"]["fake"]["expected"] == 3
    assert metrics["per_backend"]["fake"]["success"] == 3
    assert (run_dir / "scoring" / "fake.json").is_file()
    assert (root / "results" / "runs" / "fake" / "index.json").is_file()

    rebuilt, match = reproduce(root=root, run_dir=run_dir)
    assert match is True
    assert rebuilt["per_backend"]["fake"]["success"] == 3


def test_redact_and_placeholder_metrics_round_trip() -> None:
    text = "Guten Tag Anna Muster, mail an a@example.com"
    spans = [
        Span(start=10, end=21, type="PERSON", text="Anna Muster", score=1.0, source="gold"),
        Span(start=31, end=44, type="EMAIL", text="a@example.com", score=1.0, source="gold"),
    ]
    redaction = redact(text, spans)
    assert "[PERSON_1]" in redaction.redacted_text
    assert "[EMAIL_1]" in redaction.redacted_text
    assert len(redaction.mapping) == 2
    placeholders = _placeholder_metrics(
        "Guten Tag [PERSON_1], wir schreiben an [EMAIL_1].", redaction.mapping
    )
    assert placeholders["mapped_occurrences"] == 2
    assert placeholders["restore_exact"] is True
    unknown = _placeholder_metrics("Guten Tag [PERSON_9].", redaction.mapping)
    assert unknown["unknown"] == 1
    assert unknown["restore_exact"] is False


def test_metrics_without_responses_count_as_failures(tmp_path: Path) -> None:
    (tmp_path / "data" / "synthetic").mkdir(parents=True)
    shutil.copy(REPO / "data" / "synthetic" / "dev.jsonl", tmp_path / "data" / "synthetic")
    shutil.copy(REPO / "data" / "synthetic" / "test.jsonl", tmp_path / "data" / "synthetic")
    run_dir = tmp_path / "results" / "runs" / "r-triage-abc1234"
    run_dir.mkdir(parents=True)
    rows = load_split(tmp_path / "data" / "synthetic" / "dev.jsonl", "dev")[:2]
    (run_dir / "run.json").write_text(
        json.dumps(
            {
                "run_id": run_dir.name,
                "mode": "dev",
                "stage": "triage",
                "commit": "b" * 40,
                "method_sha256": "c" * 64,
                "subset": {"ids": [row.id for row in rows], "count": 2},
                "backends": {"fake": {"available": True, "availability_reason": None}},
            }
        ),
        encoding="utf-8",
    )
    # The corpus lives at the repository root, not the temp root; use the real
    # root and a run directory outside results/.
    metrics = compute_metrics(run_dir=run_dir, root=tmp_path)
    entry = metrics["per_backend"]["fake"]
    assert entry["success"] == 0
    assert entry["errors"] == 2
    assert entry["joint"]["k"] == 0
    assert entry["joint"]["n"] == 2

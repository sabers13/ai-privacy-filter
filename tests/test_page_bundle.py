"""The page bundle is complete, sourced and deterministic."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from ai_privacy_filter.evaluation import page_bundle
from ai_privacy_filter.evaluation.stats import wilson

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "results" / "page"


def _read(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _at(payload: Any, dotted: str) -> Any:
    current = payload
    if not dotted or dotted == ".":
        return current
    for part in dotted.split("."):
        current = current[part]
    return current


def _resolve(source: dict[str, Any]) -> dict[str, Any]:
    """Resolve a recorded source spec without using the bundle writer."""
    node = _at(_read(ROOT / str(source["file"])), str(source["path"]))
    value_spec = source["value"]
    numerator_spec = source.get("numerator")
    denominator_spec = source.get("denominator")
    mode = str(value_spec["mode"])
    if mode == "ratio":
        numerator = int(_at(node, str(value_spec["numerator_field"])))
        denominator = int(_at(node, str(value_spec["denominator_field"])))
        value = round(100.0 * numerator / denominator, 1)
    elif mode == "sum_ratio":
        children = list(node.values())
        numerator = sum(int(_at(child, str(value_spec["numerator_field"]))) for child in children)
        denominator = sum(
            int(_at(child, str(value_spec["denominator_field"]))) for child in children
        )
        value = round(100.0 * numerator / denominator, 1)
    elif mode == "field":
        value = round(float(_at(node, str(value_spec["field"]))) * float(value_spec["scale"]), 1)
        numerator = int(_at(node, str(numerator_spec["field"]))) if numerator_spec else None
        denominator = int(_at(node, str(denominator_spec["field"]))) if denominator_spec else None
    else:  # pragma: no cover - the bundle never writes another mode
        raise AssertionError(mode)
    ci_spec = source.get("ci")
    if not ci_spec:
        interval = None
    else:
        scale = float(ci_spec.get("scale", 100.0))
        if ci_spec["mode"] == "list":
            raw = _at(node, str(ci_spec["field"]))
            interval = [round(float(raw[0]) * scale, 1), round(float(raw[1]) * scale, 1)]
        elif ci_spec["mode"] == "fields":
            interval = [
                round(float(_at(node, str(ci_spec["lower"]))) * scale, 1),
                round(float(_at(node, str(ci_spec["upper"]))) * scale, 1),
            ]
        else:
            interval = [
                round(float(wilson(numerator, denominator)["lower"]) * 100.0, 1),
                round(float(wilson(numerator, denominator)["upper"]) * 100.0, 1),
            ]
    return {
        "value": value,
        "numerator": numerator,
        "denominator": denominator,
        "ci95": interval,
    }


def _bundle_files() -> list[Path]:
    return sorted(path for path in BUNDLE.rglob("*") if path.is_file())


def test_bundle_has_the_expected_shape() -> None:
    for relative in (
        "kpis.json",
        "results.json",
        "examples.json",
        "demos.json",
        "worked-example.json",
        "tryit-samples.json",
        "demo-enquiries.json",
        "MANIFEST.md",
    ):
        assert (BUNDLE / relative).is_file(), relative
    charts = sorted((BUNDLE / "charts").glob("*.json"))
    assert len(charts) == 5
    for chart in charts:
        assert chart.with_suffix(".csv").is_file(), chart.name
    assert len(list((BUNDLE / "screenshots").glob("*.png"))) == 4


def test_every_kpi_number_equals_its_source() -> None:
    document = _read(BUNDLE / "kpis.json")
    kpis = document["kpis"]
    assert len(kpis) >= 24
    for entry in kpis:
        resolved = _resolve(entry["source"])
        assert entry["value"] == resolved["value"], entry["id"]
        assert entry["numerator"] == resolved["numerator"], entry["id"]
        assert entry["denominator"] == resolved["denominator"], entry["id"]
        assert entry["ci95"] == resolved["ci95"], entry["id"]
        assert entry["label"]["en"] and entry["label"]["de"]
        assert entry["source"]["file"] and entry["source"]["path"]


def test_required_headline_kpis_are_present() -> None:
    values = {entry["id"]: entry for entry in _read(BUNDLE / "kpis.json")["kpis"]}
    assert (values["synthetic_recall_b2"]["value"], values["synthetic_recall_b2"]["numerator"]) == (
        99.5,
        1069,
    )
    assert values["synthetic_recall_b2"]["denominator"] == 1074
    assert (values["leak_rate_b2"]["numerator"], values["leak_rate_b2"]["denominator"]) == (3, 160)
    assert (values["over_masking_b2"]["numerator"], values["over_masking_b2"]["denominator"]) == (
        0,
        160,
    )
    assert (values["restore_exact"]["numerator"], values["restore_exact"]["denominator"]) == (
        160,
        160,
    )
    assert values["germeval_combined_recall_b2"]["value"] == 78.4
    assert (values["ollama_invented_placeholders"]["numerator"]) == 13
    assert values["ollama_invented_placeholders"]["denominator"] == 40


def test_every_chart_number_equals_its_source() -> None:
    for chart_path in sorted((BUNDLE / "charts").glob("*.json")):
        chart = _read(chart_path)
        assert chart["title"]["en"] and chart["title"]["de"]
        assert chart["unit"] in {"percent", "milliseconds"}
        for row in chart["rows"]:
            assert row["label"]["en"] and row["label"]["de"]
            for segment in row["segments"]:
                resolved = _resolve(segment["source"])
                assert segment["value"] == resolved["value"], (chart["id"], row["label"])
                assert segment["numerator"] == resolved["numerator"]
                assert segment["denominator"] == resolved["denominator"]
                assert segment["ci95"] == resolved["ci95"]


def test_each_chart_csv_twin_matches_its_json() -> None:
    for chart_path in sorted((BUNDLE / "charts").glob("*.json")):
        chart = _read(chart_path)
        expected: list[list[str]] = []
        for row in chart["rows"]:
            for segment in row["segments"]:
                interval = segment["ci95"] or [None, None]
                expected.append(
                    [
                        str(segment["value"]),
                        "" if segment["numerator"] is None else str(segment["numerator"]),
                        "" if segment["denominator"] is None else str(segment["denominator"]),
                        "" if segment["n"] is None else str(segment["n"]),
                        "" if interval[0] is None else str(interval[0]),
                        "" if interval[1] is None else str(interval[1]),
                    ]
                )
        with chart_path.with_suffix(".csv").open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        assert len(rows) == len(expected)
        for row, wanted in zip(rows, expected, strict=True):
            got = [
                _same_number(row["value"]),
                _same_number(row["numerator"]),
                _same_number(row["denominator"]),
                _same_number(row["n"]),
                _same_number(row["ci_low"] or ""),
                _same_number(row["ci_high"] or ""),
            ]
            assert got == [_same_number(value) for value in wanted]


def _same_number(text: str) -> float | None:
    return None if text == "" else float(text)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_manifest_lists_every_file_with_its_hash() -> None:
    manifest = (BUNDLE / "MANIFEST.md").read_text(encoding="utf-8")
    rows = [line for line in manifest.splitlines() if line.startswith("| `")]
    listed: dict[str, str] = {}
    for line in rows:
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        relative = cells[0].strip("`")
        listed[relative] = cells[-1].strip("`")
    files = {
        path.relative_to(BUNDLE).as_posix(): _sha256(path)
        for path in _bundle_files()
        if path.name != "MANIFEST.md"
    }
    assert set(listed) == set(files)
    for relative, digest in files.items():
        assert listed[relative] == digest, relative
    for name in (
        "screenshots/n8n-execution-success.png",
        "screenshots/n8n-executions-list.png",
        "screenshots/n8n-review-form.png",
        "screenshots/n8n-result.png",
    ):
        assert name in listed


def test_screenshots_are_png_files() -> None:
    for path in sorted((BUNDLE / "screenshots").glob("*.png")):
        assert path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_demo_materials_come_from_the_committed_corpus() -> None:
    worked = _read(BUNDLE / "worked-example.json")
    assert worked["columns"]["original"] and worked["columns"]["redacted"]
    assert worked["columns"]["reply_draft_restored"]
    samples = _read(BUNDLE / "tryit-samples.json")["samples"]
    assert len(samples) == 2
    assert all(sample["text"] for sample in samples)
    demo = _read(BUNDLE / "demo-enquiries.json")
    assert demo["count"] == 3
    dev_lines = (ROOT / "data" / "synthetic" / "dev.jsonl").read_text(encoding="utf-8").splitlines()
    expected = [json.loads(line)["id"] for line in dev_lines[:3]]
    assert [record["id"] for record in demo["enquiries"]] == expected


def test_two_runs_write_identical_bytes(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    page_bundle.export_bundle(
        root=ROOT, output_dir=first, backend="claude-cli", detection_path=None
    )
    page_bundle.export_bundle(
        root=ROOT, output_dir=second, backend="claude-cli", detection_path=None
    )
    first_files = {
        path.relative_to(first).as_posix(): path.read_bytes()
        for path in sorted(first.rglob("*"))
        if path.is_file()
    }
    second_files = {
        path.relative_to(second).as_posix(): path.read_bytes()
        for path in sorted(second.rglob("*"))
        if path.is_file()
    }
    assert set(first_files) == set(second_files)
    assert first_files == second_files


def test_committed_bundle_matches_a_fresh_export(tmp_path: Path) -> None:
    fresh = tmp_path / "fresh"
    page_bundle.export_bundle(
        root=ROOT, output_dir=fresh, backend="claude-cli", detection_path=None
    )
    for relative in (
        "kpis.json",
        "results.json",
        "examples.json",
        "worked-example.json",
        "tryit-samples.json",
        "demo-enquiries.json",
        "MANIFEST.md",
    ):
        assert (fresh / relative).read_bytes() == (BUNDLE / relative).read_bytes(), relative
    for chart in sorted((BUNDLE / "charts").glob("*")):
        assert (fresh / "charts" / chart.name).read_bytes() == chart.read_bytes(), chart.name


@pytest.mark.parametrize("path", ["kpis.json", "results.json"])
def test_bundle_json_is_sorted(path: str) -> None:
    text = (BUNDLE / path).read_text(encoding="utf-8")
    assert text == json.dumps(json.loads(text), ensure_ascii=False, indent=2, sort_keys=True) + "\n"

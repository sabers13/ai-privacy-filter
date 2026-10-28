"""Deterministic page bundle: KPIs, charts and demo material.

``make page-data`` rebuilds ``results/page/`` from the committed result files
only. The bundle has no wall-clock value: the page results timestamp comes from
the stored triage metrics, so two runs over the same inputs write the same
bytes.

The module keeps the provenance of every number it writes. A KPI or chart
segment carries a ``source`` object (file, JSON path and the fields to read),
and the tests resolve that object against the committed file again.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import shutil
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Final

from ai_privacy_filter.evaluation.page_data import export_page
from ai_privacy_filter.evaluation.stats import wilson
from ai_privacy_filter.evaluation.triage_eval import latest_run_dir

BUNDLE_SCHEMA: Final = 1
PERCENT: Final = "percent"
MILLISECONDS: Final = "milliseconds"

BACKENDS: Final[tuple[tuple[str, str, str], ...]] = (
    ("claude-cli", "Claude (CLI)", "Claude (CLI)"),
    ("codex-cli", "Codex (CLI)", "Codex (CLI)"),
    ("byok", "BYOK (OpenCode Go)", "BYOK (OpenCode Go)"),
    ("ollama", "Ollama (local)", "Ollama (lokal)"),
)

BASELINES: Final[tuple[tuple[str, str, str], ...]] = (
    ("B0", "B0 — regex only", "B0 — nur Regex"),
    ("B1", "B1 — Presidio defaults", "B1 — Presidio-Standard"),
    ("B2", "B2 — full configuration", "B2 — volle Konfiguration"),
)

TYPE_ORDER: Final[tuple[str, ...]] = (
    "PERSON",
    "ORG",
    "EMAIL",
    "PHONE",
    "IBAN",
    "ADDRESS_STREET",
    "POSTCODE_CITY",
)

SOURCE_TRIAGE_ORDER: Final[tuple[str, ...]] = (
    "results/detection_synthetic.json",
    "results/leak_rate.json",
    "results/restore.json",
    "results/detection_germeval.json",
    "results/runs/<triage-run>/metrics.json",
    "results/ollama_outcome.json",
)


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path.name} is not a JSON object")
    return payload


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _at(payload: object, dotted: str) -> Any:
    current: Any = payload
    if not dotted or dotted == ".":
        return current
    for part in dotted.split("."):
        if not isinstance(current, Mapping) or part not in current:
            raise KeyError(f"path {dotted} is missing")
        current = current[part]
    return current


def _field(payload: object, dotted: str) -> Any:
    return _at(payload, dotted)


def _number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"expected a number, got {type(value).__name__}")
    return float(value)


def _sum_field(objects: Sequence[object], field: str) -> int:
    return sum(int(_field(obj, field)) for obj in objects)


def _spec(
    file: str,
    path: str,
    *,
    value_field: str,
    value_scale: float,
    numerator_field: str,
    denominator_field: str,
    ci_field: str,
) -> dict[str, Any]:
    """Return a source spec for one scalar or ratio read from a JSON object."""
    return {
        "file": file,
        "path": path,
        "value": {"mode": "field", "field": value_field, "scale": value_scale},
        "numerator": {"mode": "field", "field": numerator_field},
        "denominator": {"mode": "field", "field": denominator_field},
        "ci": {"mode": "list", "field": ci_field, "scale": value_scale},
    }


def _ratio_spec(
    file: str,
    path: str,
    *,
    numerator_field: str,
    denominator_field: str,
) -> dict[str, Any]:
    """Return a source spec for a ratio summed over one object's children."""
    return {
        "file": file,
        "path": path,
        "value": {
            "mode": "sum_ratio",
            "numerator_field": numerator_field,
            "denominator_field": denominator_field,
            "scale": 100.0,
        },
        "numerator": {"mode": "sum", "field": numerator_field},
        "denominator": {"mode": "sum", "field": denominator_field},
        "ci": {"mode": "wilson"},
    }


def _plain_spec(file: str, path: str, field: str, scale: float = 1.0) -> dict[str, Any]:
    return {
        "file": file,
        "path": path,
        "value": {"mode": "field", "field": field, "scale": scale},
        "numerator": None,
        "denominator": None,
        "ci": None,
    }


def _resolve(root: Path, source: Mapping[str, Any]) -> dict[str, Any]:
    """Resolve one source spec against the committed tree."""
    payload = _read_json(root / str(source["file"]))
    node = _at(payload, str(source["path"]))
    value_spec = source["value"]
    numerator_spec = source.get("numerator")
    denominator_spec = source.get("denominator")
    mode = str(value_spec["mode"])
    numerator: int | None
    denominator: int | None
    if mode == "ratio":
        numerator = int(_field(node, str(value_spec["numerator_field"])))
        denominator = int(_field(node, str(value_spec["denominator_field"])))
        value = round(100.0 * numerator / denominator, 1)
    elif mode == "sum_ratio":
        children = list(node.values()) if isinstance(node, Mapping) else []
        numerator = _sum_field(children, str(value_spec["numerator_field"]))
        denominator = _sum_field(children, str(value_spec["denominator_field"]))
        value = round(100.0 * numerator / denominator, 1)
    elif mode == "field":
        value = round(
            _number(_field(node, str(value_spec["field"]))) * float(value_spec["scale"]), 1
        )
        numerator = int(_field(node, str(numerator_spec["field"]))) if numerator_spec else None
        denominator = (
            int(_field(node, str(denominator_spec["field"]))) if denominator_spec else None
        )
    else:
        raise ValueError(f"unknown value mode {mode}")
    ci_spec = source.get("ci")
    if not ci_spec:
        interval = None
    elif str(ci_spec["mode"]) == "list":
        scale = float(ci_spec["scale"])
        raw = _field(node, str(ci_spec["field"]))
        if not isinstance(raw, list) or len(raw) != 2:
            raise ValueError("expected a two-element interval")
        interval = [round(_number(raw[0]) * scale, 1), round(_number(raw[1]) * scale, 1)]
    elif str(ci_spec["mode"]) == "fields":
        scale = float(ci_spec["scale"])
        interval = [
            round(_number(_field(node, str(ci_spec["lower"]))) * scale, 1),
            round(_number(_field(node, str(ci_spec["upper"]))) * scale, 1),
        ]
    elif str(ci_spec["mode"]) == "wilson":
        if numerator is None or denominator is None:
            raise ValueError("wilson needs a numerator and a denominator")
        estimate = wilson(numerator, denominator)
        interval = [
            round(float(estimate["lower"]) * 100.0, 1),
            round(float(estimate["upper"]) * 100.0, 1),
        ]
    else:
        raise ValueError(f"unknown interval mode {ci_spec['mode']}")
    return {
        "value": value,
        "numerator": numerator,
        "denominator": denominator,
        "ci95": interval,
    }


def _kpi(
    root: Path,
    identifier: str,
    label_en: str,
    label_de: str,
    unit: str,
    source: Mapping[str, Any],
) -> dict[str, Any]:
    resolved = _resolve(root, source)
    return {
        "id": identifier,
        "label": {"en": label_en, "de": label_de},
        "value": resolved["value"],
        "unit": unit,
        "numerator": resolved["numerator"],
        "denominator": resolved["denominator"],
        "ci95": resolved["ci95"],
        "source": dict(source),
    }


def build_kpis(root: Path, *, run_dir: Path) -> dict[str, Any]:
    """Return ``kpis.json``: one entry per headline number."""
    metrics_file = f"results/runs/{run_dir.name}/metrics.json"
    kpis: list[dict[str, Any]] = [
        _kpi(
            root,
            "synthetic_recall_b2",
            "Synthetic gold spans protected (B2)",
            "Synthetische Gold-Spans geschützt (B2)",
            PERCENT,
            _ratio_spec(
                "results/detection_synthetic.json",
                "baselines.B2.per_type",
                numerator_field="protected",
                denominator_field="gold",
            ),
        ),
        _kpi(
            root,
            "leak_rate_b2",
            "Synthetic test enquiries that leaked a gold value (B2)",
            "Synthetische Testeranfragen mit geleaktem Gold-Wert (B2)",
            PERCENT,
            _spec(
                "results/leak_rate.json",
                "baselines.B2",
                value_field="leak_rate_pct",
                value_scale=1.0,
                numerator_field="leaked_enquiries",
                denominator_field="records",
                ci_field="leak_wilson_pct",
            ),
        ),
        _kpi(
            root,
            "over_masking_b2",
            "Synthetic enquiries with extra text masked (B2)",
            "Synthetische Anfragen mit zusätzlich maskiertem Text (B2)",
            PERCENT,
            _spec(
                "results/detection_synthetic.json",
                "baselines.B2.over_masking",
                value_field="enquiry_rate_pct",
                value_scale=1.0,
                numerator_field="enquiries_with_extra",
                denominator_field="records",
                ci_field="enquiry_wilson_pct",
            ),
        ),
        _kpi(
            root,
            "restore_exact",
            "Redaction/restoration exact round-trips (B2)",
            "Exakte Redaktion-/Restaurierungs-Rundläufe (B2)",
            PERCENT,
            _spec(
                "results/restore.json",
                ".",
                value_field="exact_pct",
                value_scale=1.0,
                numerator_field="exact_round_trips",
                denominator_field="records",
                ci_field="exact_wilson_pct",
            ),
        ),
    ]
    for identifier, type_name, label_en, label_de in (
        (
            "germeval_person_recall_b2",
            "PERSON",
            "GermEval PERSON recall (B2)",
            "GermEval-PERSON-Recall (B2)",
        ),
        (
            "germeval_org_recall_b2",
            "ORG",
            "GermEval ORG recall (B2)",
            "GermEval-ORG-Recall (B2)",
        ),
    ):
        kpis.append(
            _kpi(
                root,
                identifier,
                label_en,
                label_de,
                PERCENT,
                _spec(
                    "results/detection_germeval.json",
                    f"baselines.B2.per_type.{type_name}",
                    value_field="recall_pct",
                    value_scale=1.0,
                    numerator_field="protected",
                    denominator_field="gold",
                    ci_field="recall_wilson_pct",
                ),
            )
        )
    kpis.append(
        _kpi(
            root,
            "germeval_combined_recall_b2",
            "GermEval PERSON+ORG recall (B2)",
            "GermEval-PERSON+ORG-Recall (B2)",
            PERCENT,
            _spec(
                "results/detection_germeval.json",
                "baselines.B2.micro_primary",
                value_field="recall_pct",
                value_scale=1.0,
                numerator_field="protected",
                denominator_field="gold",
                ci_field="recall_wilson_pct",
            ),
        )
    )
    for backend, label_en, label_de in BACKENDS:
        for metric, metric_en, metric_de in (
            ("category_only", "category accuracy", "Kategorie-Genauigkeit"),
            ("urgency_only", "urgency accuracy", "Dringlichkeits-Genauigkeit"),
            ("joint", "joint category+urgency accuracy", "gemeinsame Genauigkeit"),
        ):
            kpis.append(
                _kpi(
                    root,
                    f"triage_{metric}_{backend}",
                    f"Triage {metric_en} — {label_en}",
                    f"Triage-{metric_de} — {label_de}",
                    PERCENT,
                    _rate_spec(
                        metrics_file,
                        f"per_backend.{backend}.{metric}",
                    ),
                )
            )
    for backend, label_en, label_de in BACKENDS:
        kpis.append(
            _kpi(
                root,
                f"triage_latency_{backend}",
                f"Median triage latency — {label_en}",
                f"Mediane Triage-Latenz — {label_de}",
                MILLISECONDS,
                _plain_spec(metrics_file, f"per_backend.{backend}.latency_ms", "median"),
            )
        )
    kpis.append(
        _kpi(
            root,
            "ollama_invented_placeholders",
            "Ollama drafts that invented a placeholder",
            "Ollama-Entwürfe mit erfundenem Platzhalter",
            PERCENT,
            {
                "file": "results/ollama_outcome.json",
                "path": "metrics.draft",
                "value": {
                    "mode": "ratio",
                    "numerator_field": "unknown_placeholder_drafts",
                    "denominator_field": "scored",
                    "scale": 100.0,
                },
                "numerator": {"mode": "sum", "field": "unknown_placeholder_drafts"},
                "denominator": {"mode": "sum", "field": "scored"},
                "ci": {"mode": "wilson"},
            },
        )
    )
    return {"schema_version": BUNDLE_SCHEMA, "kpis": kpis}


def _segment(
    root: Path,
    series: str,
    source: Mapping[str, Any],
    *,
    n: int | None = None,
) -> dict[str, Any]:
    resolved = _resolve(root, source)
    return {
        "series": series,
        "value": resolved["value"],
        "numerator": resolved["numerator"],
        "denominator": resolved["denominator"],
        "n": n if n is not None else resolved["denominator"],
        "ci95": resolved["ci95"],
        "source": dict(source),
    }


def _latency_segment(root: Path, series: str, metrics_file: str, backend: str) -> dict[str, Any]:
    source = _plain_spec(metrics_file, f"per_backend.{backend}.latency_ms", "median")
    payload = _read_json(root / metrics_file)
    n = int(_field(payload, f"per_backend.{backend}.latency_ms.n"))
    return _segment(root, series, source, n=n)


def _ci_fields(lower: str, upper: str) -> dict[str, Any]:
    return {"mode": "fields", "lower": lower, "upper": upper, "scale": 100.0}


def _rate_spec(file: str, path: str) -> dict[str, Any]:
    return {
        "file": file,
        "path": path,
        "value": {"mode": "field", "field": "rate", "scale": 100.0},
        "numerator": {"mode": "field", "field": "k"},
        "denominator": {"mode": "field", "field": "n"},
        "ci": _ci_fields("lower", "upper"),
    }


def build_charts(root: Path, *, run_dir: Path) -> list[dict[str, Any]]:
    """Return the five chart documents (their CSV twins are written separately)."""
    metrics_file = f"results/runs/{run_dir.name}/metrics.json"
    charts: list[dict[str, Any]] = []

    leak_rows: list[dict[str, Any]] = []
    for key, label_en, label_de in BASELINES:
        source = _spec(
            "results/leak_rate.json",
            f"baselines.{key}",
            value_field="leak_rate_pct",
            value_scale=1.0,
            numerator_field="leaked_enquiries",
            denominator_field="records",
            ci_field="leak_wilson_pct",
        )
        leak_rows.append(
            {
                "label": {"en": label_en, "de": label_de},
                "segments": [_segment(root, "leak_rate", source)],
            }
        )
    charts.append(
        {
            "schema_version": BUNDLE_SCHEMA,
            "id": "leak-rate-by-detector",
            "title": {
                "en": "Leak rate by detector (synthetic test, lower is better)",
                "de": "Leckrate je Detektor (synthetischer Test, niedriger ist besser)",
            },
            "unit": PERCENT,
            "series": [
                {
                    "key": "leak_rate",
                    "label": {
                        "en": "Enquiries with a leaked gold value",
                        "de": "Anfragen mit geleaktem Gold-Wert",
                    },
                }
            ],
            "rows": leak_rows,
        }
    )

    recall_rows: list[dict[str, Any]] = []
    for type_name in TYPE_ORDER:
        segments = []
        for baseline in ("B1", "B2"):
            source = _spec(
                "results/detection_synthetic.json",
                f"baselines.{baseline}.per_type.{type_name}",
                value_field="recall_pct",
                value_scale=1.0,
                numerator_field="protected",
                denominator_field="gold",
                ci_field="recall_wilson_pct",
            )
            segments.append(_segment(root, baseline, source))
        recall_rows.append(
            {
                "label": {"en": type_name, "de": type_name},
                "segments": segments,
            }
        )
    charts.append(
        {
            "schema_version": BUNDLE_SCHEMA,
            "id": "synthetic-recall-by-type",
            "title": {
                "en": "Recall per personal-data type, B1 vs B2 (synthetic test)",
                "de": "Recall je Datentyp, B1 vs B2 (synthetischer Test)",
            },
            "unit": PERCENT,
            "series": [
                {
                    "key": "B1",
                    "label": {"en": "B1 — Presidio defaults", "de": "B1 — Presidio-Standard"},
                },
                {
                    "key": "B2",
                    "label": {"en": "B2 — full configuration", "de": "B2 — volle Konfiguration"},
                },
            ],
            "rows": recall_rows,
        }
    )

    germeval_rows: list[dict[str, Any]] = []
    for _type_name, label_en, label_de, path in (
        ("PERSON", "PERSON", "PERSON", "per_type.PERSON"),
        ("ORG", "ORG", "ORG", "per_type.ORG"),
        ("PERSON+ORG", "PERSON+ORG (micro)", "PERSON+ORG (micro)", "micro_primary"),
    ):
        segments = []
        for baseline, _, _ in BASELINES:
            source = _spec(
                "results/detection_germeval.json",
                f"baselines.{baseline}.{path}",
                value_field="recall_pct",
                value_scale=1.0,
                numerator_field="protected",
                denominator_field="gold",
                ci_field="recall_wilson_pct",
            )
            segments.append(_segment(root, baseline, source))
        germeval_rows.append(
            {
                "label": {"en": label_en, "de": label_de},
                "segments": segments,
            }
        )
    charts.append(
        {
            "schema_version": BUNDLE_SCHEMA,
            "id": "germeval-recall-by-detector",
            "title": {
                "en": "GermEval PERSON and ORG recall by detector",
                "de": "GermEval-PERSON- und ORG-Recall je Detektor",
            },
            "unit": PERCENT,
            "series": [{"key": key, "label": {"en": en, "de": de}} for key, en, de in BASELINES],
            "rows": germeval_rows,
        }
    )

    accuracy_rows: list[dict[str, Any]] = []
    for backend, label_en, label_de in BACKENDS:
        segments = [
            _segment(
                root,
                metric,
                _rate_spec(metrics_file, f"per_backend.{backend}.{metric}"),
            )
            for metric in ("joint", "category_only", "urgency_only")
        ]
        accuracy_rows.append(
            {
                "label": {"en": label_en, "de": label_de},
                "segments": segments,
            }
        )
    charts.append(
        {
            "schema_version": BUNDLE_SCHEMA,
            "id": "triage-accuracy-by-backend",
            "title": {
                "en": "Triage accuracy by backend (40-enquiry subset)",
                "de": "Triage-Genauigkeit je Backend (40-Anfragen-Teilmenge)",
            },
            "unit": PERCENT,
            "series": [
                {
                    "key": "joint",
                    "label": {
                        "en": "Joint category + urgency",
                        "de": "Kategorie und Dringlichkeit gemeinsam",
                    },
                },
                {
                    "key": "category_only",
                    "label": {"en": "Category only", "de": "Nur Kategorie"},
                },
                {
                    "key": "urgency_only",
                    "label": {"en": "Urgency only", "de": "Nur Dringlichkeit"},
                },
            ],
            "rows": accuracy_rows,
        }
    )

    latency_rows: list[dict[str, Any]] = []
    for backend, label_en, label_de in BACKENDS:
        latency_rows.append(
            {
                "label": {"en": label_en, "de": label_de},
                "segments": [_latency_segment(root, "median_latency", metrics_file, backend)],
            }
        )
    charts.append(
        {
            "schema_version": BUNDLE_SCHEMA,
            "id": "triage-latency-by-backend",
            "title": {
                "en": "Median triage latency by backend",
                "de": "Mediane Triage-Latenz je Backend",
            },
            "unit": MILLISECONDS,
            "series": [
                {
                    "key": "median_latency",
                    "label": {
                        "en": "Median latency (ms)",
                        "de": "Mediane Latenz (ms)",
                    },
                }
            ],
            "rows": latency_rows,
        }
    )
    return charts


def write_chart_csv(path: Path, chart: Mapping[str, Any]) -> None:
    """Write the CSV twin of one chart document."""
    path.parent.mkdir(parents=True, exist_ok=True)
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(
        [
            "chart_id",
            "row_en",
            "row_de",
            "series",
            "value",
            "numerator",
            "denominator",
            "n",
            "ci_low",
            "ci_high",
            "source_file",
            "source_path",
        ]
    )
    rows = chart["rows"]
    for row in rows:
        for segment in row["segments"]:
            interval = segment["ci95"] or [None, None]
            source = segment["source"]
            writer.writerow(
                [
                    chart["id"],
                    row["label"]["en"],
                    row["label"]["de"],
                    segment["series"],
                    _csv_number(segment["value"]),
                    "" if segment["numerator"] is None else segment["numerator"],
                    "" if segment["denominator"] is None else segment["denominator"],
                    "" if segment["n"] is None else segment["n"],
                    "" if interval[0] is None else _csv_number(interval[0]),
                    "" if interval[1] is None else _csv_number(interval[1]),
                    source["file"],
                    source["path"],
                ]
            )
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(buffer.getvalue(), encoding="utf-8")
    temporary.replace(path)


def _csv_number(value: object) -> str:
    number = _number(value)
    if number.is_integer():
        return str(int(number))
    return f"{number:.1f}"


def build_worked_example(root: Path, examples_path: Path | None = None) -> dict[str, Any]:
    """Return the three-column worked example the page opens with."""
    path = examples_path or (root / "results" / "page" / "examples.json")
    examples = json.loads(path.read_text("utf-8"))
    if not isinstance(examples, list):
        raise ValueError("examples.json is not an array")
    chosen = next(
        (
            item
            for item in examples
            if isinstance(item, dict) and str(item.get("reply_draft_restored") or "").strip()
        ),
        None,
    )
    if chosen is None:
        raise ValueError("examples.json has no example with a restored draft")
    return {
        "schema_version": BUNDLE_SCHEMA,
        "id": str(chosen["id"]),
        "source_example": "results/page/examples.json",
        "columns": {
            "original": chosen["original"],
            "redacted": chosen["redacted"],
            "reply_draft_restored": chosen["reply_draft_restored"],
        },
        "spans": chosen.get("spans") or [],
        "misses": chosen.get("misses") or [],
    }


def build_tryit_samples(root: Path, examples_path: Path | None = None) -> dict[str, Any]:
    """Return the sample texts for the browser try-it box."""
    path = examples_path or (root / "results" / "page" / "examples.json")
    examples = json.loads(path.read_text("utf-8"))
    if not isinstance(examples, list):
        raise ValueError("examples.json is not an array")
    usable = [item for item in examples if isinstance(item, dict) and item.get("original")]
    if not usable:
        raise ValueError("examples.json has no usable example")
    chosen: list[dict[str, Any]] = []
    seen_categories: set[str] = set()
    for item in usable:
        category = str(item.get("category") or "")
        if chosen and category in seen_categories:
            continue
        if not str(item.get("reply_draft_restored") or "").strip() and not chosen:
            continue
        chosen.append(item)
        seen_categories.add(category)
        if len(chosen) == 2:
            break
    if len(chosen) < 2:
        chosen = usable[:2]
    return {
        "schema_version": BUNDLE_SCHEMA,
        "source": "results/page/examples.json",
        "samples": [
            {
                "id": f"tryit-{index}",
                "source_example_id": str(item["id"]),
                "label": {
                    "en": "Sample enquiry" if index == 1 else "Second sample enquiry",
                    "de": "Beispielanfrage" if index == 1 else "Zweite Beispielanfrage",
                },
                "text": item["original"],
            }
            for index, item in enumerate(chosen, start=1)
        ],
    }


def build_demo_enquiries(root: Path) -> dict[str, Any]:
    """Return the first three dev-split enquiries, in the order ``make demo`` posts them."""
    records: list[dict[str, Any]] = []
    with (root / "data" / "synthetic" / "dev.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            if len(records) >= 3:
                break
            if line.strip():
                payload = json.loads(line)
                if isinstance(payload, dict):
                    records.append(payload)
    if len(records) != 3:
        raise ValueError("expected three dev-split demo enquiries")
    return {
        "schema_version": BUNDLE_SCHEMA,
        "source": "data/synthetic/dev.jsonl",
        "count": 3,
        "enquiries": records,
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _purpose(relative: str, charts: Mapping[str, Mapping[str, Any]]) -> tuple[str, str, str]:
    """Return ``(purpose, source, command)`` for one bundle file."""
    if relative == "kpis.json":
        return (
            "One entry per headline KPI with numerator, denominator, 95% CI and EN/DE labels.",
            "; ".join(SOURCE_TRIAGE_ORDER),
            "make page-data",
        )
    if relative == "results.json":
        return (
            "Page-shaped detection and triage block.",
            "results/detection_synthetic.json; results/detection_germeval.json; "
            "results/runs/<triage-run>/metrics.json",
            "make page-data",
        )
    if relative == "examples.json":
        return (
            "15 gallery cards (three per category) with spans, misses and restored drafts.",
            "results/page/demos.json; results/page/responses/*.json",
            "make page-data",
        )
    if relative == "demos.json":
        return (
            "The 15 demo examples selected for the page, with hashes and blocked markers.",
            "results/runs/<triage-run>/metrics.json",
            "committed input; make page-data rebuilds examples from it",
        )
    if relative == "worked-example.json":
        return (
            "The three-column worked example (original, what the model saw, restored draft).",
            "results/page/examples.json",
            "make page-data",
        )
    if relative == "tryit-samples.json":
        return (
            "Sample texts for the browser try-it box.",
            "results/page/examples.json",
            "make page-data",
        )
    if relative == "demo-enquiries.json":
        return (
            "The three synthetic dev-split enquiries posted by `make demo`.",
            "data/synthetic/dev.jsonl",
            "make page-data",
        )
    if relative == "n8n-workflow.png":
        return (
            "n8n workflow canvas screenshot (light theme, 1280 px, synthetic data).",
            "n8n review stack",
            "make e2e (screenshot step)",
        )
    if relative.startswith("charts/"):
        stem = Path(relative).stem
        chart = charts.get(stem)
        if relative.endswith(".csv"):
            return (
                f"CSV twin of charts/{stem}.json.",
                f"charts/{stem}.json",
                "make page-data",
            )
        title = chart["title"]["en"] if chart else stem
        files = sorted(
            {
                str(segment["source"]["file"])
                for row in (chart["rows"] if chart else [])
                for segment in row["segments"]
            }
        )
        return (f"Chart data: {title}.", "; ".join(files), "make page-data")
    if relative.startswith("responses/"):
        return (
            "Stored backend response for one gallery example (placeholders only).",
            "results/runs/<backend>/responses/",
            "committed input; make page-data copies it into the bundle",
        )
    if relative.startswith("screenshots/"):
        name = Path(relative).stem
        purposes = {
            "n8n-execution-success": (
                "Workflow canvas of one finished demo execution, zoom to fit, "
                "both branches and item counts visible."
            ),
            "n8n-executions-list": "Executions list with the successful demo runs.",
            "n8n-review-form": (
                "Review form for one synthetic demo enquiry, with the edit decision "
                "selected and the corrected draft filled in before submit."
            ),
            "n8n-result": "Rendered card of the edited reply record.",
        }
        return (
            purposes.get(name, "Review-stack screenshot."),
            "`make demo` review stack (headless Chromium)",
            "scripts/demo/capture_shots.py (after make demo)",
        )
    return ("Bundle file.", "committed input", "make page-data")


def write_manifest(root: Path, output_dir: Path, charts: Sequence[Mapping[str, Any]]) -> Path:
    """Write ``MANIFEST.md`` with one row per file in the bundle."""
    chart_index = {str(chart["id"]): chart for chart in charts}
    files = sorted(
        path for path in output_dir.rglob("*") if path.is_file() and path.name != "MANIFEST.md"
    )
    lines = [
        "# Page bundle manifest",
        "",
        "Every file the page needs, rebuilt from the committed result files with "
        "`make page-data`. Two runs over the same inputs write the same bytes.",
        "",
        f"Files: {len(files)} (this manifest is not listed).",
        "",
        "| File | Purpose | Source | Generating command | SHA-256 |",
        "|---|---|---|---|---|",
    ]
    for path in files:
        relative = path.relative_to(output_dir).as_posix()
        purpose, source, command = _purpose(relative, chart_index)
        lines.append(f"| `{relative}` | {purpose} | {source} | `{command}` | `{_sha256(path)}` |")
    lines.append("")
    target = output_dir / "MANIFEST.md"
    target.write_text("\n".join(lines), encoding="utf-8")
    return target


def _seed_inputs(root: Path, output_dir: Path) -> None:
    """Copy the committed demo inputs into a fresh output directory."""
    source_dir = root / "results" / "page"
    if source_dir.resolve() == output_dir.resolve():
        return
    output_dir.mkdir(parents=True, exist_ok=True)
    demos = source_dir / "demos.json"
    if demos.is_file():
        shutil.copyfile(demos, output_dir / "demos.json")
    responses = source_dir / "responses"
    if responses.is_dir():
        target = output_dir / "responses"
        target.mkdir(parents=True, exist_ok=True)
        for path in sorted(responses.glob("*.json")):
            shutil.copyfile(path, target / path.name)
    screenshots = source_dir / "screenshots"
    if screenshots.is_dir():
        target = output_dir / "screenshots"
        target.mkdir(parents=True, exist_ok=True)
        for path in sorted(screenshots.glob("*.png")):
            shutil.copyfile(path, target / path.name)
    workflow = source_dir / "n8n-workflow.png"
    if workflow.is_file():
        shutil.copyfile(workflow, output_dir / "n8n-workflow.png")


def export_bundle(
    *,
    root: Path,
    output_dir: Path,
    backend: str,
    detection_path: Path | None = None,
) -> dict[str, Path]:
    """Rebuild the whole page bundle; return the paths written."""
    run_dir = latest_run_dir(root)
    _seed_inputs(root, output_dir)
    examples_path, results_path, _ = export_page(
        root=root,
        run_dir=run_dir,
        backend=backend,
        detection_path=detection_path,
        output_dir=output_dir,
    )
    kpis = build_kpis(root, run_dir=run_dir)
    charts = build_charts(root, run_dir=run_dir)
    charts_dir = output_dir / "charts"
    for chart in charts:
        path = charts_dir / f"{chart['id']}.json"
        _write_json(path, chart)
        write_chart_csv(charts_dir / f"{chart['id']}.csv", chart)
    kpis_path = output_dir / "kpis.json"
    _write_json(kpis_path, kpis)
    worked_path = output_dir / "worked-example.json"
    _write_json(worked_path, build_worked_example(root, examples_path))
    samples_path = output_dir / "tryit-samples.json"
    _write_json(samples_path, build_tryit_samples(root, examples_path))
    demo_path = output_dir / "demo-enquiries.json"
    _write_json(demo_path, build_demo_enquiries(root))
    manifest_path = write_manifest(root, output_dir, charts)
    return {
        "examples": examples_path,
        "results": results_path,
        "kpis": kpis_path,
        "worked_example": worked_path,
        "tryit_samples": samples_path,
        "demo_enquiries": demo_path,
        "manifest": manifest_path,
        "charts": charts_dir,
    }


__all__ = [
    "build_charts",
    "build_demo_enquiries",
    "build_kpis",
    "build_tryit_samples",
    "build_worked_example",
    "export_bundle",
    "write_chart_csv",
    "write_manifest",
]

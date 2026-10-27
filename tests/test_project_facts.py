"""project-facts.yml is true, and every bolded README number points at a result."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
FACTS = ROOT / "project-facts.yml"
README = ROOT / "README.md"


def _load() -> dict[str, Any]:
    payload = yaml.safe_load(FACTS.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _walk(node: object) -> list[str]:
    """Return every string value stored under an ``evidence`` key."""
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "evidence" and isinstance(value, str):
                found.append(value)
            else:
                found.extend(_walk(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_walk(item))
    return found


def test_every_evidence_path_exists() -> None:
    missing = [path for path in _walk(_load()) if not (ROOT / path).is_file()]
    assert missing == []


def test_headline_numbers_have_an_existing_result_file() -> None:
    facts = _load()
    headline = facts["headline_numbers"]
    assert len(headline) >= 8
    for entry in headline:
        assert isinstance(entry["label"], str) and entry["label"]
        assert isinstance(entry["value"], (int, float))
        assert (ROOT / entry["evidence"]).is_file(), entry


def test_every_bolded_number_in_the_readme_is_a_link() -> None:
    text = README.read_text(encoding="utf-8")
    for match in re.finditer(r"\*\*(.+?)\*\*", text, flags=re.DOTALL):
        bold = match.group(1)
        if not re.search(r"\d", bold):
            continue
        assert "](" in bold, f"bold number without a result link: {bold!r}"


def test_headline_result_files_are_linked_from_the_readme() -> None:
    readme = README.read_text(encoding="utf-8")
    for entry in _load()["headline_numbers"]:
        assert f"]({entry['evidence']})" in readme, entry["evidence"]


def test_facts_cover_the_required_sections() -> None:
    facts = _load()
    for key in (
        "project",
        "repository",
        "data",
        "stack",
        "verification",
        "headline_numbers",
        "detection",
        "triage",
        "n8n",
        "page_data",
        "third_party",
        "claims",
    ):
        assert key in facts
    assert facts["project"]["licence"] == "Apache-2.0"
    assert facts["project"]["publication"].startswith("local-only")
    assert facts["data"]["real_person_examples"] == 0
    assert facts["repository"]["tracked_germeval_files"] == 0

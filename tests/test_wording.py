"""Wording limits: no over-claims in the public documents."""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PUBLIC_DOCS = [
    REPO_ROOT / "README.md",
    REPO_ROOT / "NOTICE",
    REPO_ROOT / "THIRD_PARTY.md",
    REPO_ROOT / "CHANGELOG.md",
    REPO_ROOT / "docs" / "contracts.md",
    REPO_ROOT / "docs" / "n8n.md",
    REPO_ROOT / "docs" / "task-notes" / "T01.md",
    REPO_ROOT / "docs" / "task-notes" / "T02.md",
    REPO_ROOT / "docs" / "task-notes" / "T03.md",
    REPO_ROOT / "docs" / "task-notes" / "T13.md",
]

BANNED = [
    "gdpr" + "-compliant",
    "gdpr" + " compliant",
    "fully " + "offline",
    "anonymis" + "ed",
    "anonymiz" + "ed",
]


@pytest.mark.parametrize("document", PUBLIC_DOCS, ids=lambda path: path.name)
@pytest.mark.parametrize("phrase", BANNED)
def test_no_overclaim_language(document: Path, phrase: str) -> None:
    text = document.read_text(encoding="utf-8").lower()
    assert phrase not in text


def test_readme_states_the_limits() -> None:
    text = " ".join((REPO_ROOT / "README.md").read_text(encoding="utf-8").lower().split())
    assert "not legal advice" in text
    assert "pseudonymisation" in text
    assert "pseudonymises" in text
    assert "runs entirely on your machine" in text
    assert "apache-2.0" in text


def test_readme_points_at_measured_results() -> None:
    """T11/T12 replaced the "not evaluated yet" foundation status."""
    text = " ".join((REPO_ROOT / "README.md").read_text(encoding="utf-8").lower().split())
    assert "results/" in text
    assert "test splits" in text

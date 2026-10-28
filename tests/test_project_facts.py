"""project-facts.yml follows the review template and every number is traceable."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
FACTS = ROOT / "project-facts.yml"
README = ROOT / "README.md"

TEMPLATE_FIELDS = {
    "title",
    "slug",
    "repo",
    "live_url",
    "stack",
    "stack_label",
    "one_line",
    "mention",
    "summary",
    "tags",
    "keywords",
    "claim_limits",
    "bullets",
    "resources",
}
ALLOWED_TAGS = {"python", "nlp", "llm", "automation", "api", "testing", "ci"}
BULLET_KINDS = ("outcome", "technical", "stack")
LOCAL_ONLY_MARKERS = ("base_commit", "remotes:", "unpublished", "local-release", "branch:")


def _load() -> dict[str, Any]:
    payload = yaml.safe_load(FACTS.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _bullets(facts: dict[str, Any]) -> list[dict[str, Any]]:
    bullets = facts["bullets"]
    assert isinstance(bullets, list)
    return bullets


def test_matches_the_review_template_field_for_field() -> None:
    assert set(_load()) == TEMPLATE_FIELDS


def test_required_fields_are_filled() -> None:
    facts = _load()
    for key in TEMPLATE_FIELDS - {"bullets", "resources", "stack", "tags"}:
        assert isinstance(facts[key], str) and facts[key].strip(), key
    assert isinstance(facts["stack"], list) and facts["stack"]
    assert isinstance(facts["tags"], list) and facts["tags"]
    assert set(facts["tags"]) <= ALLOWED_TAGS
    assert isinstance(facts["resources"], list)


def test_one_line_is_lower_case_without_a_full_stop() -> None:
    one_line = _load()["one_line"]
    assert one_line[:1] == one_line[:1].lower()
    assert not one_line.rstrip().endswith(".")


def test_claim_limits_state_the_limits() -> None:
    limits = _load()["claim_limits"].lower()
    for phrase in (
        "independent project",
        "not anonymisation",
        "not legal advice",
        "not on real customer mail",
    ):
        assert phrase in limits, phrase


def test_three_bullets_and_every_number_traces_to_a_file() -> None:
    bullets = _bullets(_load())
    assert [bullet["kind"] for bullet in bullets] == list(BULLET_KINDS)
    for bullet in bullets:
        text = bullet["text"]
        assert isinstance(text, str) and len(text) > 20
        for number in bullet["numbers"]:
            assert number["value"]
            assert number["value"] in text, number
            evidence = ROOT / number["file"]
            assert evidence.is_file(), number
            contents = evidence.read_text(encoding="utf-8").lower()
            assert number["contains"].lower() in contents, number


def test_every_bare_number_in_a_bullet_is_listed() -> None:
    for bullet in _bullets(_load()):
        listed = [str(number["value"]) for number in bullet["numbers"]]
        for token in re.findall(r"\d[\d.,]*\s?(?:[kKmM]|%|x)?", bullet["text"]):
            bare = token.strip()
            if not re.search(r"\d{2,}|%|[kKmM]$", bare):
                continue
            assert any(value in bare or bare in value for value in listed), (bare, listed)


def test_resources_are_credited() -> None:
    resources = _load()["resources"]
    assert resources == [
        {
            "name": "GermEval 2014 NER",
            "owner": "Benikova, Biemann, Reznicek (LREC 2014)",
            "licence": "CC BY 4.0",
            "url": "https://sites.google.com/site/germeval2014ner/home",
            "used_as": "held-out detection rates only; not redistributed",
        }
    ]


def test_no_local_only_release_state() -> None:
    raw = FACTS.read_text(encoding="utf-8")
    for marker in LOCAL_ONLY_MARKERS:
        assert marker not in raw, marker


def test_every_bolded_number_in_the_readme_is_a_link() -> None:
    text = README.read_text(encoding="utf-8")
    for match in re.finditer(r"\*\*(.+?)\*\*", text, flags=re.DOTALL):
        bold = match.group(1)
        if not re.search(r"\d", bold):
            continue
        assert "](" in bold, f"bold number without a result link: {bold!r}"

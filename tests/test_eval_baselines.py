"""B0 rules: behaviour required by METHOD.md section 3.1 and the generated JSON."""

from __future__ import annotations

import json
from pathlib import Path

from ai_privacy_filter.models import EntityType
from eval import baselines

REPO_ROOT = Path(__file__).resolve().parents[1]
TRYIT_RULES = REPO_ROOT / "web" / "tryit-rules.json"


def detection_types(text: str) -> set[EntityType]:
    return {span.type for span in baselines.detect_b0(text)}


def test_email_rule_requires_local_at_domain_dot_tld() -> None:
    spans = baselines.detect_b0("Kontakt: max.muster@example.org, danke.")
    assert [span.text for span in spans] == ["max.muster@example.org"]
    assert spans[0].type is EntityType.EMAIL
    assert spans[0].source == "b0:email"
    assert not detection_types("kein@email")


def test_url_rule_trims_trailing_punctuation() -> None:
    spans = baselines.detect_b0("Siehe https://example.org/pfad?q=1, dann weiter.")
    assert spans[0].text == "https://example.org/pfad?q=1"
    www = baselines.detect_b0("(www.example.org).")
    assert www[0].text == "www.example.org"


def test_iban_rule_requires_a_valid_mod_97_checksum() -> None:
    valid = baselines.detect_b0("IBAN DE89 3704 0044 0532 0130 00 Ende")
    assert [span.type for span in valid] == [EntityType.IBAN]
    invalid = baselines.detect_b0("IBAN DE89 3704 0044 0532 0130 01 Ende")
    assert EntityType.IBAN not in {span.type for span in invalid}


def test_phone_rule_accepts_german_forms_and_rejects_dot_groups() -> None:
    for text in (
        "0170 56245510",
        "+49 (0)30 123456",
        "0049-30-123456",
        "(04675) 27742",
        "030/123456",
    ):
        assert baselines.detect_b0(f"Tel: {text}."), text
    assert baselines.detect_b0("030.123456") == []
    assert baselines.detect_b0("0170 12") == []


def test_vat_id_is_de_plus_nine_digits() -> None:
    spans = baselines.detect_b0("USt-IdNr. DE123456789.")
    assert [span.type for span in spans] == [EntityType.VAT_ID]
    assert baselines.detect_b0("DE1234567890") == []


def test_postcode_city_requires_capitalised_city_words() -> None:
    spans = baselines.detect_b0("26382 Karlsruhe")
    assert [span.text for span in spans] == ["26382 Karlsruhe"]
    assert baselines.detect_b0("26382 karlsruhe") == []


def test_street_rule_requires_a_suffix_and_house_number() -> None:
    spans = baselines.detect_b0("Irmingard-Stolze-Straße 2")
    assert [span.type for span in spans] == [EntityType.ADDRESS_STREET]
    assert baselines.detect_b0("Hauptweg 12a")
    assert baselines.detect_b0("Irmingard-Stolze 2") == []


def test_date_of_birth_needs_a_birth_cue_within_forty_characters() -> None:
    assert baselines.detect_b0("Geburtsdatum: 12.03.1980")
    assert baselines.detect_b0("Termin am 12.03.1980") == []


def test_b0_never_emits_person_or_org() -> None:
    text = "Max Müller von der Beispiel GmbH, Irmingard-Stolze-Straße 2"
    types = detection_types(text)
    assert EntityType.PERSON not in types
    assert EntityType.ORG not in types


def test_longest_match_wins_then_type_priority() -> None:
    text = "max@example.org"
    spans = baselines.detect_b0(text)
    assert len(spans) == 1
    assert spans[0].type is EntityType.EMAIL
    assert spans[0].source == "b0:email"


def test_b0_is_deterministic_and_has_fixed_scores() -> None:
    text = "Mail an max@example.org oder 0170 56245510."
    first = baselines.detect_b0(text)
    second = baselines.detect_b0(text)
    assert first == second
    assert all(span.score == 1.0 for span in first)
    assert all(span.source.startswith("b0:") for span in first)


def test_committed_tryit_rules_are_generated_from_the_python_rules() -> None:
    assert TRYIT_RULES.is_file()
    committed = TRYIT_RULES.read_text(encoding="utf-8")
    assert committed == baselines.render_rules_json()
    payload = json.loads(committed)
    assert [rule["name"] for rule in payload["rules"]] == [rule.name for rule in baselines.RULES]
    assert payload["type_priority"] == [entity.value for entity in baselines.B0_TYPE_PRIORITY]

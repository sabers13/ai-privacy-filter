"""B0: the fixed regex-only baseline (the page try-it box rule set).

``METHOD.md`` section 3.1 defines B0 by behaviour, not by a regex spelling. This
module is the single source of truth for those rules: B0 scoring imports
:func:`detect_b0`, and ``web/tryit-rules.json`` is generated from
:func:`export_rules` so the browser box cannot drift into a different rule set.

B0 has **no PERSON and no ORG rule**. Its recall for those two types is 0 % by
construction; that is reported, not hidden.

The rule set is never tuned. The German recognizers used by B2 are deliberately
not reused here: importing them would make the page box a statistical detector
and would blur the comparison the baseline exists to make.
"""

from __future__ import annotations

import argparse
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from ai_privacy_filter.models import EntityType, Span

REPO_ROOT: Final = Path(__file__).resolve().parents[1]
TRYIT_RULES_PATH: Final = REPO_ROOT / "web" / "tryit-rules.json"
RULES_SCHEMA_VERSION: Final = 1

#: Overlap priority from METHOD.md section 6.4; lower index wins.
B0_TYPE_PRIORITY: Final[tuple[EntityType, ...]] = (
    EntityType.EMAIL,
    EntityType.IBAN,
    EntityType.URL,
    EntityType.VAT_ID,
    EntityType.PHONE,
    EntityType.ADDRESS_STREET,
    EntityType.POSTCODE_CITY,
    EntityType.DATE_OF_BIRTH,
    EntityType.ORG,
    EntityType.PERSON,
)
_PRIORITY_INDEX: Final = {entity: index for index, entity in enumerate(B0_TYPE_PRIORITY)}

_STREET_SUFFIXES: Final[tuple[str, ...]] = (
    "[Ss]traße",
    "[Ss]trasse",
    "[Ss]tr\\.",
    "[Ww]eg",
    "[Pp]latz",
    "[Aa]llee",
    "[Rr]ing",
    "[Gg]asse",
    "[Dd]amm",
    "[Uu]fer",
    "[Bb]erg",
    "[Ss]teig",
    "[Pp]fad",
    "[Hh]of",
    "[Mm]arkt",
    "[Cc]haussee",
    "[Zz]eile",
    "[Ww]inkel",
    "[Bb]ogen",
)
_STREET_BODY: Final = "[A-ZÄÖÜ][\\wäöüß.\\-]*(?: [\\wäöüß.\\-]+)?"
_STREET_PATTERN: Final = (
    rf"(?<![\w\-]){_STREET_BODY}(?:{'|'.join(_STREET_SUFFIXES)})"
    r"\s+\d{1,4}[A-Za-z]?(?![\w\-])"
)

_EMAIL_PATTERN: Final = (
    r"(?<![\w.+\-])[A-Za-z0-9._%+!#$&'*/=?^`{|}~\-]+"
    r"@(?:[A-Za-z0-9](?:[A-Za-z0-9\-]*[A-Za-z0-9])?\.)+[A-Za-z]{2,}(?![\w\-])"
)
_URL_PATTERN: Final = r"(?<![\w@/])(?:https?://|www\.)[^\s<>\"']+"
_IBAN_PATTERN: Final = r"(?<![\w])DE\d{2}(?: ?\d{4}){4} ?\d{2}(?!\d)"
_PHONE_PATTERN: Final = r"(?<![\w+])(?:\+49|0049|\(?0)[\d ()/\-]{4,}\d(?![\w])"
_VAT_ID_PATTERN: Final = r"(?<![\w])DE\d{9}(?!\d)"
_POSTCODE_CITY_PATTERN: Final = (
    r"(?<![\w])\d{5} (?:[A-ZÄÖÜ][\wäöüß.\-]*)(?: [A-ZÄÖÜ][\wäöüß.\-]*)*(?![\w])"
)
_DATE_PATTERN: Final = r"(?<!\d)\d{1,2}\.\d{1,2}\.\d{4}(?!\d)"

DOB_CUES: Final[tuple[str, ...]] = ("geboren", "geburtsdatum", "geburtstag", "geb.")
DOB_CONTEXT_WINDOW: Final = 40
URL_TRAILING: Final = ".,;:!?)]}"
PHONE_MIN_SIGNIFICANT_DIGITS: Final = 6


@dataclass(frozen=True, slots=True)
class Rule:
    """One B0 pattern plus the name of its post-match validation."""

    name: str
    entity_type: EntityType
    pattern: str
    validation: str

    def describe(self) -> dict[str, str]:
        return {
            "name": self.name,
            "type": self.entity_type.value,
            "pattern": self.pattern,
            "validation": self.validation,
        }


RULES: Final[tuple[Rule, ...]] = (
    Rule("email", EntityType.EMAIL, _EMAIL_PATTERN, "none"),
    Rule("url", EntityType.URL, _URL_PATTERN, "trim_trailing_punctuation"),
    Rule("iban", EntityType.IBAN, _IBAN_PATTERN, "iban_mod97"),
    Rule("phone", EntityType.PHONE, _PHONE_PATTERN, "phone_min_6_significant_digits"),
    Rule("vat_id", EntityType.VAT_ID, _VAT_ID_PATTERN, "none"),
    Rule("postcode_city", EntityType.POSTCODE_CITY, _POSTCODE_CITY_PATTERN, "none"),
    Rule("address_street", EntityType.ADDRESS_STREET, _STREET_PATTERN, "none"),
    Rule("date_of_birth", EntityType.DATE_OF_BIRTH, _DATE_PATTERN, "birth_cue_within_40"),
)

_COMPILED: Final = {rule.name: re.compile(rule.pattern) for rule in RULES}


def _iban_mod97(value: str) -> bool:
    compact = value.replace(" ", "").upper()
    if len(compact) < 15 or not compact.isalnum():
        return False
    rearranged = compact[4:] + compact[:4]
    digits = []
    for character in rearranged:
        if character.isdigit():
            digits.append(character)
        elif "A" <= character <= "Z":
            digits.append(str(ord(character) - ord("A") + 10))
        else:
            return False
    return int("".join(digits)) % 97 == 1


def _significant_phone_digits(value: str) -> str:
    digits = "".join(character for character in value if character.isdigit())
    if digits.startswith("0049"):
        digits = digits[4:]
    elif digits.startswith("49") and value.lstrip().startswith("+49"):
        digits = digits[2:]
    if digits.startswith("0"):
        digits = digits[1:]
    return digits


def _dob_has_cue(text: str, start: int, end: int) -> bool:
    window = text[max(0, start - DOB_CONTEXT_WINDOW) : end + DOB_CONTEXT_WINDOW].casefold()
    return any(cue in window for cue in DOB_CUES)


def _accept(rule: Rule, text: str, start: int, end: int) -> bool:
    if rule.validation == "iban_mod97":
        return _iban_mod97(text[start:end])
    if rule.validation == "phone_min_6_significant_digits":
        return len(_significant_phone_digits(text[start:end])) >= PHONE_MIN_SIGNIFICANT_DIGITS
    if rule.validation == "birth_cue_within_40":
        return _dob_has_cue(text, start, end)
    return True


def _trim_url(text: str, start: int, end: int) -> int | None:
    while end > start and text[end - 1] in URL_TRAILING:
        end -= 1
    prefix = 8 if text[start : start + 8].lower().startswith("https://") else 7
    if text[start : start + 4].lower().startswith("www."):
        prefix = 4
    if end - start <= prefix:
        return None
    return end


def _raw_candidates(text: str) -> list[tuple[Rule, int, int]]:
    candidates: list[tuple[Rule, int, int]] = []
    for rule in RULES:
        for match in _COMPILED[rule.name].finditer(text):
            start, end = match.span()
            if rule.validation == "trim_trailing_punctuation":
                trimmed = _trim_url(text, start, end)
                if trimmed is None:
                    continue
                end = trimmed
            if end <= start:
                continue
            if not text[start:end].strip():
                continue
            if not _accept(rule, text, start, end):
                continue
            candidates.append((rule, start, end))
    return candidates


def _resolve(text: str, candidates: Sequence[tuple[Rule, int, int]]) -> list[Span]:
    """Longest match first, then type priority, then leftmost (METHOD §3.1)."""

    ranked = sorted(
        candidates,
        key=lambda item: (
            -(item[2] - item[1]),
            _PRIORITY_INDEX[item[0].entity_type],
            item[1],
            item[0].name,
        ),
    )
    kept: list[Span] = []
    for rule, start, end in ranked:
        if any(start < span.end and span.start < end for span in kept):
            continue
        kept.append(
            Span(
                start=start,
                end=end,
                type=rule.entity_type,
                text=text[start:end],
                score=1.0,
                source=f"b0:{rule.name}",
            )
        )
    return sorted(kept, key=lambda span: (span.start, span.end, span.type.value))


def detect_b0(text: str) -> list[Span]:
    """Return B0 spans for ``text``. No model, no name list, no network."""

    if not text:
        return []
    return _resolve(text, _raw_candidates(text))


def export_rules() -> dict[str, object]:
    """Return the browser rule document generated from this module."""

    return {
        "schema_version": RULES_SCHEMA_VERSION,
        "source": "eval/baselines.py",
        "note": (
            "B0 is the fixed regex-only baseline. It has no PERSON or ORG rule. "
            "Longest match wins, then the fixed type priority, then leftmost. "
            "Every span has score 1.0 and source b0:<rule>."
        ),
        "type_priority": [entity.value for entity in B0_TYPE_PRIORITY],
        "overlap": "longest match first, then type priority, then leftmost",
        "rules": [rule.describe() for rule in RULES],
        "validations": {
            "trim_trailing_punctuation": URL_TRAILING,
            "iban_mod97": (
                "move the first four characters to the end, letters to 10-35, mod 97 equals 1"
            ),
            "phone_min_6_significant_digits": (
                "drop a +49/0049 country code and one leading 0, then count digits; "
                "at least 6 must remain"
            ),
            "birth_cue_within_40": {
                "cues": list(DOB_CUES),
                "window": DOB_CONTEXT_WINDOW,
            },
        },
    }


def render_rules_json() -> str:
    """Render the browser rule document exactly as committed."""

    return json.dumps(export_rules(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def write_tryit_rules(path: Path = TRYIT_RULES_PATH) -> Path:
    """Write ``web/tryit-rules.json`` from the Python rules."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_rules_json(), encoding="utf-8")
    return path


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Write web/tryit-rules.json from eval/baselines.py"
    )
    parser.add_argument("--write", action="store_true", help="write the committed rule document")
    parser.add_argument("--print", action="store_true", help="print the rule document to stdout")
    args = parser.parse_args(argv)
    if args.print:
        print(render_rules_json(), end="")
        return 0
    if args.write:
        path = write_tryit_rules()
        print(f"wrote {path.relative_to(REPO_ROOT).as_posix()}")
        return 0
    parser.error("choose --write or --print")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

"""IBANs with a mod-97 checksum.

Formatted IBANs are written in groups of four separated by a space, which is
also how people paste them into an enquiry. The checksum is the strongest
signal available, so both profiles require it: a structurally plausible string
with a wrong check digit is almost always some other code.

Lower-case letters are accepted in both profiles. ``strict`` additionally accepts
hyphens and dots between groups; ``balanced`` only accepts spaces and the
unformatted form.
"""

from __future__ import annotations

import re

from presidio_analyzer import RecognizerResult
from presidio_analyzer.nlp_engine import NlpArtifacts

from ai_privacy_filter.recognizers.base import GermanRecognizer, Settings

ENTITY = "IBAN"

CONTEXT_WORDS = (
    "iban",
    "konto",
    "kontonummer",
    "bankverbindung",
    "bank",
    "bic",
    "überweisen",
    "ueberweisen",
)

MIN_LENGTH = 15
MAX_LENGTH = 34
DE_LENGTH = 22


def compact_iban(iban: str) -> str:
    """Return the IBAN without separators, upper-cased."""

    return re.sub(r"[^A-Za-z0-9]", "", iban).upper()


def checksum_ok(iban: str) -> bool:
    """Return whether ``iban`` is a well-formed IBAN with a valid mod-97 checksum."""

    compact = compact_iban(iban)
    if not MIN_LENGTH <= len(compact) <= MAX_LENGTH:
        return False
    if not (compact[:2].isalpha() and compact[2:4].isdigit()):
        return False
    rearranged = compact[4:] + compact[:4]
    digits = "".join(str(int(char, 36)) for char in rearranged)
    return int(digits) % 97 == 1


class DeIbanRecognizer(GermanRecognizer):
    """Recognise German and other IBANs that pass the mod-97 checksum."""

    entity = ENTITY

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings, name="DeIbanRecognizer", context_words=CONTEXT_WORDS)
        separators = re.escape(settings.iban_separators)
        group_separator = rf"(?:[{separators}]|\r?\n[ \t]*)?" if separators else r"(?:\r?\n[ \t]*)?"
        self._pattern = re.compile(
            r"(?<![A-Za-z0-9])"
            rf"(?P<iban>[A-Za-z]{{2}}\d{{2}}(?:{group_separator}[A-Za-z0-9]{{4}}){{2,7}}"
            rf"(?:{group_separator}[A-Za-z0-9]{{1,4}})?)"
            r"(?![A-Za-z0-9])"
        )

    def analyze(
        self,
        text: str,
        entities: list[str],
        nlp_artifacts: NlpArtifacts | None = None,
    ) -> list[RecognizerResult]:
        """Return IBAN spans with a valid checksum and a per-country length."""

        if not self.accepts(entities):
            return []
        results: list[RecognizerResult] = []
        for match in self._pattern.finditer(text):
            compact = compact_iban(match.group("iban"))
            if not checksum_ok(compact):
                continue
            if compact.startswith("DE") and len(compact) != DE_LENGTH:
                continue
            score = self.boosted(0.95, text, match.start(), match.end(), boost=0.05)
            country = compact[:2]
            results.append(
                self.result(
                    match.start("iban"),
                    match.end("iban"),
                    score,
                    f"IBAN with valid mod-97 checksum ({country})",
                )
            )
        return results

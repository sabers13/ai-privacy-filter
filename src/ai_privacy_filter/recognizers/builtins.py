"""Email and URL recognizers, built on Presidio's bundled patterns.

Presidio's email recognizer validates a candidate with ``tldextract``, whose
first call may download the public-suffix list. Detection must not touch the
network, so the subclass below keeps Presidio's email pattern and replaces only
that validation with an equivalent local check.

The URL recognizer is a pure regular expression and needs no change; it is
registered once for German text.
"""

# mypy: disable-error-code="no-untyped-call"
# Presidio's recognizer constructors have no return annotation.

from __future__ import annotations

import re

from presidio_analyzer import Pattern, RecognizerResult
from presidio_analyzer.nlp_engine import NlpArtifacts
from presidio_analyzer.predefined_recognizers import EmailRecognizer, UrlRecognizer

ENTITY_EMAIL = "EMAIL"
ENTITY_URL = "URL"

# Presidio scores a URL below spaCy's NER score, so a URL that the model also
# calls an organisation would lose the merge. Raising the URL score makes the
# specific entity win at equal length.
_URL_SCORES = {
    "Standard Url": 0.9,
    "Quoted URL": 0.9,
    "Non schema URL": 0.8,
    "Quoted Non-schema URL": 0.8,
}

# Presidio's URL pattern only knows public TLDs, so a scheme URL on a reserved
# domain ("beispiel.example") is cut short. This pattern accepts any scheme URL
# and outscores the partial match.
_GENERIC_SCHEME_URL = Pattern(
    "Generic schema URL",
    r"(?i)\bhttps?://[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)*"
    r"(?::\d{1,5})?(?:/[^\s()<>\"']*)?",
    0.95,
)

# "B.Sc", "M.Eng" and friends are post-nominal academic suffixes, not domains.
_ACADEMIC_SUFFIX = re.compile(r"^[A-ZÄÖÜ]\.\s?(?:Sc|Eng|A|Ed)\.?$")
# "Univ.Pro" (the .pro TLD) is a title fragment, not a link.
_TITLE_FRAGMENT = re.compile(r"^(?:Univ|Dipl|Mag|Ing|Dr|Prof)\.")


def _valid_email(pattern_text: str) -> bool:
    """Structural email check: local part, dotted domain, alphabetic TLD."""

    local, separator, domain = pattern_text.rpartition("@")
    if not separator or not local or not domain:
        return False
    labels = domain.split(".")
    if len(labels) < 2:
        return False
    if any(not label or label.startswith("-") or label.endswith("-") for label in labels):
        return False
    tld = labels[-1]
    return len(tld) >= 2 and tld.isalpha()


class DeUrlRecognizer(UrlRecognizer):
    """Presidio's URL recognizer with a generic scheme pattern and no academic suffixes."""

    def __init__(self) -> None:
        patterns = [
            _GENERIC_SCHEME_URL,
            *(
                Pattern(pattern.name, pattern.regex, _URL_SCORES.get(pattern.name, pattern.score))
                for pattern in UrlRecognizer.PATTERNS
            ),
        ]
        super().__init__(
            patterns=patterns,
            supported_language="de",
            supported_entity=ENTITY_URL,
        )

    def analyze(
        self,
        text: str,
        entities: list[str],
        nlp_artifacts: NlpArtifacts | None = None,
        regex_flags: int | None = None,
    ) -> list[RecognizerResult]:
        """Drop the results that are really post-nominal academic suffixes."""

        results = super().analyze(text, entities, nlp_artifacts, regex_flags)
        kept: list[RecognizerResult] = []
        for result in results:
            matched = text[result.start : result.end]
            if _ACADEMIC_SUFFIX.match(matched):
                continue
            if (
                "://" not in matched
                and not matched.casefold().startswith("www.")
                and _TITLE_FRAGMENT.match(matched)
            ):
                continue
            kept.append(result)
        return kept


class DeEmailRecognizer(EmailRecognizer):
    """Presidio's email pattern with an offline validation step."""

    def __init__(self) -> None:
        super().__init__(supported_language="de", supported_entity=ENTITY_EMAIL)

    def validate_result(self, pattern_text: str) -> bool:
        """Return whether the match is a structurally valid email address."""

        return _valid_email(pattern_text)


def build_url_recognizer() -> UrlRecognizer:
    """Return the German URL recognizer, built on Presidio's patterns."""

    return DeUrlRecognizer()

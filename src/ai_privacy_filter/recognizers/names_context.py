"""Person names that a salutation, a title or a signature introduces.

spaCy's German NER finds most full names but misses short ones ("Herr Koch")
and names on their own line after a closing. This recognizer uses the words
around a name as evidence and reports the whole mention the way the synthetic
corpus annotates it: an optional salutation, optional academic titles, the name
and optional post-nominal titles ("Herr Dr. Martin Koch", "Univ.Prof. Anna
Schmidt MBA."). "Herr" and "Dr." on their own are not a name.

Covered cues: ``Herr``/``Frau``/``Herrn``, academic titles before the name,
closing formulas such as ``Mit freundlichen Grüßen`` followed by the name,
``Mein Name ist``, ``Ich heiße`` and form labels (``Name:``, ``Absender:``,
``Ansprechpartner:``).
"""

from __future__ import annotations

import re

from presidio_analyzer import RecognizerResult
from presidio_analyzer.nlp_engine import NlpArtifacts

from ai_privacy_filter.recognizers.base import GermanRecognizer, Settings

ENTITY = "PERSON"

_NAME = r"[A-ZÄÖÜ][a-zäöüß]+(?:-[A-ZÄÖÜ][a-zäöüß]+)*"
# A name may not run across a line break: the word after a signature line is
# often a company, not part of the person name.
_FULL = rf"{_NAME}(?:[ \t]+{_NAME}){{0,2}}"

_ACADEMIC = (
    r"(?:Univ\.Prof\.|Dr\.|Prof\."
    r"|Dipl\.(?:-(?:Ing|Kfm|Kffr|Inf|Psych|Päd|Soz|Wirt)\.)?"
    r"|Mag\.|Ing\.)"
)
_POST_NOMINAL = r"(?:B\.\s?(?:Sc|Eng|A|Ed)\.|M\.\s?(?:Sc|Eng|A|Ed)\.|LL\.M\.|MBA\.?)"
_SUBJECT = r"(?:(?:med|jur|rer\.\s*nat)\.\s*)?"
_SALUTATION_WORD = r"(?:Herr|Frau|Herrn)"

# One mention: salutation and titles are part of the span, as in the synthetic
# gold data. Titles without a name are never a span.
_MENTION = (
    rf"(?:(?:{_SALUTATION_WORD})[ \t]+)?(?:(?:{_ACADEMIC})[ \t]+)*{_SUBJECT}"
    rf"{_FULL}(?:[ \t]+{_POST_NOMINAL})?"
)

_SALUTATION = re.compile(rf"(?<![\w])(?P<name>{_SALUTATION_WORD}[ \t]+{_MENTION})")
_TITLE = re.compile(
    rf"(?<![\w])(?P<name>(?:(?:{_ACADEMIC})[ \t]+)+{_SUBJECT}{_FULL}"
    rf"(?:[ \t]+{_POST_NOMINAL})?)"
)
_CLOSING = re.compile(
    r"(?i:mit freundlichen grüßen|freundliche grüße|viele grüße|beste grüße"
    r"|mit besten grüßen|herzliche grüße|hochachtungsvoll|mit vorzüglicher hochachtung)"
    rf"[ \t]*[,!.]?(?:[ \t]*\r?\n){{0,2}}[ \t]*(?P<name>{_MENTION})"
)
_INTRO = re.compile(
    r"(?i:mein name ist|mein name lautet|ich heiße|ich heisse|man nennt mich)"
    rf"\s+(?P<name>{_MENTION})"
)
# Labels only count at the start of a line; "Rechnung bitte an:" must not
# introduce a person, while "Von:" and "An:" in a forwarded header do.
_LABEL = re.compile(
    r"^[ \t]*(?i:name|nachname|vorname|absender|absenderin|ansprechpartner"
    r"|ansprechpartnerin|von|an)\s*[:\-]\s*(?P<name>" + _MENTION + r")",
    re.MULTILINE,
)
# A post-nominal title is strong evidence even without a cue ("Romana Wieloch
# MBA." in a forwarded header). spaCy stops before the title, so this span is
# longer and wins the merge.
_POST_NOMINAL_MENTION = re.compile(
    rf"(?<![\w])(?P<name>{_NAME}(?:[ \t]+{_NAME}){{1,2}}[ \t]+{_POST_NOMINAL})"
)

# After a closing, the next capitalised line is often a company or a team
# name ("Die Geschäftsführung", "Bäckerei Weber"). These lead words mean the
# match is not a person name, so the signature pattern leaves it to the org
# recognizer.
_SIGNATURE_STOPWORDS = frozenset(
    {
        "die",
        "der",
        "das",
        "den",
        "dem",
        "ihr",
        "ihre",
        "ihrem",
        "ihren",
        "unser",
        "unsere",
        "mit",
        "viele",
        "beste",
        "freundliche",
        "herzliche",
        "team",
        "firma",
        "bäckerei",
        "geschäftsführung",
        "geschaeftsfuehrung",
        "abteilung",
        "kundenservice",
        "vertrieb",
    }
)


class NamesContextRecognizer(GermanRecognizer):
    """Recognise a person mention that a salutation, title or signature introduces."""

    entity = ENTITY

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings, name="NamesContextRecognizer", context_words=())

    def analyze(
        self,
        text: str,
        entities: list[str],
        nlp_artifacts: NlpArtifacts | None = None,
    ) -> list[RecognizerResult]:
        """Return person-name spans introduced by context."""

        if not self.accepts(entities):
            return []
        results: list[RecognizerResult] = []
        for match in _SALUTATION.finditer(text):
            captured = match.group("name")
            # "Herr" and "Frau" alone are not a name.
            if (
                captured.split()[0].casefold() in {"herr", "frau", "herrn"}
                and len(captured.split()) < 2
            ):
                continue
            results.append(
                self.result(
                    match.start("name"),
                    match.end("name"),
                    0.9,
                    "German person mention after a salutation",
                )
            )
        for match in _TITLE.finditer(text):
            preceding = text[: match.start()].rsplit(None, 1)
            if preceding and preceding[-1] in {"Herr", "Frau", "Herrn"}:
                # The salutation pattern already covers the longer mention.
                continue
            results.append(
                self.result(
                    match.start("name"),
                    match.end("name"),
                    0.9,
                    "German person mention after an academic title",
                )
            )
        for match in _CLOSING.finditer(text):
            first_word = match.group("name").split()[0].casefold()
            if first_word in _SIGNATURE_STOPWORDS:
                continue
            results.append(
                self.result(
                    match.start("name"),
                    match.end("name"),
                    0.9,
                    "German person mention in a signature block",
                )
            )
        for match in _INTRO.finditer(text):
            results.append(
                self.result(
                    match.start("name"),
                    match.end("name"),
                    0.9,
                    "German person mention introduced in the text",
                )
            )
        for match in _LABEL.finditer(text):
            results.append(
                self.result(
                    match.start("name"),
                    match.end("name"),
                    0.9,
                    "German person mention on a labelled form line",
                )
            )
        for match in _POST_NOMINAL_MENTION.finditer(text):
            results.append(
                self.result(
                    match.start("name"),
                    match.end("name"),
                    0.85,
                    "German person mention with a post-nominal title",
                )
            )
        # "Herr Dr. Martin Koch" matches both the salutation and the title
        # pattern; keep only the longest mention for each nested match.
        ordered = sorted(results, key=lambda item: (-(item.end - item.start), item.start))
        kept: list[RecognizerResult] = []
        for result in ordered:
            if any(result.start >= other.start and result.end <= other.end for other in kept):
                continue
            kept.append(result)
        return kept

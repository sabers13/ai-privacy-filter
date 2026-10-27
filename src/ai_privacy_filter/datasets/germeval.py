"""GermEval 2014 NER: sources, integrity checks and TSV parser.

The 2014 GermEval shared task (Benikova, Biemann, Reznicek, LREC 2014) publishes
German Wikipedia and news sentences with outer and nested named-entity
annotations under CC BY 4.0. This module keeps the project's use narrow:

* the files are downloaded into ``data/external/germeval/``, which is
  git-ignored, and are never redistributed;
* only the outer annotation column is parsed, because the project evaluates
  outer spans;
* nothing here prints or serialises sentence text to a log. Parsed JSONL stays
  under ``data/external/`` too.

Label mapping for evaluation
----------------------------
``PER`` maps to :attr:`EntityType.PERSON` and ``ORG`` to :attr:`EntityType.ORG`;
those two classes form the primary personal-data metric. ``LOC`` maps to the
loader-only :class:`GermEvalClass.LOCATION` and is reported separately, because
a place name is not personal data on its own. ``OTH`` is kept as ``OTHER`` for
completeness and is not scored. The ``deriv`` and ``part`` variants (for
example ``B-PERderiv`` or ``B-LOCpart``) are annotation sub-structure and are
kept as their own classes, excluded from the primary metric.

Integrity
---------
:data:`SPLIT_SOURCES` pins the exact file URL, byte size and SHA-256 of the
revision downloaded on 2026-10-27. The files change rarely, and a mismatch is
reported as an error instead of being silently accepted.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal

import httpx

from ai_privacy_filter.models import EntityType

HOMEPAGE_URL = "https://sites.google.com/site/germeval2014ner/home"
DATA_PAGE_URL = "https://sites.google.com/site/germeval2014ner/data"
DRIVE_FOLDER_URL = (
    "https://drive.google.com/drive/folders/1kC0I2UGl2ltrluI9NqDjaQJGw5iliw_J?usp=sharing"
)
LICENCE = "CC BY 4.0"
LICENCE_URL = "https://creativecommons.org/licenses/by/4.0/"


class GermEvalError(Exception):
    """Base class for loader failures that are safe to show to a person."""


class GermEvalFormatError(GermEvalError):
    """The TSV does not follow the published format."""


class GermEvalIntegrityError(GermEvalError):
    """A downloaded file does not match its pinned size or checksum."""


class GermEvalSplitSizeError(GermEvalError):
    """The parsed sentence counts differ from the published split sizes."""


class GermEvalClass(StrEnum):
    """Evaluation classes derived from the outer GermEval tag column."""

    PERSON = "PERSON"
    ORG = "ORG"
    LOCATION = "LOCATION"
    OTHER = "OTHER"
    PERSON_DERIV = "PERSON_DERIV"
    ORG_DERIV = "ORG_DERIV"
    LOCATION_DERIV = "LOCATION_DERIV"
    OTHER_DERIV = "OTHER_DERIV"
    PERSON_PART = "PERSON_PART"
    ORG_PART = "ORG_PART"
    LOCATION_PART = "LOCATION_PART"
    OTHER_PART = "OTHER_PART"

    @property
    def entity_type(self) -> EntityType | None:
        """Contract type for classes that describe personal data."""
        if self is GermEvalClass.PERSON:
            return EntityType.PERSON
        if self is GermEvalClass.ORG:
            return EntityType.ORG
        return None

    @property
    def is_primary(self) -> bool:
        """Whether the class counts towards the primary metric."""
        return self in PRIMARY_LABELS


GERMEVAL_CLASSES: tuple[GermEvalClass, ...] = tuple(GermEvalClass)

#: Classes scored as personal data. ``PER`` and ``ORG`` only.
PRIMARY_LABELS: frozenset[GermEvalClass] = frozenset({GermEvalClass.PERSON, GermEvalClass.ORG})

#: Classes reported separately and excluded from the primary metric. Locations
#: are not personal data by themselves; ``OTHER`` is not personal data either.
REPORTED_SEPARATELY_LABELS: frozenset[GermEvalClass] = frozenset(
    {GermEvalClass.LOCATION, GermEvalClass.OTHER}
)

_LABEL_BY_TAG_BODY: dict[str, GermEvalClass] = {
    "PER": GermEvalClass.PERSON,
    "ORG": GermEvalClass.ORG,
    "LOC": GermEvalClass.LOCATION,
    "OTH": GermEvalClass.OTHER,
    "PERderiv": GermEvalClass.PERSON_DERIV,
    "ORGderiv": GermEvalClass.ORG_DERIV,
    "LOCderiv": GermEvalClass.LOCATION_DERIV,
    "OTHderiv": GermEvalClass.OTHER_DERIV,
    "PERpart": GermEvalClass.PERSON_PART,
    "ORGpart": GermEvalClass.ORG_PART,
    "LOCpart": GermEvalClass.LOCATION_PART,
    "OTHpart": GermEvalClass.OTHER_PART,
}

_BIO_PREFIXES = frozenset({"B", "I"})


@dataclass(frozen=True, slots=True)
class GermEvalSource:
    """One published data file with its integrity metadata."""

    split: str
    filename: str
    url: str
    sha256: str
    size_bytes: int
    sentences: int

    @property
    def drive_folder_url(self) -> str:
        return DRIVE_FOLDER_URL


#: Current revision (page note: data changed 28/4/22 to fix an encoding error).
SPLIT_SOURCES: tuple[GermEvalSource, ...] = (
    GermEvalSource(
        split="train",
        filename="NER-de-train.tsv",
        url="https://drive.google.com/uc?export=download&id=1Jjhbal535VVz2ap4v4r_rN1UEHTdLK5P",
        sha256="98682c21700e5bd7959ac361150679929e4af5d91e4dd7c379cc89133a810994",
        size_bytes=7_882_321,
        sentences=24_000,
    ),
    GermEvalSource(
        split="dev",
        filename="NER-de-dev.tsv",
        url="https://drive.google.com/uc?export=download&id=1ZfRcQThdtAR5PPRjIDtrVP7BtXSCUBbm",
        sha256="2ffe8405acc6bb6111b62a88623aa1e1f2e6db16b77fc005c54163541807aa05",
        size_bytes=723_854,
        sentences=2_200,
    ),
    GermEvalSource(
        split="test",
        filename="NER-de-test.tsv",
        url="https://drive.google.com/uc?export=download&id=1u9mb7kNJHWQCWyweMDRMuTFoOHOfeBTH",
        sha256="ec6e6adce5ed71cee119ae758e569ea4be54c6be5319b3b49dfcdcb05e51301b",
        size_bytes=1_682_725,
        sentences=5_100,
    ),
)

SOURCE_BY_SPLIT: dict[str, GermEvalSource] = {source.split: source for source in SPLIT_SOURCES}


@dataclass(frozen=True, slots=True)
class GermEvalSpan:
    """One outer-level entity span inside a sentence."""

    start: int
    end: int
    text: str
    label: GermEvalClass
    entity_type: EntityType | None
    token_start: int
    token_end: int

    @property
    def is_primary(self) -> bool:
        return self.label.is_primary


@dataclass(frozen=True, slots=True)
class GermEvalSentence:
    """A sentence with its text, tokens and outer-level spans."""

    id: str
    split: str
    source: str
    date: str | None
    text: str
    tokens: tuple[str, ...]
    spans: tuple[GermEvalSpan, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "split": self.split,
            "source": self.source,
            "date": self.date,
            "text": self.text,
            "tokens": list(self.tokens),
            "spans": [
                {
                    "start": span.start,
                    "end": span.end,
                    "text": span.text,
                    "label": str(span.label),
                    "entity_type": None if span.entity_type is None else str(span.entity_type),
                    "primary": span.is_primary,
                    "token_start": span.token_start,
                    "token_end": span.token_end,
                }
                for span in self.spans
            ],
        }


@dataclass(frozen=True, slots=True)
class SplitStats:
    """Counts for one parsed split, without any sentence text."""

    split: str
    sentences: int
    tokens: int
    spans: int
    primary_spans: int
    spans_by_label: dict[str, int]

    def to_dict(self) -> dict[str, object]:
        return {
            "split": self.split,
            "sentences": self.sentences,
            "tokens": self.tokens,
            "spans": self.spans,
            "primary_spans": self.primary_spans,
            "spans_by_label": self.spans_by_label,
        }


def parse_tag(tag: str) -> tuple[Literal["B", "I"], GermEvalClass] | None:
    """Parse one outer BIO tag.

    ``O`` returns ``None``. Anything else must be ``B-`` or ``I-`` followed by a
    known class, for example ``B-PER`` or ``I-LOCpart``.
    """
    if tag == "O":
        return None
    prefix, separator, body = tag.partition("-")
    if separator != "-" or prefix not in _BIO_PREFIXES:
        raise GermEvalFormatError(f"unsupported tag prefix in {tag!r}")
    label = _LABEL_BY_TAG_BODY.get(body)
    if label is None:
        raise GermEvalFormatError(f"unknown tag class in {tag!r}")
    if prefix == "B":
        return "B", label
    return "I", label


def _token_offsets(tokens: Sequence[str]) -> list[tuple[int, int]]:
    offsets: list[tuple[int, int]] = []
    position = 0
    for token in tokens:
        offsets.append((position, position + len(token)))
        position += len(token) + 1
    return offsets


def _spans_from_tags(tokens: Sequence[str], outer_tags: Sequence[str]) -> tuple[GermEvalSpan, ...]:
    offsets = _token_offsets(tokens)
    text = " ".join(tokens)
    spans: list[GermEvalSpan] = []
    open_label: GermEvalClass | None = None
    open_start = 0

    def close(token_end: int) -> None:
        if open_label is None:
            return
        start = offsets[open_start][0]
        end = offsets[token_end][1]
        spans.append(
            GermEvalSpan(
                start=start,
                end=end,
                text=text[start:end],
                label=open_label,
                entity_type=open_label.entity_type,
                token_start=open_start,
                token_end=token_end,
            )
        )

    for index, tag in enumerate(outer_tags):
        parsed = parse_tag(tag)
        if parsed is None:
            close(index - 1)
            open_label = None
            continue
        prefix, label = parsed
        # Tolerate a continuation tag that starts a new entity: real corpora
        # occasionally begin with I- or switch type without a B- tag.
        if prefix == "B" or open_label is not label:
            close(index - 1)
            open_label = label
            open_start = index
    close(len(tokens) - 1)
    return tuple(spans)


def _parse_comment(parts: Sequence[str], *, split: str, lineno: int) -> tuple[str, str | None]:
    if len(parts) < 2 or not parts[1].strip():
        raise GermEvalFormatError(f"{split}:{lineno}: comment line has no source")
    source = parts[1].strip()
    date = parts[2].strip() if len(parts) > 2 and parts[2].strip() else None
    return source, date


def _parse_token_line(
    line: str, *, split: str, lineno: int, expected_index: int
) -> tuple[str, str]:
    parts = line.split("\t")
    if len(parts) < 4:
        raise GermEvalFormatError(f"{split}:{lineno}: token line has fewer than four columns")
    try:
        index = int(parts[0])
    except ValueError as exc:
        raise GermEvalFormatError(f"{split}:{lineno}: token index is not an integer") from exc
    if index != expected_index:
        raise GermEvalFormatError(
            f"{split}:{lineno}: token index {index} does not follow {expected_index - 1}"
        )
    token = parts[1]
    if not token:
        raise GermEvalFormatError(f"{split}:{lineno}: token is empty")
    try:
        parse_tag(parts[2])
    except GermEvalFormatError as exc:
        raise GermEvalFormatError(f"{split}:{lineno}: {exc}") from exc
    return token, parts[2]


def parse_tsv(text: str, *, split: str) -> tuple[GermEvalSentence, ...]:
    """Parse one GermEval TSV document into sentences with outer spans.

    ``split`` only labels the sentences (``train``, ``dev`` or ``test``) and
    prefixes their ids. Blank lines are separators and are ignored; a ``#``
    line starts a new sentence. The nested column is intentionally ignored.
    Error messages carry positions and tags, never token text.
    """
    if not text.strip():
        raise GermEvalFormatError(f"{split}: empty document")

    sentences: list[GermEvalSentence] = []
    current_source: str | None = None
    current_date: str | None = None
    tokens: list[str] = []
    tags: list[str] = []
    index = 0

    def flush(next_lineno: int) -> None:
        nonlocal current_source, current_date, tokens, tags, index
        if current_source is None and not tokens:
            return
        if current_source is None:
            raise GermEvalFormatError(
                f"{split}:{next_lineno - 1}: token lines before the first # header"
            )
        if not tokens:
            raise GermEvalFormatError(
                f"{split}:{next_lineno - 1}: sentence header without token lines"
            )
        sentences.append(
            GermEvalSentence(
                id=f"{split}-{index:05d}",
                split=split,
                source=current_source,
                date=current_date,
                text=" ".join(tokens),
                tokens=tuple(tokens),
                spans=_spans_from_tags(tokens, tags),
            )
        )
        index += 1
        tokens = []
        tags = []
        current_source = None
        current_date = None

    for lineno, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        if line.startswith("#"):
            flush(lineno)
            current_source, current_date = _parse_comment(
                line.split("\t"), split=split, lineno=lineno
            )
            continue
        token, tag = _parse_token_line(
            line, split=split, lineno=lineno, expected_index=len(tokens) + 1
        )
        tokens.append(token)
        tags.append(tag)
    flush(len(text.splitlines()) + 1)
    if not sentences:
        raise GermEvalFormatError(f"{split}: no sentences found")
    return tuple(sentences)


def parse_file(path: Path, *, split: str) -> tuple[GermEvalSentence, ...]:
    """Read and parse one downloaded TSV file."""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise GermEvalError(f"{split}: cannot read {path.name}: {exc.strerror or exc}") from exc
    return parse_tsv(text, split=split)


def summarise(sentences: Iterable[GermEvalSentence], *, split: str) -> SplitStats:
    """Count sentences, tokens and spans by class. Never keeps text."""
    sentence_count = 0
    token_count = 0
    span_count = 0
    primary_count = 0
    by_label: dict[str, int] = {str(label): 0 for label in GERMEVAL_CLASSES}
    for sentence in sentences:
        sentence_count += 1
        token_count += len(sentence.tokens)
        for span in sentence.spans:
            span_count += 1
            by_label[str(span.label)] += 1
            if span.is_primary:
                primary_count += 1
    return SplitStats(
        split=split,
        sentences=sentence_count,
        tokens=token_count,
        spans=span_count,
        primary_spans=primary_count,
        spans_by_label={label: count for label, count in by_label.items() if count},
    )


def check_published_sizes(counts: Mapping[str, int]) -> None:
    """Raise unless every split matches the published sentence count."""
    for source in SPLIT_SOURCES:
        actual = counts.get(source.split)
        if actual is None:
            raise GermEvalSplitSizeError(f"{source.split}: no sentence count to check")
        if actual != source.sentences:
            raise GermEvalSplitSizeError(
                f"{source.split}: expected {source.sentences} sentences, parsed {actual}"
            )
    extra = sorted(set(counts) - set(SOURCE_BY_SPLIT))
    if extra:
        raise GermEvalSplitSizeError(f"unexpected splits in size check: {', '.join(extra)}")


def file_sha256(path: Path) -> tuple[str, int]:
    """Return ``(hex digest, byte count)`` for one file."""
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def verify_file(path: Path, source: GermEvalSource) -> None:
    """Check size and SHA-256 of an existing download."""
    if not path.is_file():
        raise GermEvalIntegrityError(f"{source.split}: {path.name} is missing")
    digest, size = file_sha256(path)
    if size != source.size_bytes:
        raise GermEvalIntegrityError(
            f"{source.split}: expected {source.size_bytes} bytes, found {size}"
        )
    if digest != source.sha256:
        raise GermEvalIntegrityError(f"{source.split}: sha256 does not match the pinned value")


def _download(url: str, destination: Path, *, timeout_s: float = 120.0) -> tuple[str, int]:
    temporary = destination.with_name(destination.name + ".part")
    digest = hashlib.sha256()
    size = 0
    try:
        with httpx.stream("GET", url, follow_redirects=True, timeout=timeout_s) as response:
            response.raise_for_status()
            with temporary.open("wb") as handle:
                for chunk in response.iter_bytes():
                    handle.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return digest.hexdigest(), size


def download_source(source: GermEvalSource, data_dir: Path, *, force: bool = False) -> Path:
    """Ensure one split is present and verified, downloading it if needed."""
    data_dir.mkdir(parents=True, exist_ok=True)
    destination = data_dir / source.filename
    if destination.exists() and not force:
        try:
            verify_file(destination, source)
            return destination
        except GermEvalIntegrityError:
            # A partial or superseded file cannot be trusted; fetch it again.
            destination.unlink()
    digest, size = _download(source.url, destination)
    if size != source.size_bytes or digest != source.sha256:
        destination.with_name(destination.name + ".part").unlink(missing_ok=True)
        raise GermEvalIntegrityError(
            f"{source.split}: downloaded file does not match the pinned size and sha256"
        )
    destination.with_name(destination.name + ".part").replace(destination)
    return destination


def fetch_sources(
    data_dir: Path,
    *,
    splits: Iterable[str] | None = None,
    force: bool = False,
) -> dict[str, Path]:
    """Download and verify the requested splits. Returns split to path."""
    selected = select_sources(splits)
    return {source.split: download_source(source, data_dir, force=force) for source in selected}


def select_sources(splits: Iterable[str] | None) -> tuple[GermEvalSource, ...]:
    if splits is None:
        return SPLIT_SOURCES
    wanted = set(splits)
    unknown = sorted(wanted - set(SOURCE_BY_SPLIT))
    if unknown:
        raise GermEvalError(f"unknown split(s): {', '.join(unknown)}")
    return tuple(source for source in SPLIT_SOURCES if source.split in wanted)


def write_parsed(
    sentences: Sequence[GermEvalSentence], output_dir: Path, *, split: str
) -> tuple[Path, Path]:
    """Write one split as JSONL plus a text-free stats file. Returns both paths."""
    output_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = output_dir / f"{split}.jsonl"
    stats_path = output_dir / f"{split}.stats.json"
    with jsonl_path.open("w", encoding="utf-8", newline="\n") as handle:
        for sentence in sentences:
            handle.write(json.dumps(sentence.to_dict(), ensure_ascii=False, sort_keys=True))
            handle.write("\n")
    stats_path.write_text(
        json.dumps(summarise(sentences, split=split).to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return jsonl_path, stats_path


def write_manifest(
    data_dir: Path, results: Mapping[str, tuple[Path, SplitStats]], *, retrieved_at: datetime
) -> Path:
    """Write a machine-readable provenance manifest next to the downloads."""
    manifest = {
        "dataset": "GermEval 2014 NER",
        "homepage": HOMEPAGE_URL,
        "data_page": DATA_PAGE_URL,
        "drive_folder": DRIVE_FOLDER_URL,
        "licence": LICENCE,
        "licence_url": LICENCE_URL,
        "retrieved_at": retrieved_at.astimezone(UTC).isoformat(),
        "splits": {
            split: {
                "url": SOURCE_BY_SPLIT[split].url,
                "file": path.name,
                "sha256": SOURCE_BY_SPLIT[split].sha256,
                "bytes": SOURCE_BY_SPLIT[split].size_bytes,
                "sentences": stats.sentences,
                "tokens": stats.tokens,
                "spans": stats.spans,
                "primary_spans": stats.primary_spans,
            }
            for split, (path, stats) in sorted(results.items())
        },
    }
    manifest_path = data_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest_path


def iter_jsonl(path: Path) -> Iterator[dict[str, object]]:
    """Yield parsed sentence records from a JSONL file written by :func:`write_parsed`."""
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)

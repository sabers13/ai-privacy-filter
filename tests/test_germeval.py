"""GermEval 2014 loader and parser tests.

Every sentence below is invented for this test file. No GermEval 2014 sentence
is reproduced in this repository; the real data stays under ``data/external/``.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path

import pytest

from ai_privacy_filter.datasets import germeval
from ai_privacy_filter.models import EntityType

REPO_ROOT = Path(__file__).resolve().parents[1]

# Invented fixture in the published four-column TSV shape. The outer tag is the
# third column; the fourth column is a nested annotation that must be ignored.
FIXTURE = (
    "#\thttps://example.invalid/quellen/artikel-1\t[2024-01-15]\t\n"
    "1\tBeispielstadt\tB-LOC\tO\n"
    "2\tliegt\tO\tO\n"
    "3\tim\tO\tO\n"
    "4\tNorden\tO\tO\n"
    "5\t.\tO\tO\n"
    "6\tFrau\tO\tO\n"
    "7\tMuster\tB-PER\tO\n"
    "8\tarbeitet\tO\tO\n"
    "9\tbei\tO\tO\n"
    "10\tBeispielfirma\tB-ORG\tB-LOC\n"
    "11\t.\tO\tO\n"
    "12\tDer\tO\tO\n"
    "13\tMusterpreis\tB-PERderiv\tO\n"
    "14\tund\tO\tO\n"
    "15\tdas\tO\tO\n"
    "16\tMusterhaus\tB-LOCpart\tO\n"
    "17\t.\tO\tO\n"
    "\n"
    "#\thttps://example.invalid/quellen/artikel-2\t[2024-02-20]\t\n"
    "1\tBeispielstadt\tB-ORG\tO\n"
    "2\t-\tI-ORG\tO\n"
    "3\tLauf\tI-ORG\tO\n"
    "4\tist\tO\tO\n"
    "5\tein\tO\tO\n"
    "6\tEreignis\tB-OTH\tO\n"
    "7\t.\tO\tO\n"
)

# A second invented fixture with the two shape quirks present in the original
# files: a three-column header and a token line with a trailing tab.
QUIRKY_FIXTURE = (
    "#\thttps://example.invalid/quellen/artikel-3\t[2024-03-01]\n"
    '1\t"\tO\tO\t\n'
    "2\tBeispiel\tI-PER\tO\n"
    "3\tStadt\tI-LOC\tO\n"
    "4\tEnde\tO\tO\n"
)


def test_parse_returns_two_sentences_with_metadata() -> None:
    sentences = germeval.parse_tsv(FIXTURE, split="train")

    assert len(sentences) == 2
    first, second = sentences
    assert first.id == "train-00000"
    assert second.id == "train-00001"
    assert first.split == "train"
    assert first.source == "https://example.invalid/quellen/artikel-1"
    assert first.date == "[2024-01-15]"
    assert second.date == "[2024-02-20]"
    assert first.text.startswith("Beispielstadt liegt im Norden . Frau Muster")
    assert len(first.tokens) == 17


def test_outer_spans_are_mapped_and_slices_match_text() -> None:
    sentences = germeval.parse_tsv(FIXTURE, split="dev")
    first = sentences[0]

    assert [str(span.label) for span in first.spans] == [
        "LOCATION",
        "PERSON",
        "ORG",
        "PERSON_DERIV",
        "LOCATION_PART",
    ]
    for span in first.spans:
        assert first.text[span.start : span.end] == span.text
    assert first.spans[0].text == "Beispielstadt"
    assert first.spans[1].text == "Muster"
    assert first.spans[1].start == first.text.index("Muster")
    assert first.spans[2].text == "Beispielfirma"
    # The nested B-LOC on the same token must not replace the outer ORG span.
    assert first.spans[2].token_start == first.spans[2].token_end == 9
    assert all(span.token_start <= span.token_end for span in first.spans)


def test_contract_types_locations_and_variants_are_not_primary() -> None:
    first = germeval.parse_tsv(FIXTURE, split="train")[0]
    by_label = {span.label: span for span in first.spans}

    assert by_label[germeval.GermEvalClass.PERSON].entity_type is EntityType.PERSON
    assert by_label[germeval.GermEvalClass.ORG].entity_type is EntityType.ORG
    assert by_label[germeval.GermEvalClass.LOCATION].entity_type is None
    assert by_label[germeval.GermEvalClass.PERSON_DERIV].entity_type is None
    assert by_label[germeval.GermEvalClass.LOCATION_PART].entity_type is None

    assert first.spans[1].is_primary
    assert first.spans[2].is_primary
    assert not first.spans[0].is_primary
    assert not first.spans[3].is_primary
    assert not first.spans[4].is_primary


def test_multi_token_span_and_other_class() -> None:
    second = germeval.parse_tsv(FIXTURE, split="train")[1]
    org, other = second.spans

    assert org.label is germeval.GermEvalClass.ORG
    assert org.text == "Beispielstadt - Lauf"
    assert (org.token_start, org.token_end) == (0, 2)
    assert other.label is germeval.GermEvalClass.OTHER
    assert other.text == "Ereignis"
    assert second.text[org.start : org.end] == "Beispielstadt - Lauf"
    assert [span.is_primary for span in second.spans] == [True, False]


def test_shape_quirks_and_tolerated_continuation_tags() -> None:
    sentence = germeval.parse_tsv(QUIRKY_FIXTURE, split="test")[0]

    assert sentence.date == "[2024-03-01]"
    assert sentence.tokens[0] == '"'
    assert [(str(span.label), span.text) for span in sentence.spans] == [
        ("PERSON", "Beispiel"),
        ("LOCATION", "Stadt"),
    ]


def test_parse_tag_accepts_published_tags_and_rejects_unknown() -> None:
    assert germeval.parse_tag("O") is None
    assert germeval.parse_tag("B-PER") == ("B", germeval.GermEvalClass.PERSON)
    assert germeval.parse_tag("I-LOCpart") == ("I", germeval.GermEvalClass.LOCATION_PART)

    with pytest.raises(germeval.GermEvalFormatError):
        germeval.parse_tag("B-PERSON")
    with pytest.raises(germeval.GermEvalFormatError):
        germeval.parse_tag("X-PER")


def test_format_errors_name_positions_but_never_token_text() -> None:
    before_header = "1\tGEHEIM\tO\tO\n"
    with pytest.raises(germeval.GermEvalFormatError) as excinfo:
        germeval.parse_tsv(before_header, split="train")
    assert "GEHEIM" not in str(excinfo.value)

    bad_index = FIXTURE.replace("2\tliegt\tO\tO", "5\tliegt\tO\tO")
    with pytest.raises(germeval.GermEvalFormatError) as excinfo:
        germeval.parse_tsv(bad_index, split="train")
    assert "train:3" in str(excinfo.value)
    assert "liegt" not in str(excinfo.value)

    unknown_tag = FIXTURE.replace("B-PER\tO", "B-PERSON\tO")
    with pytest.raises(germeval.GermEvalFormatError) as excinfo:
        germeval.parse_tsv(unknown_tag, split="train")
    assert "train:8" in str(excinfo.value)

    with pytest.raises(germeval.GermEvalFormatError):
        germeval.parse_tsv("", split="train")
    with pytest.raises(germeval.GermEvalFormatError):
        germeval.parse_tsv("#\thttps://example.invalid/x\t[2024-01-01]\t\n", split="train")
    with pytest.raises(germeval.GermEvalFormatError):
        germeval.parse_tsv(
            "#\thttps://example.invalid/x\t[2024-01-01]\t\n"
            "#\thttps://example.invalid/y\t[2024-01-02]\t\n",
            split="train",
        )
    with pytest.raises(germeval.GermEvalFormatError):
        germeval.parse_tsv("#\t\t[2024-01-01]\t\n1\tWort\tO\tO\n", split="train")


def test_parse_file_reads_utf8_and_ignores_bom(tmp_path: Path) -> None:
    path = tmp_path / "fixture.tsv"
    path.write_text("\ufeff" + QUIRKY_FIXTURE, encoding="utf-8")

    sentences = germeval.parse_file(path, split="test")

    assert len(sentences) == 1
    assert sentences[0].tokens[1] == "Beispiel"


def test_summarise_counts_without_retaining_text() -> None:
    sentences = germeval.parse_tsv(FIXTURE, split="train")
    stats = germeval.summarise(sentences, split="train")

    assert stats.sentences == 2
    assert stats.tokens == 24
    assert stats.spans == 7
    assert stats.primary_spans == 3
    assert stats.spans_by_label["LOCATION"] == 1
    assert stats.spans_by_label["PERSON_DERIV"] == 1
    assert stats.spans_by_label["LOCATION_PART"] == 1
    assert "Muster" not in json.dumps(stats.to_dict())
    assert stats.to_dict()["sentences"] == 2


def test_published_split_sizes_are_pinned_and_checked() -> None:
    published = {source.split: source.sentences for source in germeval.SPLIT_SOURCES}
    assert published == {"train": 24_000, "dev": 2_200, "test": 5_100}

    germeval.check_published_sizes(published)

    with pytest.raises(germeval.GermEvalSplitSizeError):
        germeval.check_published_sizes({**published, "train": 23_999})
    with pytest.raises(germeval.GermEvalSplitSizeError):
        germeval.check_published_sizes({"train": 24_000, "dev": 2_200})
    with pytest.raises(germeval.GermEvalSplitSizeError):
        germeval.check_published_sizes({**published, "extra": 1})


def test_verify_file_accepts_pinned_bytes_and_rejects_tampering(tmp_path: Path) -> None:
    payload = b"invented fixture bytes\n"
    path = tmp_path / "fixture.tsv"
    path.write_bytes(payload)
    source = germeval.GermEvalSource(
        split="train",
        filename="fixture.tsv",
        url="https://example.invalid/fixture.tsv",
        sha256=hashlib.sha256(payload).hexdigest(),
        size_bytes=len(payload),
        sentences=1,
    )

    germeval.verify_file(path, source)

    path.write_bytes(payload + b"tampered")
    with pytest.raises(germeval.GermEvalIntegrityError) as excinfo:
        germeval.verify_file(path, source)
    assert "bytes" in str(excinfo.value)
    assert "invented" not in str(excinfo.value)

    path.write_bytes(payload.replace(b"invented", b"Invented"))
    with pytest.raises(germeval.GermEvalIntegrityError) as excinfo:
        germeval.verify_file(path, source)
    assert "sha256" in str(excinfo.value)
    assert "Invented" not in str(excinfo.value)


def test_write_parsed_round_trips_jsonl_and_stats(tmp_path: Path) -> None:
    sentences = germeval.parse_tsv(FIXTURE, split="train")

    jsonl_path, stats_path = germeval.write_parsed(sentences, tmp_path, split="train")

    records = list(germeval.iter_jsonl(jsonl_path))
    assert [record["id"] for record in records] == ["train-00000", "train-00001"]
    assert records[0]["text"] == sentences[0].text
    assert records[0]["spans"][1]["entity_type"] == "PERSON"
    assert records[0]["spans"][0]["entity_type"] is None
    stats = json.loads(stats_path.read_text(encoding="utf-8"))
    assert stats["sentences"] == 2
    assert stats["primary_spans"] == 3


def test_select_sources_rejects_unknown_split() -> None:
    assert [source.split for source in germeval.select_sources(None)] == [
        "train",
        "dev",
        "test",
    ]
    assert [source.split for source in germeval.select_sources(["test"])] == ["test"]
    with pytest.raises(germeval.GermEvalError):
        germeval.select_sources(["validation"])


def test_every_source_has_a_pinned_https_download() -> None:
    for source in germeval.SPLIT_SOURCES:
        assert source.url.startswith("https://drive.google.com/uc?export=download&id=")
        assert re.fullmatch(r"[0-9a-f]{64}", source.sha256)
        assert source.size_bytes > 0
        assert source.sentences > 0


def test_sources_markdown_matches_the_pinned_metadata() -> None:
    """The human-readable provenance table and the code must not drift."""
    text = (REPO_ROOT / "data" / "SOURCES.md").read_text(encoding="utf-8")
    pattern = re.compile(
        r"^\| (train|dev|test) \| (\S+) \| (https://\S+) \| (\d+) \| (\d+) \| `([0-9a-f]{64})` \|$",
        re.MULTILINE,
    )
    rows = {
        match.group(1): (
            match.group(2),
            match.group(3),
            int(match.group(4)),
            int(match.group(5)),
            match.group(6),
        )
        for match in pattern.finditer(text)
    }

    assert set(rows) == {source.split for source in germeval.SPLIT_SOURCES}
    for source in germeval.SPLIT_SOURCES:
        assert rows[source.split] == (
            source.filename,
            source.url,
            source.size_bytes,
            source.sentences,
            source.sha256,
        )

    assert germeval.LICENCE in text
    assert germeval.LICENCE_URL in text
    assert "2026-10-27" in text


def test_no_external_data_is_tracked_and_external_is_ignored() -> None:
    tracked = subprocess.run(
        ["git", "ls-files", "data/external"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert tracked.stdout == ""

    ignored = subprocess.run(
        ["git", "check-ignore", "data/external/germeval/train.tsv"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert ignored.returncode == 0

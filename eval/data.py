"""Inputs, hashes and provenance for the detection evaluation.

Everything that touches committed data or the git-ignored GermEval download
lives here. The module never prints or serialises GermEval sentence text.

MEMBERS
-------
``load_synthetic`` validates the committed JSONL against the contract
``Enquiry`` model. ``load_germeval`` verifies the pinned TSV checksums and
parses only the outer annotation column. ``check_synthetic_hashes`` compares
the corpus files against the hashes published in ``METHOD.md``.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import platform
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from ai_privacy_filter.datasets import germeval
from ai_privacy_filter.models import Enquiry, EntityType

REPO_ROOT: Final = Path(__file__).resolve().parents[1]
METHOD_PATH: Final = REPO_ROOT / "METHOD.md"
RESULTS_DIR: Final = REPO_ROOT / "results"
SYNTHETIC_DIR: Final = REPO_ROOT / "data" / "synthetic"
GERMEVAL_DATA_DIR: Final = REPO_ROOT / "data" / "external" / "germeval"
CONFIG_PATH: Final = REPO_ROOT / "eval" / "configs.json"
CANDIDATES_PATH: Final = REPO_ROOT / "eval" / "candidates.json"
RUNS_DIR: Final = RESULTS_DIR / "runs"
RERUNS_PATH: Final = REPO_ROOT / "docs" / "eval-reruns.md"
METHOD_HASH_PATH: Final = RESULTS_DIR / "METHOD_HASH"

SYNTHETIC_FILES: Final = {"dev": "dev.jsonl", "test": "test.jsonl"}

#: Types that have gold spans in the synthetic test split (METHOD.md §2.1).
SYNTHETIC_GOLD_TYPES: Final = (
    EntityType.PERSON,
    EntityType.ORG,
    EntityType.EMAIL,
    EntityType.PHONE,
    EntityType.IBAN,
    EntityType.ADDRESS_STREET,
    EntityType.POSTCODE_CITY,
)

#: Types with no synthetic gold spans; recall is not estimable.
SYNTHETIC_UNESTIMABLE: Final = (EntityType.URL, EntityType.VAT_ID, EntityType.DATE_OF_BIRTH)


def sha256_file(path: Path) -> str:
    """Return the SHA-256 of the raw file bytes."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_bytes(payload: bytes) -> str:
    """Return the SHA-256 of a byte string."""

    return hashlib.sha256(payload).hexdigest()


def method_sha256() -> str:
    """Hash ``METHOD.md`` exactly as the method guard does."""

    return sha256_file(METHOD_PATH)


def canonical_json(payload: object) -> bytes:
    """Serialise deterministically: sorted keys, UTF-8, trailing newline."""

    return (json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode(
        "utf-8"
    )


def write_json(path: Path, payload: object) -> None:
    """Write canonical JSON, creating the parent directory."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json(payload))


def load_synthetic(split: str) -> list[Enquiry]:
    """Load and validate one committed synthetic split."""

    filename = SYNTHETIC_FILES.get(split)
    if filename is None:
        raise ValueError(f"unknown synthetic split: {split!r}")
    path = SYNTHETIC_DIR / filename
    enquiries: list[Enquiry] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                enquiry = Enquiry.model_validate_json(line)
                for span in enquiry.gold_spans:
                    if span.end > len(enquiry.text):
                        raise RuntimeError(
                            f"data integrity failure: {split}:{enquiry.id} has a gold span "
                            "outside its text; stop and do not score"
                        )
                    if enquiry.text[span.start : span.end] != span.text:
                        raise RuntimeError(
                            f"data integrity failure: {split}:{enquiry.id} has a gold span "
                            "that does not match its text; stop and do not score"
                        )
                enquiries.append(enquiry)
    return enquiries


def synthetic_hash(split: str) -> str:
    """Return the SHA-256 of one synthetic split file."""

    return sha256_file(SYNTHETIC_DIR / SYNTHETIC_FILES[split])


def check_synthetic_hashes() -> dict[str, str]:
    """Verify the synthetic hashes against ``METHOD.md`` and return them.

    ``tests/test_method.py`` already pins the corpus to the document; this
    runtime check makes a corrupted local file stop the run instead of
    producing numbers from different data.
    """

    method_text = METHOD_PATH.read_text(encoding="utf-8")
    values: dict[str, str] = {}
    for split in ("dev", "test"):
        digest = synthetic_hash(split)
        if digest not in method_text:
            raise RuntimeError(
                f"data integrity failure: {split} hash is not pinned in METHOD.md; "
                "stop and do not score"
            )
        values[split] = digest
    return values


def ensure_germeval(splits: Sequence[str] = ("train", "dev", "test")) -> dict[str, Path]:
    """Download when missing, verify pinned size/checksum, return paths.

    ``germeval.fetch_sources`` verifies an existing file and only reaches the
    network when the file is absent or corrupt. A mismatch raises
    :class:`germeval.GermEvalIntegrityError`; callers must treat it as a hard
    stop.
    """

    return germeval.fetch_sources(GERMEVAL_DATA_DIR, splits=splits)


def germeval_manifest_sha256() -> str:
    """Hash the pinned split metadata without a retrieval timestamp.

    The downloaded ``manifest.json`` carries ``retrieved_at``, so its bytes
    change between downloads. The run record instead pins this canonical
    summary, which is identical for every verified download.
    """

    payload = {
        source.split: {
            "sha256": source.sha256,
            "bytes": source.size_bytes,
            "sentences": source.sentences,
        }
        for source in germeval.SPLIT_SOURCES
    }
    return sha256_bytes(canonical_json(payload))


def load_germeval(split: str) -> tuple[germeval.GermEvalSentence, ...]:
    """Verify and parse one GermEval split; text stays in memory only."""

    paths = ensure_germeval((split,))
    return germeval.parse_file(paths[split], split=split)


def data_hashes() -> dict[str, str]:
    """Return the run-record data hashes from METHOD.md section 5.3."""

    synthetic = check_synthetic_hashes()
    return {
        "synthetic_dev": synthetic["dev"],
        "synthetic_test": synthetic["test"],
        "germeval_manifest": germeval_manifest_sha256(),
    }


def package_version(name: str) -> str:
    """Return an installed package version, or ``unknown`` when absent."""

    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def environment() -> dict[str, str]:
    """Return public, stable environment facts for the run record.

    No hostname, user, absolute path, address or key is included.
    """

    return {
        "python": f"{platform.python_version()}",
        "presidio": package_version("presidio-analyzer"),
        "spacy": package_version("spacy"),
        "spacy_model": f"de_core_news_lg-{package_version('de-core-news-lg')}",
        "faker": package_version("faker"),
        "platform": f"{platform.system().lower()}-{platform.machine()}",
    }


@dataclass(frozen=True, slots=True)
class Config:
    """The frozen B2 configuration chosen on dev (METHOD.md §3.3, §4)."""

    profile: str
    method_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "profile": self.profile,
            "chosen_on": "synthetic dev + GermEval dev only",
            "selection_rule": (
                "highest minimum of the nine dev recalls; ties by lower enquiry-level "
                "over-masking, higher macro precision, then balanced before strict"
            ),
            "method_sha256": self.method_sha256,
        }


def load_config() -> Config:
    """Load and validate ``eval/configs.json``."""

    payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    profile = payload.get("profile")
    if profile not in {"balanced", "strict"}:
        raise RuntimeError("eval/configs.json does not name a valid detection profile")
    return Config(profile=profile, method_sha256=str(payload.get("method_sha256", "")))


def config_sha256() -> str:
    """Hash the committed frozen configuration file."""

    return sha256_file(CONFIG_PATH)


def load_candidates() -> list[str]:
    """Return the pre-declared B2 candidate profiles."""

    payload = json.loads(CANDIDATES_PATH.read_text(encoding="utf-8"))
    candidates = payload.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise RuntimeError("eval/candidates.json must list at least one candidate")
    profiles = [str(item["profile"]) for item in candidates]
    for profile in profiles:
        if profile not in {"balanced", "strict"}:
            raise RuntimeError(f"unknown candidate profile in eval/candidates.json: {profile}")
    return profiles


def provenance(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return the stable provenance block embedded in every result file."""

    payload: dict[str, Any] = {
        "method_sha256": method_sha256(),
        "config_sha256": config_sha256(),
        "synthetic_test_sha256": synthetic_hash("test"),
        "germeval_manifest_sha256": germeval_manifest_sha256(),
        "environment": environment(),
    }
    if extra:
        payload.update(extra)
    return payload


def read_method_hash_file() -> str | None:
    """Return the recorded run-time method hash, if the file exists."""

    if not METHOD_HASH_PATH.is_file():
        return None
    return METHOD_HASH_PATH.read_text(encoding="utf-8").strip() or None


def record_method_hash() -> str:
    """Write ``results/METHOD_HASH`` at the start of a run and return the hash."""

    digest = method_sha256()
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    METHOD_HASH_PATH.write_text(digest + "\n", encoding="utf-8")
    return digest


def read_sha256sums(path: Path) -> dict[str, str]:
    """Parse a ``sha256sum``-style file into ``{filename: digest}``."""

    entries: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, _, name = line.partition("  ")
        entries[name] = digest
    return entries

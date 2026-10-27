"""Live triage evaluation: the fixed 40-enquiry subset per backend.

The stage is the one fixed in ``METHOD.md`` sections 5, 7.6 and 10:

* read the method hash guard before any test record;
* write a run record and flush it;
* probe availability, run one unscored dev warm-up per live backend;
* run the full pipeline (detect -> redact -> guard -> prompt -> backend) on the
  same 40 enquiries, in the same order, for every backend;
* store each response with placeholders only and a hash;
* compute accuracy, latency, draft restoration and draft indicators;
* write ``metrics.json``, ``report.md`` and ``scoring/`` under the run id.

A response that contains a gold value is discarded and recorded as a leak by
type. A guard block makes no model call. No retry and no fallback happens.
"""

from __future__ import annotations

import json
import platform
import re
import subprocess
import sys
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import httpx

from ai_privacy_filter import __version__
from ai_privacy_filter.backends.base import LLMBackend
from ai_privacy_filter.backends.byok import OLLAMA_BASE_URL, BYOKBackend
from ai_privacy_filter.backends.registry import get_backend
from ai_privacy_filter.evaluation.corpus import (
    canonical_json,
    load_split,
    select_triage_subset,
    sha256_file,
    sha256_text,
    subset_ids,
)
from ai_privacy_filter.evaluation.stats import latency_summary, wilson
from ai_privacy_filter.guard import GuardBlockedError, require_clean
from ai_privacy_filter.models import Category, Enquiry, EntityType, RedactionResult, Urgency
from ai_privacy_filter.pipeline import default_pipeline
from ai_privacy_filter.pseudonymise import canonical_placeholder, normalise_value, restore
from ai_privacy_filter.triage import PROMPT_PREFIX, TRIAGE_SCHEMA, build_prompt

SCHEMA_VERSION: Final = 1
STAGE: Final = "triage"
DEFAULT_BACKENDS: Final[tuple[str, ...]] = ("claude-cli", "codex-cli", "byok")
LIVE_BACKENDS: Final[frozenset[str]] = frozenset({"claude-cli", "codex-cli", "byok", "ollama"})
OLLAMA_MODEL: Final = "qwen2.5:1.5b"
DEFAULT_TIMEOUT_S: Final = 120
OLLAMA_TIMEOUT_S: Final = 90
WARMUP_DEV_ID: Final = "syn-dev-0001"

#: Reply/status values that are not a backend ``error_kind``.
STATUS_OK: Final = "ok"
STATUS_ERROR: Final = "error"
STATUS_GUARD_BLOCK: Final = "guard_block"
STATUS_LEAK: Final = "leak"
STATUS_MISSING: Final = "missing"

_PLACEHOLDER_RE: Final = re.compile(r"\[[^\[\]\n]{1,64}\]")
_DIGIT_TYPES: Final[frozenset[EntityType]] = frozenset({EntityType.PHONE, EntityType.IBAN})


class GuardRefused(RuntimeError):
    """The method hash guard refused the run; ``exit_code`` is its own code."""

    def __init__(self, exit_code: int, message: str) -> None:
        super().__init__(message)
        self.exit_code = exit_code


def _log(message: str) -> None:
    """Write a count-only progress line to stderr."""
    print(f"triage: {message}", file=sys.stderr, flush=True)


def utc_now() -> str:
    """Return the current UTC time as an ISO-8601 string with ``Z``."""
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def git_commit(root: Path) -> str:
    """Return ``HEAD`` for ``root`` or raise a safe error."""
    completed = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if completed.returncode != 0:
        raise RuntimeError("cannot read git HEAD")
    return completed.stdout.strip()


def default_run_id(commit: str) -> str:
    """Return ``<UTC-stamp>-triage-<short-commit>``."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-triage-{commit[:7]}"


def method_guard_check(
    *,
    root: Path,
    run_id: str,
    mode: str,
    rerun_reason: str | None,
    method_approval: str | None,
) -> dict[str, Any]:
    """Call ``scripts/method_guard.py`` before any test record is read."""
    command = [
        sys.executable,
        str(root / "scripts" / "method_guard.py"),
        "--mode",
        mode,
        "--stage",
        STAGE,
        "--root",
        str(root),
    ]
    if mode == "test":
        command.extend(["--run-id", run_id])
    if rerun_reason:
        command.extend(["--rerun-reason", rerun_reason])
    if method_approval:
        command.extend(["--method-approval", method_approval])
    completed = subprocess.run(command, capture_output=True, text=True, check=False, timeout=120)
    if completed.returncode != 0:
        message = completed.stderr.strip().splitlines()
        raise GuardRefused(completed.returncode, message[-1] if message else "method guard refused")
    try:
        decision = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover - guard contract
        raise GuardRefused(2, "method guard returned no JSON decision") from exc
    if not isinstance(decision, dict) or not decision.get("allowed"):
        raise GuardRefused(2, "method guard did not allow the run")
    return decision


def config_sha256(timeout_s: int) -> str:
    """Hash the frozen prompt, schema and call settings."""
    return sha256_text(
        canonical_json(
            {
                "prompt_prefix": PROMPT_PREFIX,
                "schema": TRIAGE_SCHEMA,
                "timeout_s": timeout_s,
                "max_tokens": {"byok": 2000, "ollama": 300},
            }
        )
    )


def environment_info() -> dict[str, Any]:
    """Return public package versions and the platform; never a host or path."""
    from importlib.metadata import PackageNotFoundError, version

    def package(name: str) -> str | None:
        try:
            return version(name)
        except PackageNotFoundError:  # pragma: no cover - installed in the project env
            return None

    return {
        "python": platform.python_version(),
        "presidio": package("presidio-analyzer"),
        "spacy": package("spacy"),
        "spacy_model": "de_core_news_lg-3.8.0",
        "faker": package("faker"),
        "platform": f"{sys.platform}-{platform.machine()}",
        "package_version": __version__,
    }


def build_backend(name: str) -> LLMBackend:
    """Construct one backend by its run name."""
    if name == "ollama":
        return BYOKBackend.for_ollama(OLLAMA_MODEL)
    return get_backend(name)


def probe_ollama(timeout_s: float = 5.0) -> tuple[bool, str | None]:
    """Check the tunnel endpoint and the model id without a generation call."""
    try:
        with httpx.Client(timeout=timeout_s) as client:
            response = client.get(f"{OLLAMA_BASE_URL.rstrip('/')}/models")
    except httpx.HTTPError:
        return False, "tunnel_down"
    if response.status_code != 200:
        return False, f"http_{response.status_code}"
    try:
        payload = response.json()
    except ValueError:
        return False, "invalid_response"
    data = payload.get("data") if isinstance(payload, dict) else None
    ids = (
        {
            item.get("id")
            for item in data
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        }
        if isinstance(data, list)
        else set()
    )
    if OLLAMA_MODEL not in ids:
        return False, "model_missing"
    return True, None


def availability(name: str) -> tuple[bool, str | None]:
    """Probe one backend's availability; never raises for a missing dependency."""
    if name == "ollama":
        return probe_ollama()
    backend = build_backend(name)
    if backend.is_available():
        return True, None
    return False, "not_installed" if name.endswith("-cli") else "not_configured"


def _ascii_digits(value: str) -> str:
    return "".join(character for character in value if character in "0123456789")


def leaked_types(text: str, gold_spans: Iterable[Any]) -> list[EntityType]:
    """Return the gold types whose value occurs in ``text`` (types only)."""
    haystack = normalise_value(text)
    haystack_digits = _ascii_digits(text)
    found: list[EntityType] = []
    for span in gold_spans:
        needle = normalise_value(str(span.text))
        hit = bool(needle) and needle in haystack
        if not hit and span.type in _DIGIT_TYPES:
            digits = _ascii_digits(str(span.text))
            hit = len(digits) >= 4 and digits in haystack_digits
        if hit and span.type not in found:
            found.append(span.type)
    return found


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path.name} is not a JSON object")
    return payload


def response_path(root: Path, backend: str, enquiry_id: str) -> Path:
    """Return the stored-response path for one backend and enquiry."""
    return root / "results" / "runs" / backend / "responses" / f"{enquiry_id}.json"


def index_path(root: Path, backend: str) -> Path:
    return root / "results" / "runs" / backend / "index.json"


def _model_label(backend: str, model: str | None) -> tuple[str | None, bool]:
    """Return ``(label, hashed)``; a private-config model id is only hashed."""
    if model is None:
        return None, backend == "byok"
    if backend == "byok":
        return f"sha256:{sha256_text(model)[:12]}", True
    return model, False


def _response_record(
    *,
    run_id: str,
    backend: str,
    enquiry_id: str,
    status: str,
    error_kind: str | None,
    model: str | None,
    latency_ms: int,
    usage: dict[str, Any] | None,
    prompt_sha256: str,
    text: str | None,
    parsed: dict[str, Any] | None,
    leaked: Sequence[EntityType] = (),
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "backend": backend,
        "enquiry_id": enquiry_id,
        "status": status,
        "error_kind": error_kind,
        "model": model,
        "model_hashed": backend == "byok",
        "latency_ms": max(0, latency_ms),
        "usage": usage,
        "prompt_sha256": prompt_sha256,
        "response_sha256": sha256_text(text) if text is not None else None,
        "draft_sha256": (
            sha256_text(str(parsed.get("reply_draft")))
            if isinstance(parsed, dict) and isinstance(parsed.get("reply_draft"), str)
            else None
        ),
        "text": text,
        "parsed": parsed,
        "leaked_types": [member.value for member in leaked],
        "created_at": utc_now(),
    }


def _warmup(
    backend_name: str,
    backend: LLMBackend,
    dev_text: str,
    timeout_s: int,
) -> dict[str, Any]:
    """One unscored dev call to load a model; never raises."""
    redaction = default_pipeline().redact_text(dev_text)
    prompt = build_prompt(redaction.redacted_text)
    result = backend.complete_json(prompt, TRIAGE_SCHEMA, timeout_s)
    return {
        "prompt_sha256": sha256_text(prompt),
        "latency_ms": result.latency_ms,
        "error_kind": result.error_kind,
        "model": _model_label(backend_name, result.model)[0],
    }


def run_triage(
    *,
    root: Path,
    backend_names: Sequence[str],
    mode: str,
    run_id: str,
    rerun_reason: str | None,
    method_approval: str | None,
    timeout_s: int | None,
    resume: bool,
    limit: int | None,
    warmup: bool,
    command: Sequence[str],
) -> tuple[Path, dict[str, Any]]:
    """Run the triage stage and return ``(run_dir, summary)``.

    The method guard is called before any test record is loaded. In ``dev`` mode
    the guard allows the run and no claim is written.
    """
    if mode not in {"test", "dev"}:
        raise ValueError("mode must be test or dev")
    commit = git_commit(root)
    effective_timeout = timeout_s or DEFAULT_TIMEOUT_S
    decision = method_guard_check(
        root=root,
        run_id=run_id,
        mode=mode,
        rerun_reason=rerun_reason,
        method_approval=method_approval,
    )

    run_dir = root / "results" / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    existing = _read_json(run_dir / "run.json") if (run_dir / "run.json").is_file() else {}
    started_at = str(existing.get("started_at") or utc_now())
    record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "mode": mode,
        "stage": STAGE,
        "status": "running",
        "method_sha256": decision.get("method_sha256"),
        "method_git_blob": decision.get("method_git_blob"),
        "commit": commit,
        "dirty": False,
        "config_sha256": config_sha256(effective_timeout),
        "data_sha256": {
            "synthetic_test": sha256_file(root / "data" / "synthetic" / "test.jsonl"),
            "synthetic_dev": sha256_file(root / "data" / "synthetic" / "dev.jsonl"),
        },
        "rerun_reason": rerun_reason,
        "method_approval": method_approval,
        "supersedes": existing.get("supersedes"),
        "started_at": started_at,
        "updated_at": utc_now(),
        "finished_at": None,
        "command": list(command),
        "environment": environment_info(),
        "backends": dict(existing.get("backends") or {}),
        "warmup": dict(existing.get("warmup") or {}),
        "extensions": list(existing.get("extensions") or []),
        "artifacts_sha256": dict(existing.get("artifacts_sha256") or {}),
    }
    if existing and set(backend_names) - set(existing.get("backends") or {}):
        record["extensions"].append(
            {
                "at": utc_now(),
                "backends_added": sorted(set(backend_names) - set(existing.get("backends") or {})),
                "reason": "optional backend run after the primary page export",
            }
        )
    _write_json(run_dir / "run.json", record)

    test_path = root / "data" / "synthetic" / "test.jsonl"
    dev_path = root / "data" / "synthetic" / "dev.jsonl"
    if mode == "test":
        subset = select_triage_subset(load_split(test_path, "test"))
    else:
        subset = load_split(dev_path, "dev")[:40]
    if limit is not None:
        if mode == "test":
            raise ValueError("--limit is only allowed in dev mode")
        subset = subset[:limit]
    ids = subset_ids(subset)
    record["subset"] = {"ids": ids, "count": len(ids)}
    _write_json(run_dir / "run.json", record)
    dev_rows = load_split(dev_path, "dev")
    dev_text = next(
        (row.text for row in dev_rows if row.id == WARMUP_DEV_ID),
        dev_rows[0].text,
    )

    pipeline = default_pipeline()
    redactions: dict[str, RedactionResult] = {
        enquiry.id: pipeline.redact_text(enquiry.text) for enquiry in subset
    }

    summary: dict[str, Any] = {"run_id": run_id, "backends": {}, "quota": [], "failures": []}
    for name in backend_names:
        available, reason = availability(name)
        entry = {
            "available": available,
            "availability_reason": reason,
            "timeout_s": OLLAMA_TIMEOUT_S if name == "ollama" else effective_timeout,
            "model": None,
            "model_hashed": name == "byok",
            "calls": 0,
            "expected": len(ids),
        }
        record["backends"][name] = entry
        _write_json(run_dir / "run.json", record)
        if not available:
            _write_json(
                index_path(root, name),
                {
                    "schema_version": SCHEMA_VERSION,
                    "run_id": run_id,
                    "backend": name,
                    "available": False,
                    "availability_reason": reason,
                    "calls": 0,
                    "expected": len(ids),
                    "responses_sha256": {},
                    "responses_sha256_combined": None,
                },
            )
            summary["backends"][name] = {"available": False, "reason": reason}
            continue

        backend = build_backend(name)
        backend_timeout = OLLAMA_TIMEOUT_S if name == "ollama" else effective_timeout
        _log(f"{name} available; {len(ids)} enquiries")
        if warmup and name != "fake":
            record["warmup"][name] = _warmup(name, backend, dev_text, backend_timeout)
            _write_json(run_dir / "run.json", record)

        backend_started = utc_now()
        calls = 0
        for enquiry in subset:
            target = response_path(root, name, enquiry.id)
            if resume and target.is_file():
                calls += 1
                continue
            redaction = redactions[enquiry.id]
            prompt = build_prompt(redaction.redacted_text)
            prompt_hash = sha256_text(prompt)
            try:
                require_clean(redaction.redacted_text, redaction.mapping)
            except GuardBlockedError as exc:
                _write_json(
                    target,
                    _response_record(
                        run_id=run_id,
                        backend=name,
                        enquiry_id=enquiry.id,
                        status=STATUS_GUARD_BLOCK,
                        error_kind=STATUS_GUARD_BLOCK,
                        model=None,
                        latency_ms=0,
                        usage=None,
                        prompt_sha256=prompt_hash,
                        text=None,
                        parsed=None,
                        leaked=exc.verdict.leaked_types,
                    ),
                )
                calls += 1
                continue

            prompt_leaks = leaked_types(prompt, enquiry.gold_spans)
            if prompt_leaks:
                _write_json(
                    target,
                    _response_record(
                        run_id=run_id,
                        backend=name,
                        enquiry_id=enquiry.id,
                        status=STATUS_LEAK,
                        error_kind=STATUS_LEAK,
                        model=None,
                        latency_ms=0,
                        usage=None,
                        prompt_sha256=prompt_hash,
                        text=None,
                        parsed=None,
                        leaked=prompt_leaks,
                    ),
                )
                calls += 1
                continue

            result = backend.complete_json(prompt, TRIAGE_SCHEMA, backend_timeout)
            model_label, _ = _model_label(name, result.model)
            if result.error_kind is not None:
                clean_text = (
                    result.text if not leaked_types(result.text, enquiry.gold_spans) else None
                )
                _write_json(
                    target,
                    _response_record(
                        run_id=run_id,
                        backend=name,
                        enquiry_id=enquiry.id,
                        status=STATUS_ERROR,
                        error_kind=result.error_kind,
                        model=model_label,
                        latency_ms=result.latency_ms,
                        usage=result.usage,
                        prompt_sha256=prompt_hash,
                        text=clean_text,
                        parsed=None,
                        leaked=leaked_types(result.text, enquiry.gold_spans),
                    ),
                )
                terminal = result.error_kind in {
                    "quota_exhausted",
                    "auth_error",
                    "billing_error",
                    "not_installed",
                    "not_configured",
                }
                if result.error_kind == "quota_exhausted":
                    summary["quota"].append(name)
                    _write_json(
                        run_dir / "quota.json",
                        {
                            "backend": name,
                            "at": utc_now(),
                            "error_kind": "quota_exhausted",
                            "completed_calls": calls,
                        },
                    )
                if terminal:
                    summary["failures"].append({"backend": name, "error_kind": result.error_kind})
                calls += 1
                if terminal:
                    break
                continue

            parsed = result.parsed
            response_leaks = leaked_types(result.text, enquiry.gold_spans)
            if isinstance(parsed, dict):
                draft = parsed.get("reply_draft")
                if isinstance(draft, str):
                    response_leaks = sorted(
                        set(response_leaks) | set(leaked_types(draft, enquiry.gold_spans)),
                        key=lambda member: member.value,
                    )
            if response_leaks:
                _write_json(
                    target,
                    _response_record(
                        run_id=run_id,
                        backend=name,
                        enquiry_id=enquiry.id,
                        status=STATUS_LEAK,
                        error_kind=STATUS_LEAK,
                        model=model_label,
                        latency_ms=result.latency_ms,
                        usage=result.usage,
                        prompt_sha256=prompt_hash,
                        text=None,
                        parsed=None,
                        leaked=response_leaks,
                    ),
                )
                calls += 1
                continue

            _write_json(
                target,
                _response_record(
                    run_id=run_id,
                    backend=name,
                    enquiry_id=enquiry.id,
                    status=STATUS_OK,
                    error_kind=None,
                    model=model_label,
                    latency_ms=result.latency_ms,
                    usage=result.usage,
                    prompt_sha256=prompt_hash,
                    text=result.text,
                    parsed=parsed,
                ),
            )
            calls += 1
            if calls % 5 == 0:
                _log(f"{name} {calls}/{len(ids)}")

        hashes = {
            enquiry_id: sha256_file(response_path(root, name, enquiry_id))
            for enquiry_id in ids
            if response_path(root, name, enquiry_id).is_file()
        }
        observed_models = {
            str(stored.get("model"))
            for enquiry_id in ids
            if (stored := _load_response(root, name, enquiry_id)) is not None
            and stored.get("model")
        }
        model_label = sorted(observed_models)[0] if observed_models else None
        _write_json(
            index_path(root, name),
            {
                "schema_version": SCHEMA_VERSION,
                "run_id": run_id,
                "backend": name,
                "available": True,
                "availability_reason": None,
                "model": model_label,
                "model_hashed": name == "byok",
                "started_at": backend_started,
                "finished_at": utc_now(),
                "calls": calls,
                "expected": len(ids),
                "timeout_s": backend_timeout,
                "responses_sha256": hashes,
                "responses_sha256_combined": sha256_text(canonical_json(hashes)),
            },
        )
        record["backends"][name]["model"] = model_label
        record["backends"][name]["calls"] = calls
        _log(f"{name} done {calls}/{len(ids)}")
        summary["backends"][name] = {"available": True, "calls": calls}
        _write_json(run_dir / "run.json", record)

    metrics = compute_metrics(run_dir=run_dir, root=root)
    _write_json(run_dir / "metrics.json", metrics)
    (run_dir / "report.md").write_text(render_report(metrics), encoding="utf-8")
    _write_scoring(run_dir, metrics)

    incomplete = bool(summary["quota"] or summary["failures"])
    record["status"] = "aborted" if incomplete else "complete"
    record["updated_at"] = utc_now()
    record["finished_at"] = utc_now()
    if summary["quota"]:
        record["abort_reason"] = "quota_exhausted"
    elif summary["failures"]:
        record["abort_reason"] = "backend_terminal_error"
    record["artifacts_sha256"] = {
        "metrics.json": sha256_file(run_dir / "metrics.json"),
        "report.md": sha256_file(run_dir / "report.md"),
    }
    _write_json(run_dir / "run.json", record)
    return run_dir, summary


def _load_response(root: Path, backend: str, enquiry_id: str) -> dict[str, Any] | None:
    path = response_path(root, backend, enquiry_id)
    if not path.is_file():
        return None
    try:
        return _read_json(path)
    except (OSError, ValueError):
        return {"status": STATUS_MISSING, "error_kind": "unreadable_response"}


def _confusion(pairs: Iterable[tuple[str, str]], labels: Sequence[str]) -> dict[str, Any]:
    matrix = [[0 for _ in labels] for _ in labels]
    index = {label: position for position, label in enumerate(labels)}
    counted = 0
    for gold, predicted in pairs:
        if gold not in index or predicted not in index:
            continue
        matrix[index[gold]][index[predicted]] += 1
        counted += 1
    return {"labels": list(labels), "matrix": matrix, "scored": counted}


def _placeholder_metrics(draft: str, mapping: Mapping[str, str]) -> dict[str, Any]:
    known = {canonical_placeholder(placeholder) for placeholder in mapping}
    tokens = _PLACEHOLDER_RE.findall(draft)
    mapped_tokens = [token for token in tokens if canonical_placeholder(token) in known]
    unknown_tokens = [token for token in tokens if canonical_placeholder(token) not in known]
    restored = restore(draft, mapping)
    unresolved = [
        token
        for token in _PLACEHOLDER_RE.findall(restored.text)
        if canonical_placeholder(token) in known
    ]
    return {
        "mapped_occurrences": len(mapped_tokens),
        "unresolved_mapped": len(unresolved),
        "unknown": len(unknown_tokens),
        "restore_exact": not unresolved and not unknown_tokens,
        "no_placeholder": not tokens,
        "with_input_placeholder": bool(mapped_tokens),
    }


def _backend_metrics(
    *,
    root: Path,
    backend: str,
    subset: Sequence[Enquiry],
    redactions: Mapping[str, RedactionResult],
    entry: Mapping[str, Any],
    rubric: Mapping[str, Any] | None,
) -> dict[str, Any]:
    ids = [enquiry.id for enquiry in subset]
    if not entry.get("available"):
        return {
            "available": False,
            "availability_reason": entry.get("availability_reason"),
            "calls": 0,
            "expected": len(ids),
            "note": "backend unavailable; not scored (METHOD 10)",
        }

    responses: dict[str, dict[str, Any]] = {}
    for enquiry in subset:
        stored = _load_response(root, backend, enquiry.id)
        responses[enquiry.id] = stored or {
            "status": STATUS_MISSING,
            "error_kind": "missing_response",
        }

    ok = {
        enquiry.id: responses[enquiry.id]
        for enquiry in subset
        if responses[enquiry.id].get("status") == STATUS_OK
    }
    joint = 0
    category_hits = 0
    urgency_hits = 0
    category_pairs: list[tuple[str, str]] = []
    urgency_pairs: list[tuple[str, str]] = []
    latencies: list[int] = []
    lengths: list[int] = []
    non_empty = 0
    with_input_placeholder = 0
    unknown_drafts = 0
    restore_exact = 0
    no_placeholder = 0
    unresolved_total = 0
    mapped_total = 0
    gold_value_drafts = 0
    error_kinds: Counter[str] = Counter()

    for enquiry in subset:
        stored = responses[enquiry.id]
        status = str(stored.get("status"))
        if status != STATUS_OK:
            error_kinds[str(stored.get("error_kind") or status)] += 1
            if stored.get("leaked_types"):
                gold_value_drafts += 1
            continue
        parsed = stored.get("parsed")
        if not isinstance(parsed, dict):
            error_kinds["invalid_response"] += 1
            continue
        category = str(parsed.get("category"))
        urgency = str(parsed.get("urgency"))
        draft = parsed.get("reply_draft")
        if not isinstance(draft, str):
            error_kinds["schema_error"] += 1
            continue
        category_pairs.append((enquiry.gold_category.value, category))
        urgency_pairs.append((enquiry.gold_urgency.value, urgency))
        category_hits += int(category == enquiry.gold_category.value)
        urgency_hits += int(urgency == enquiry.gold_urgency.value)
        joint += int(
            category == enquiry.gold_category.value and urgency == enquiry.gold_urgency.value
        )
        latency = stored.get("latency_ms")
        if isinstance(latency, int):
            latencies.append(latency)
        lengths.append(len(draft))
        non_empty += int(bool(draft.strip()))
        placeholders = _placeholder_metrics(draft, redactions[enquiry.id].mapping)
        mapped_total += int(placeholders["mapped_occurrences"])
        unresolved_total += int(placeholders["unresolved_mapped"])
        with_input_placeholder += int(bool(placeholders["with_input_placeholder"]))
        unknown_drafts += int(bool(placeholders["unknown"]))
        restore_exact += int(bool(placeholders["restore_exact"]))
        no_placeholder += int(bool(placeholders["no_placeholder"]))

    n = len(ids)
    scored = len(ok)
    return {
        "available": True,
        "availability_reason": None,
        "calls": len(
            [value for value in responses.values() if value.get("status") != STATUS_MISSING]
        ),
        "expected": n,
        "success": scored,
        "errors": n - scored,
        "error_kinds": dict(sorted(error_kinds.items())),
        "guard_blocks": sum(
            1 for value in responses.values() if value.get("status") == STATUS_GUARD_BLOCK
        ),
        "leaks": sum(1 for value in responses.values() if value.get("status") == STATUS_LEAK),
        "schema_invalid": error_kinds.get("schema_error", 0),
        "joint": wilson(joint, n),
        "category_only": wilson(category_hits, n),
        "urgency_only": wilson(urgency_hits, n),
        "confusion_category": _confusion(category_pairs, [category.value for category in Category]),
        "confusion_urgency": _confusion(urgency_pairs, [urgency.value for urgency in Urgency]),
        "latency_ms": latency_summary(latencies),
        "draft": {
            "scored": scored,
            "restore_exact": restore_exact,
            "restore_exact_rate": wilson(restore_exact, scored),
            "no_placeholder": no_placeholder,
            "unknown_placeholder_drafts": unknown_drafts,
            "unresolved_mapped_occurrences": unresolved_total,
            "mapped_occurrences": mapped_total,
            "non_empty": non_empty,
            "with_input_placeholder": with_input_placeholder,
            "gold_value_in_draft": gold_value_drafts,
            "median_length": sorted(lengths)[len(lengths) // 2] if lengths else None,
        },
        "rubric": rubric,
    }


def _subset_from_record(record: Mapping[str, Any], root: Path) -> list[Enquiry]:
    """Return the run's pinned subset, or the fixed rule when no ids are stored."""
    mode = str(record.get("mode", "test"))
    split = "test" if mode == "test" else "dev"
    rows = load_split(root / "data" / "synthetic" / f"{split}.jsonl", split)
    pinned = record.get("subset")
    if isinstance(pinned, dict):
        ids = pinned.get("ids")
        if isinstance(ids, list) and all(isinstance(value, str) for value in ids):
            by_id = {row.id: row for row in rows}
            return [by_id[value] for value in ids if value in by_id]
    if mode == "test":
        return select_triage_subset(rows)
    return rows[:40]


def compute_metrics(*, run_dir: Path, root: Path) -> dict[str, Any]:
    """Recompute every triage metric from stored responses and the corpus."""
    record = _read_json(run_dir / "run.json")
    mode = str(record.get("mode", "test"))
    subset = _subset_from_record(record, root)
    pipeline = default_pipeline()
    redactions = {enquiry.id: pipeline.redact_text(enquiry.text) for enquiry in subset}
    rubric_path = run_dir / "rubric.json"
    rubric = _read_json(rubric_path) if rubric_path.is_file() else None
    backends = record.get("backends")
    if not isinstance(backends, dict):
        backends = {}
    per_backend = {
        str(name): _backend_metrics(
            root=root,
            backend=str(name),
            subset=subset,
            redactions=redactions,
            entry=entry if isinstance(entry, dict) else {},
            rubric=rubric,
        )
        for name, entry in backends.items()
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": record.get("run_id"),
        "stage": STAGE,
        "mode": mode,
        "method_sha256": record.get("method_sha256"),
        "commit": record.get("commit"),
        "generated_at": utc_now(),
        "subset": {"ids": [enquiry.id for enquiry in subset], "count": len(subset)},
        "per_backend": per_backend,
        "notes": [
            "Denominator is the full subset for every available backend (intention-to-treat).",
            "Urgency is tied to category in this corpus, so urgency-only accuracy is "
            "not independent evidence.",
            "Latency is one machine and one network path, not a benchmark.",
            "No pass thresholds; these are descriptive measurements.",
        ],
    }


def _pct(metric: Mapping[str, Any]) -> str:
    rate = metric.get("rate")
    if rate is None:
        return "not estimable"
    return (
        f"{metric['k']}/{metric['n']} = {100.0 * float(rate):.1f}% "
        f"(95% CI {100.0 * float(metric['lower']):.1f}-{100.0 * float(metric['upper']):.1f}%)"
    )


def render_report(metrics: Mapping[str, Any]) -> str:
    """Render the human-readable triage report; counts and types only."""
    subset = metrics.get("subset")
    count = int(subset.get("count", 0)) if isinstance(subset, dict) else 0
    scope = (
        "The fixed 40-enquiry subset from METHOD.md section 8."
        if count == 40
        else f"The run's selected subset ({count} enquiries)."
    )
    lines: list[str] = [
        "# Triage report",
        "",
        f"Run: `{metrics.get('run_id')}`  ",
        f"Stage: `{metrics.get('stage')}`  ",
        f"Method SHA-256: `{metrics.get('method_sha256')}`  ",
        f"Commit: `{metrics.get('commit')}`",
        "",
        scope,
        f"Accuracy uses {count} as the denominator for every available backend.",
        "A failure, guard block or leak counts as an incorrect answer and is",
        "reported separately.",
        "",
        "## Backend quality",
        "",
    ]
    for name, entry in metrics.get("per_backend", {}).items():
        if not entry.get("available"):
            lines.append(
                f"- **{name}**: not measured ({entry.get('availability_reason') or 'unavailable'})"
            )
            continue
        lines.append(
            f"- **{name}**: joint {_pct(entry['joint'])}; category {_pct(entry['category_only'])}; "
            f"urgency {_pct(entry['urgency_only'])}; success {entry['success']}/"
            f"{entry['expected']}; failures {entry['errors']} "
            f"({json.dumps(entry['error_kinds'], sort_keys=True)})"
        )
        latency = entry["latency_ms"]
        if latency["n"]:
            lines.append(
                f"  - latency: n={latency['n']}, median {latency['median']:.1f} ms, "
                f"min {latency['min']:.1f}, max {latency['max']:.1f}, "
                f"p95 {latency['p95']:.1f} ms"
            )
        draft = entry["draft"]
        lines.append(
            f"  - drafts: restore-exact {_pct(draft['restore_exact_rate'])}; "
            f"no placeholders {draft['no_placeholder']}; unknown-placeholder drafts "
            f"{draft['unknown_placeholder_drafts']}; gold-value drafts "
            f"{draft['gold_value_in_draft']}; median length "
            f"{draft['median_length'] if draft['median_length'] is not None else 'n/a'}"
        )
        rubric = entry.get("rubric")
        if rubric:
            lines.append(
                f"  - rubric (n={rubric.get('n')}): "
                f"relevance {rubric.get('means', {}).get('relevance')}, "
                f"placeholder {rubric.get('means', {}).get('placeholder_handling')}, "
                f"German {rubric.get('means', {}).get('german_quality')}"
            )
    lines.extend(
        [
            "",
            "## Limitations",
            "",
            "- Synthetic template enquiries, not real customer mail.",
            "- 40 enquiries is small; intervals are wide.",
            "- Urgency is confounded with category in the corpus.",
            "- One sample per backend per enquiry; LLM output is not deterministic.",
            "- Latency is one machine and one network path.",
            "- One reviewer scores the draft rubric.",
            "- No pass thresholds; these are descriptive measurements of pseudonymisation,",
            "  not a compliance or legal claim.",
            "",
        ]
    )
    return "\n".join(lines)


def _write_scoring(run_dir: Path, metrics: Mapping[str, Any]) -> None:
    """Write per-enquiry booleans and types only (no text)."""
    scoring_dir = run_dir / "scoring"
    scoring_dir.mkdir(parents=True, exist_ok=True)
    for name in metrics.get("per_backend", {}):
        payload = _scoring_rows(run_dir, str(name))
        _write_json(scoring_dir / f"{name}.json", payload)


def _scoring_rows(run_dir: Path, backend: str) -> dict[str, Any]:
    record = _read_json(run_dir / "run.json")
    root = run_dir.parents[2]
    subset = _subset_from_record(record, root)
    rows: dict[str, Any] = {}
    for enquiry in subset:
        stored = _load_response(root, backend, enquiry.id) or {"status": STATUS_MISSING}
        raw_parsed = stored.get("parsed")
        parsed: dict[str, Any] = raw_parsed if isinstance(raw_parsed, dict) else {}
        category = str(parsed.get("category", ""))
        urgency = str(parsed.get("urgency", ""))
        rows[enquiry.id] = {
            "status": stored.get("status"),
            "error_kind": stored.get("error_kind"),
            "category_correct": category == enquiry.gold_category.value,
            "urgency_correct": urgency == enquiry.gold_urgency.value,
            "joint_correct": (
                category == enquiry.gold_category.value and urgency == enquiry.gold_urgency.value
            ),
            "leaked_types": list(stored.get("leaked_types") or []),
        }
    return rows


def latest_run_dir(root: Path) -> Path:
    """Return the newest triage run directory that has a ``run.json``."""
    candidates = [
        path.parent
        for path in (root / "results" / "runs").glob("*/run.json")
        if "-triage-" in path.parent.name
    ]
    if not candidates:
        raise FileNotFoundError("no triage run found under results/runs")
    return max(candidates, key=lambda path: path.name)


def reproduce(*, root: Path, run_dir: Path) -> tuple[dict[str, Any], bool]:
    """Rebuild metrics and report from stored responses; return ``(metrics, match)``."""
    stored_path = run_dir / "metrics.json"
    stored = _read_json(stored_path) if stored_path.is_file() else None
    metrics = compute_metrics(run_dir=run_dir, root=root)
    match = _same_numbers(stored, metrics)
    _write_json(stored_path, metrics)
    (run_dir / "report.md").write_text(render_report(metrics), encoding="utf-8")
    _write_scoring(run_dir, metrics)
    return metrics, match


def _same_numbers(left: object, right: object) -> bool:
    """Compare two metric trees, ignoring timestamps."""
    if isinstance(left, dict) and isinstance(right, dict):
        keys = (set(left) | set(right)) - {
            "generated_at",
            "created_at",
            "started_at",
            "finished_at",
            "updated_at",
        }
        return all(_same_numbers(left.get(key), right.get(key)) for key in keys)
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(
            _same_numbers(a, b) for a, b in zip(left, right, strict=True)
        )
    return left == right

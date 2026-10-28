"""Command line entry point: ``aipf redact|process|eval|serve``.

``redact`` and ``process`` run locally and print to the terminal. They never
log a value: ``redact`` prints the pseudonymised text on stdout and a span count
on stderr, and only prints the placeholder mapping when ``--show-mapping`` asks
for it, because that mapping contains the original values.

``serve`` binds ``127.0.0.1:8765`` unless ``AIPF_BIND=host:port`` says otherwise.

Exit codes: ``0`` success, ``1`` runtime failure (including a pipeline leg that is
still a stub), ``2`` refused (guard block, unknown backend, bad arguments or an
empty text).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Final

import uvicorn
from pydantic import ValidationError

from ai_privacy_filter import api
from ai_privacy_filter.backends.registry import get_backend
from ai_privacy_filter.guard import GuardBlockedError
from ai_privacy_filter.models import PageResults
from ai_privacy_filter.pipeline import (
    PipelineNotReadyError,
    default_pipeline,
    summarise_spans,
)
from ai_privacy_filter.pseudonymise import restore
from ai_privacy_filter.triage import TriageError

COMMANDS: dict[str, str] = {
    "redact": "pseudonymise a text and show the placeholder mapping",
    "process": "run the full detect -> guard -> LLM -> restore pipeline",
    "eval": "recompute detection and triage metrics from stored results",
    "serve": "run the local FastAPI service",
}

EXIT_OK: Final = 0
EXIT_ERROR: Final = 1
EXIT_REFUSED: Final = 2

DEFAULT_RESULTS: Final = Path("results/results.json")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aipf", description="AI Privacy Filter")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name, help_text in COMMANDS.items():
        subparser = subparsers.add_parser(name, help=help_text)
        if name in {"redact", "process"}:
            subparser.add_argument(
                "text",
                nargs="?",
                help="text to handle; omit or pass - to read standard input",
            )
        if name == "redact":
            subparser.add_argument(
                "--show-mapping",
                action="store_true",
                help="print placeholder -> original on stderr (contains personal data)",
            )
        if name == "process":
            subparser.add_argument("--backend", default="fake", help="backend name (default: fake)")
            subparser.add_argument(
                "--restore",
                action="store_true",
                help="put the original values back into the draft reply",
            )
        if name == "eval":
            subparser.add_argument(
                "--results",
                default=str(DEFAULT_RESULTS),
                help=f"stored results file (default: {DEFAULT_RESULTS})",
            )
        if name == "serve":
            subparser.add_argument("--host", help="bind host (default: AIPF_BIND or 127.0.0.1)")
            subparser.add_argument(
                "--port", type=int, help="bind port (default: AIPF_BIND or 8765)"
            )
            subparser.add_argument(
                "--reload", action="store_true", help="reload on source changes (development)"
            )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return the process exit code."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = build_parser().parse_args(argv)
    handlers: dict[str, Callable[[argparse.Namespace], int]] = {
        "redact": _redact,
        "process": _process,
        "eval": _eval,
        "serve": _serve,
    }
    return handlers[args.command](args)


def _read_text(raw: str | None) -> str:
    """Return the text argument, or everything on standard input."""
    if raw is None or raw == "-":
        return sys.stdin.read()
    return raw


def _redact(args: argparse.Namespace) -> int:
    text = _read_text(args.text)
    if not text.strip():
        print("aipf redact: empty text", file=sys.stderr)
        return EXIT_REFUSED
    pipeline = default_pipeline()
    try:
        result = pipeline.redact_text(text)
    except PipelineNotReadyError as exc:
        print(f"aipf redact: {exc}", file=sys.stderr)
        return EXIT_ERROR
    summary = summarise_spans(result.spans)
    print(result.redacted_text)
    total = sum(summary.values())
    counts = ", ".join(f"{name}={count}" for name, count in summary.items()) or "none"
    print(f"-- {total} span(s): {counts}", file=sys.stderr)
    if args.show_mapping:
        for placeholder, original in result.mapping.items():
            print(f"{placeholder} -> {original}", file=sys.stderr)
    return EXIT_OK


def _process(args: argparse.Namespace) -> int:
    text = _read_text(args.text)
    if not text.strip():
        print("aipf process: empty text", file=sys.stderr)
        return EXIT_REFUSED
    try:
        backend = get_backend(args.backend)
    except ValueError as exc:
        print(f"aipf process: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    pipeline = default_pipeline()
    try:
        outcome = pipeline.process(text, backend)
    except PipelineNotReadyError as exc:
        print(f"aipf process: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except GuardBlockedError as exc:
        leaked = ", ".join(member.value for member in exc.verdict.leaked_types) or "unknown type"
        print(
            f"aipf process: refused by the outbound guard ({leaked}); no model call was made",
            file=sys.stderr,
        )
        return EXIT_REFUSED
    except TriageError as exc:
        print(
            f"aipf process: backend {args.backend} failed ({exc.error_kind})",
            file=sys.stderr,
        )
        return EXIT_ERROR
    triage = outcome.triage.model_dump(mode="json")
    if args.restore:
        triage["reply_draft"] = restore(outcome.triage.reply_draft, outcome.mapping).text
    payload = {
        "triage": triage,
        "redacted_text": outcome.redacted_text,
        "spans_summary": outcome.spans_summary,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return EXIT_OK


def _eval(args: argparse.Namespace) -> int:
    path = Path(args.results)
    if not path.is_file():
        print(
            f"aipf eval: no stored results at {path}; run make reproduce first",
            file=sys.stderr,
        )
        return EXIT_ERROR
    try:
        results = PageResults.model_validate_json(path.read_text(encoding="utf-8"))
    except ValidationError:
        print(f"aipf eval: {path} is not a valid results file", file=sys.stderr)
        return EXIT_ERROR
    payload = {
        "detection": results.detection.model_dump(mode="json"),
        "triage": results.triage.model_dump(mode="json"),
        "meta": results.meta.model_dump(mode="json"),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return EXIT_OK


def _serve(args: argparse.Namespace) -> int:
    try:
        host, port = api.resolve_bind()
    except ValueError as exc:
        print(f"aipf serve: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    if args.host is not None:
        host = str(args.host)
    if args.port is not None:
        port = int(args.port)
    print(f"aipf serve: http://{host}:{port}", file=sys.stderr)
    if args.reload:
        uvicorn.run(
            "ai_privacy_filter.api:create_app",
            factory=True,
            host=host,
            port=port,
            reload=True,
            log_level="info",
        )
    else:
        uvicorn.run(api.create_app(), host=host, port=port, log_level="info")
    return EXIT_OK

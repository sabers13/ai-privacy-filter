"""Command line entry point: ``aipf redact|process|eval|serve``.

Lands with T08/T13. T01 ships the argument parser and a typed stub.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

COMMANDS: dict[str, str] = {
    "redact": "pseudonymise a text and show the placeholder mapping",
    "process": "run the full detect -> guard -> LLM -> restore pipeline",
    "eval": "recompute detection and triage metrics from stored results",
    "serve": "run the local FastAPI service",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aipf", description="AI Privacy Filter")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name, help_text in COMMANDS.items():
        subparser = subparsers.add_parser(name, help=help_text)
        if name in {"redact", "process"}:
            subparser.add_argument("text", nargs="?")
        if name == "process":
            subparser.add_argument("--backend", default="fake")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI. Commands land in T08/T13."""
    build_parser().parse_args(argv)
    raise NotImplementedError("the CLI lands in T08/T13")

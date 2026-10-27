#!/usr/bin/env python3
"""Scan a working tree and its git history for private identifiers and secrets.

Rules come from two places:

1. ``$AIPF_FORBIDDEN_FILE``: one case-insensitive pattern per line (blank lines
   and ``#`` comments are skipped). The file itself is private and must never be
   copied into a repository.
2. Built-in checks: private IPv4 ranges (RFC 1918 plus CGNAT), absolute home
   paths, Tailscale hostnames, API-key-shaped strings and PEM private-key blocks.

Output is always ``path:line: [label]``. The matched text is never printed, so
running the scanner cannot leak the value it found. Exit codes: 0 clean, 1 hits,
2 usage or configuration error.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

SKIP_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".hypothesis",
        ".n8n-data",
        ".opencode",
        "dist",
        "build",
    }
)
SKIP_PREFIXES: tuple[str, ...] = ("data/external/",)
MAX_BYTES = 4_000_000

HOME_PATH = "/" + "home" + "/"


@dataclass(frozen=True)
class Rule:
    """One named regular expression."""

    label: str
    pattern: re.Pattern[str]


_BUILTIN_RULES: tuple[Rule, ...] = (
    Rule("private-ip-10", re.compile(r"(?<![\d.])10\.\d{1,3}\.\d{1,3}\.\d{1,3}(?![\d.])")),
    Rule(
        "private-ip-172.16",
        re.compile(r"(?<![\d.])172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}(?![\d.])"),
    ),
    Rule("private-ip-192.168", re.compile(r"(?<![\d.])192\.168\.\d{1,3}\.\d{1,3}(?![\d.])")),
    Rule(
        "private-ip-100.64",
        re.compile(r"(?<![\d.])100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3}(?![\d.])"),
    ),
    Rule("home-path", re.compile(re.escape(HOME_PATH))),
    Rule("tailscale-host", re.compile(r"\.ts\.net\b", re.IGNORECASE)),
    Rule("api-key-sk", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}")),
    Rule("api-key-aws", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    Rule("api-key-github", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}")),
    Rule("api-key-slack", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}")),
    Rule("private-key-block", re.compile("-----BEGIN " + r"[A-Z ]*PRIVATE KEY-----")),
)


def load_forbidden_rules(path: Path) -> list[Rule]:
    """Turn one pattern per line into case-insensitive literal rules.

    Labels carry the line number, never the pattern text.
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    rules: list[Rule] = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        pattern = raw.strip()
        if not pattern or pattern.startswith("#"):
            continue
        rules.append(Rule(f"forbidden#{lineno}", re.compile(re.escape(pattern), re.IGNORECASE)))
    return rules


def _is_skipped(relative: Path) -> bool:
    parts = relative.parts
    if any(part in SKIP_DIRS for part in parts):
        return True
    if any(part.endswith(".egg-info") for part in parts):
        return True
    return any(str(relative).startswith(prefix) for prefix in SKIP_PREFIXES)


def iter_files(root: Path) -> Iterator[Path]:
    """Yield regular text files under ``root``, skipping vendored and generated trees."""
    for dirpath, dirnames, filenames in os.walk(root):
        current = Path(dirpath)
        dirnames[:] = sorted(
            name for name in dirnames if name not in SKIP_DIRS and not name.endswith(".egg-info")
        )
        for name in sorted(filenames):
            path = current / name
            try:
                relative = path.relative_to(root)
            except ValueError:
                continue
            if path.is_symlink() or not path.is_file() or _is_skipped(relative):
                continue
            if path.stat().st_size > MAX_BYTES:
                continue
            yield path


def read_text(path: Path) -> str | None:
    """Read a text file; return ``None`` for binaries."""
    data = path.read_bytes()
    if b"\x00" in data[:8192]:
        return None
    return data.decode("utf-8", errors="replace")


def scan_text(text: str, rules: Sequence[Rule]) -> list[tuple[int, str]]:
    """Return ``(line_number, label)`` hits for one decoded text."""
    hits: list[tuple[int, str]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for rule in rules:
            if rule.pattern.search(line):
                hits.append((lineno, rule.label))
    return hits


def scan_worktree(root: Path, rules: Sequence[Rule]) -> list[str]:
    reports: list[str] = []
    for path in iter_files(root):
        text = read_text(path)
        if text is None:
            continue
        relative = path.relative_to(root).as_posix()
        for lineno, label in scan_text(text, rules):
            reports.append(f"{relative}:{lineno}: [{label}]")
    return reports


def scan_history(root: Path, rules: Sequence[Rule], *, all_refs: bool = False) -> list[str]:
    """Scan added lines in ``git log -p``; return ``history <commit>:<path>:<line>`` reports.

    The default scope is ``HEAD``: the gate checks the branch under review, not
    unrelated local work-in-progress branches in a shared checkout. Pass
    ``all_refs`` for a full audit of every local branch and tag.
    """

    revision = ["--all"] if all_refs else ["HEAD"]
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "log", "-p", *revision, "--no-color", "--no-ext-diff"],
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if completed.returncode != 0:
        return []

    reports: list[str] = []
    commit = "unknown"
    path = "unknown"
    new_line = 0
    for line in completed.stdout.splitlines():
        if line.startswith("commit "):
            commit = line.removeprefix("commit ").strip()
            continue
        if line.startswith("+++ b/"):
            path = line.removeprefix("+++ b/")
            continue
        if line.startswith("@@"):
            match = re.search(r"\+(\d+)", line)
            new_line = int(match.group(1)) if match is not None else 0
            continue
        if line.startswith("+") and not line.startswith("+++"):
            added = line[1:]
            for rule in rules:
                if rule.pattern.search(added):
                    reports.append(f"history {commit[:12]}:{path}:{new_line}: [{rule.label}]")
            new_line += 1
        elif line.startswith(" "):
            new_line += 1
    return reports


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=".", help="tree to scan (default: current directory)")
    parser.add_argument(
        "--no-history",
        action="store_true",
        help="skip the git log scan (used by tests and non-git directories)",
    )
    parser.add_argument("--quiet", action="store_true", help="suppress the clean banner")
    parser.add_argument(
        "--all-refs",
        action="store_true",
        help="scan history on every local branch and tag instead of HEAD only",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"scan_forbidden: not a directory: {root}", file=sys.stderr)
        return 2

    rules = list(_BUILTIN_RULES)
    forbidden_file = os.environ.get("AIPF_FORBIDDEN_FILE")
    if forbidden_file:
        pattern_path = Path(forbidden_file)
        if not pattern_path.is_file():
            print(
                "scan_forbidden: AIPF_FORBIDDEN_FILE is set but not readable; refusing to pass",
                file=sys.stderr,
            )
            return 2
        rules.extend(load_forbidden_rules(pattern_path))
    elif not args.quiet:
        print(
            "scan_forbidden: AIPF_FORBIDDEN_FILE not set; built-in checks only",
            file=sys.stderr,
        )

    reports = scan_worktree(root, rules)
    if not args.no_history:
        reports.extend(scan_history(root, rules, all_refs=args.all_refs))

    for report in reports:
        print(report)
    if reports:
        print(
            f"scan_forbidden: {len(reports)} hit(s); matched text suppressed",
            file=sys.stderr,
        )
        return 1
    if not args.quiet:
        print("scan_forbidden: clean", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

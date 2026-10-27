"""Process isolation for native CLI backends.

Adapted from SaberOps, Apache-2.0 (``src/saberops/workers/base.py`` and
``src/saberops/process.py``).

Every CLI call runs in a fresh, empty temporary directory created with
``mkdtemp`` and removed afterwards, with the prompt fed on standard input and a
hermetic environment. The working directory is a sibling-free scratch area: the
CLI never starts inside this repository, so project instructions, sessions and
uncommitted files are out of reach even when a CLI still technically holds a
read-only file tool.

The process runs in its own session so a timeout kills the whole group rather
than only the direct child.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import tempfile
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "ProcessOutcome",
    "Runner",
    "empty_workdir",
    "run_process",
]

#: Prefix for every scratch directory this module creates.
WORKDIR_PREFIX = "aipf-cli-"


@dataclass(frozen=True)
class ProcessOutcome:
    """Captured result of one CLI invocation."""

    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool


#: Injectable runner signature used by the CLI backends in tests.
Runner = Callable[..., ProcessOutcome]


@contextmanager
def empty_workdir(parent: Path | None = None) -> Iterator[Path]:
    """Yield a new empty directory and remove it (and its contents) afterwards.

    ``parent`` defaults to the system temporary directory. Tests may point it at
    a scratch parent that holds a canary file, which the CLI can reach at
    ``../<canary>`` but must never read.
    """
    path = Path(tempfile.mkdtemp(prefix=WORKDIR_PREFIX, dir=parent))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def _decode(chunk: bytes | None) -> str:
    if not chunk:
        return ""
    return chunk.decode("utf-8", errors="replace")


def run_process(
    argv: Sequence[str],
    *,
    prompt: str,
    env: Mapping[str, str],
    timeout_s: int,
    cwd: Path,
) -> ProcessOutcome:
    """Run ``argv`` in ``cwd`` with ``prompt`` on stdin and no inherited env.

    A timeout kills the process group; partial output is still returned with
    ``timed_out=True`` so the caller can classify what arrived.
    """
    process = subprocess.Popen(
        list(argv),
        cwd=cwd,
        env=dict(env),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    timed_out = False
    try:
        stdout, stderr = process.communicate(
            input=prompt.encode("utf-8"),
            timeout=max(1.0, float(timeout_s)),
        )
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_group(process)
        try:
            stdout, stderr = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            stdout, stderr = b"", b""
    return ProcessOutcome(
        stdout=_decode(stdout),
        stderr=_decode(stderr),
        exit_code=process.returncode if process.returncode is not None else -1,
        timed_out=timed_out,
    )


def _kill_group(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):  # pragma: no cover - race
        with suppress(ProcessLookupError):
            process.kill()

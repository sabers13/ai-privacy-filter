"""Shared directory locks for live model calls.

``METHOD.md`` section 10 and the mission standing rules use two shared
directories:

* ``aipf-lock-live-llm`` -- at most one live caller in the mission;
* ``aipf-lock-server`` -- held while the optional Ollama host is installed,
  pulled or smoked.

A lock is acquired with ``mkdir`` (atomic) and released with ``rmdir``. A run
never holds a lock while idle. The lock directory lives outside the repository;
set ``AIPF_LOCK_DIR`` to the shared mission state directory. The default is a
per-user state directory, so a single machine still serialises its own runs.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from types import TracebackType
from typing import Final

LIVE_LOCK_NAME: Final = "aipf-lock-live-llm"
SERVER_LOCK_NAME: Final = "aipf-lock-server"
DEFAULT_POLL_S: Final = 60.0


class LockTimeout(RuntimeError):
    """The lock stayed busy for the whole time box."""

    def __init__(self, path: Path, waited_s: float) -> None:
        super().__init__(f"lock {path.name} busy for {waited_s:.0f}s")
        self.path = path
        self.waited_s = waited_s


def lock_root() -> Path:
    """Return the directory that holds the shared lock directories."""
    configured = os.environ.get("AIPF_LOCK_DIR")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".local" / "state" / "ai-privacy-filter"


class DirectoryLock:
    """A ``mkdir`` lock that retries until ``timeout_s`` and always releases."""

    def __init__(
        self,
        name: str,
        *,
        root: Path | None = None,
        timeout_s: float = 0.0,
        poll_s: float = DEFAULT_POLL_S,
    ) -> None:
        self.path = (root if root is not None else lock_root()) / name
        self.timeout_s = max(0.0, timeout_s)
        self.poll_s = max(0.1, poll_s)
        self._held = False

    def acquire(self) -> None:
        """Take the lock or raise :class:`LockTimeout`."""
        started = time.monotonic()
        while True:
            try:
                self.path.mkdir(parents=True)
            except FileExistsError:
                waited = time.monotonic() - started
                if waited >= self.timeout_s:
                    raise LockTimeout(self.path, waited) from None
                time.sleep(min(self.poll_s, self.timeout_s - waited))
                continue
            except OSError as exc:
                raise RuntimeError(f"cannot create lock {self.path.name}") from exc
            self._held = True
            return

    def release(self) -> None:
        """Release the lock if this object holds it."""
        if not self._held:
            return
        try:
            self.path.rmdir()
        except FileNotFoundError:
            pass
        finally:
            self._held = False

    @property
    def held(self) -> bool:
        return self._held

    def __enter__(self) -> DirectoryLock:
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.release()

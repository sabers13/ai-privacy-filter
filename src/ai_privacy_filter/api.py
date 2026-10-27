"""FastAPI service.

Lands in T08. T01 ships the typed signature only.
"""

from __future__ import annotations

from fastapi import FastAPI


def create_app() -> FastAPI:
    """Build the service app with /health, /redact, /restore and /process."""
    raise NotImplementedError("the FastAPI service lands in T08")

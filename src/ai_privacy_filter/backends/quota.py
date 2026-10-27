"""Quota and rate-limit classification.

Lands in T06 (ported from SaberOps ``exhaustion.py``). T01 ships the typed stub.
"""

from __future__ import annotations


def classify_quota(stderr: str) -> str | None:
    """Return a quota verdict (e.g. ``"QUOTA_EXHAUSTED"``) or ``None`` if unclear."""
    raise NotImplementedError("quota classification lands in T06")

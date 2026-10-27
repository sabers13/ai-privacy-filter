"""Personal-data detection.

Lands in T02 (Presidio analyzer + German recognizers). T01 ships the typed
signature only.
"""

from __future__ import annotations

from typing import Literal

from ai_privacy_filter.models import Span

Profile = Literal["strict", "balanced"]


def detect(text: str, *, profile: Profile = "balanced") -> list[Span]:
    """Return personal-data spans in ``text``.

    ``profile="strict"`` favours recall, ``profile="balanced"`` accepts more
    over-masking. Detect is local: it must not touch the network.
    """
    raise NotImplementedError("detect lands in T02")

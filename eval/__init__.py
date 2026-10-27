"""Detection evaluation harness for ai-privacy-filter.

The package implements the protocol in ``METHOD.md``. It reads the committed
synthetic corpus and the git-ignored GermEval 2014 download, scores B0, B1 and
B2 with one matching function and writes the deterministic result files under
``results/``.

Privacy: nothing here writes GermEval sentence text, a real-person value or a
private path to a result, a log line or the terminal. GermEval spans are
described by category only.
"""

from __future__ import annotations

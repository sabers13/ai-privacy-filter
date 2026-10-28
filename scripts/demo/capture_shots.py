#!/usr/bin/env python3
"""Capture the four review-stack screenshots from a running ``make demo`` stack.

The demo leaves three synthetic dev-split enquiries waiting for a reviewer. This
script completes them in one browser session: the enquiry named by ``--edit-id``
is corrected with ``--reply`` (default: the built-in corrected reply), the other
two are approved. It then writes the review form, the executions list, the
execution canvas and the result card into ``--screenshots`` and dumps the page
text of every screen into ``--evidence`` so the caller can run the forbidden
string scan before the images are kept.

Nothing is written inside the repository except the four PNGs. The generated
config file carries local form URLs and lives in a temporary directory.

Exit codes: 0 written, 1 failure, 2 usage error.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Final

ROOT: Final = Path(__file__).resolve().parents[2]
N8N_DATA: Final = ROOT / ".n8n-data"
PENDING_DIR: Final = N8N_DATA / "pending"
OUT_DIR: Final = N8N_DATA / "out"
BROWSER_SCRIPT: Final = Path(__file__).resolve().parent / "capture_shots.cjs"
DEFAULT_EDIT_ID: Final = "syn-dev-0001"
DEFAULT_EDIT_REPLY: Final = (
    "Vielen Dank für Ihre Anfrage. Gerne erstellen wir Ihnen ein Angebot für die "
    "technische Ausstattung Ihrer zwei neuen Besprechungsräume mit Preisen und "
    "Lieferzeiten. Wir melden uns bis Freitag mit den Details schriftlich bei Ihnen."
)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--screenshots",
        default=str(ROOT / "results" / "page" / "screenshots"),
        help="directory the four PNGs are written to",
    )
    parser.add_argument(
        "--evidence",
        required=True,
        help="directory for the page text dumps (keep it outside the repository)",
    )
    parser.add_argument("--edit-id", default=DEFAULT_EDIT_ID)
    parser.add_argument("--reply", default=DEFAULT_EDIT_REPLY)
    parser.add_argument(
        "--phases",
        default="forms,list,canvas,result",
        help="comma-separated steps to run (default: all four)",
    )
    return parser.parse_args(argv)


def _load_items(edit_id: str, reply: str) -> list[dict[str, Any]]:
    if not PENDING_DIR.is_dir():
        raise SystemExit("capture: .n8n-data/pending is missing; run make demo first")
    pending = sorted(PENDING_DIR.glob("*.json"))
    if len(pending) < 2:
        raise SystemExit("capture: fewer than two pending enquiries; run make demo first")
    items: list[dict[str, Any]] = []
    seen_edit = False
    for path in pending:
        answer = json.loads(path.read_text(encoding="utf-8"))
        identifier = str(answer["id"])
        form_url = str(answer.get("form_url", ""))
        if not form_url:
            raise SystemExit(f"capture: {identifier} has no review form URL")
        if identifier == edit_id:
            seen_edit = True
            items.append(
                {"id": identifier, "form_url": form_url, "decision": "edit", "reply": reply}
            )
        else:
            items.append({"id": identifier, "form_url": form_url, "decision": "approve"})
    if not seen_edit:
        raise SystemExit(f"capture: no pending enquiry named {edit_id}")
    return items


def _playwright_env(prefix: Path) -> dict[str, str]:
    """Reuse the pinned Playwright install from the e2e harness."""
    sys.path.insert(0, str(ROOT / "scripts" / "e2e"))
    from run_e2e import Browser  # sibling helper, imported on demand

    return Browser().env


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    screenshots = Path(args.screenshots).expanduser().resolve()
    evidence = Path(args.evidence).expanduser().resolve()
    evidence.mkdir(parents=True, exist_ok=True)

    config = {
        "baseUrl": os.environ.get("AIPF_N8N_URL", "http://127.0.0.1:5678"),
        "workflowId": "enquiry-triage",
        "editId": args.edit_id,
        "items": _load_items(args.edit_id, args.reply),
        "phases": [part for part in args.phases.split(",") if part],
        "screenshotsDir": str(screenshots),
        "evidenceDir": str(evidence),
        "outDir": str(OUT_DIR),
    }
    prefix = Path(os.environ.get("AIPF_PLAYWRIGHT_PREFIX", "/tmp/opencode/aipf-playwright"))
    env = _playwright_env(prefix)
    with tempfile.TemporaryDirectory(prefix="aipf-shots-") as scratch:
        config_path = Path(scratch) / "capture.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        completed = subprocess.run(
            ["node", str(BROWSER_SCRIPT), str(config_path)],
            cwd=ROOT,
            env=env,
            text=True,
            check=False,
        )
    if completed.returncode != 0:
        print("capture: FAIL", file=sys.stderr)
        return 1
    print("capture: four screenshots written", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

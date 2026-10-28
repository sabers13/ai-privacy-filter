#!/usr/bin/env python3
"""Local n8n demo: start the review stack, seed three enquiries, leave it up.

``make demo`` builds and starts the same compose stack as ``make e2e``, creates
the throwaway n8n owner, imports and activates ``workflows/enquiry-triage.json``
and posts three synthetic dev-split enquiries through the normal webhook, gate
and model path. It then prints the n8n URL and one pending review-form URL per
enquiry, and returns with the stack still running so a person can approve, edit
or reject each draft in a browser. Nothing is submitted headlessly and nothing
is written outside the git-ignored ``.n8n-data`` directory.

``make demo-down`` stops the compose project and removes ``.n8n-data``.

The compose stack, owner, activation, corpus and lock helpers live in
``run_e2e.py`` so the demo and the end-to-end check share one definition.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any, Final

# The helpers sit beside this file; importing them keeps one stack definition.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_e2e import (
    N8N_URL,
    Browser,
    Compose,
    E2EError,
    acquire_lock,
    activate_workflow,
    load_records,
    log,
    post_enquiry,
    release_lock,
    remove_n8n_data,
    require_env,
    reset_data_dirs,
    set_log_prefix,
    start_stack,
    static_default_lock,
    wait_pending,
)

DEMO_RECORDS: Final = 3
ENV_NAMES: Final = (
    "AIPF_N8N_OWNER_EMAIL",
    "AIPF_N8N_OWNER_PASSWORD",
    "AIPF_BYOK_BASE_URL",
    "AIPF_BYOK_API_KEY",
    "AIPF_BYOK_MODEL",
)


def up(args: argparse.Namespace) -> int:
    """Start the stack and post the demo enquiries; leave the stack running."""
    require_env(ENV_NAMES)
    os.environ.setdefault("AIPF_BACKEND", "byok")
    os.environ.setdefault("AIPF_GATE_URL", "http://gate:8765")

    lock_path = Path(os.environ.get("AIPF_LIVE_LLM_LOCK", static_default_lock()))
    compose = Compose()
    locked = False
    try:
        reset_data_dirs()
        start_stack(compose)

        Browser().call("setup", timeout=180)
        log("n8n owner is ready")

        activate_workflow(compose)

        records = load_records(DEMO_RECORDS)
        acquire_lock(lock_path, timeout_s=args.lock_wait_s)
        locked = True

        answers: list[dict[str, Any]] = []
        for record in records:
            post_enquiry(record)
            answer = wait_pending(record["id"])
            answers.append(answer)
            log(f"enquiry {record['id']} reached the review step")
    except E2EError as exc:
        log(f"FAIL: {exc}")
        logs = compose.call("logs", "--no-color", "--tail=60", capture=True)
        if logs.stdout:
            sys.stderr.write(logs.stdout)
        compose.call("down", "--remove-orphans", capture=True, timeout=180)
        remove_n8n_data(required=False)
        return 1
    finally:
        if locked:
            release_lock(lock_path)
        compose.close()

    print(flush=True)
    log(f"n8n is running at {N8N_URL}")
    log("review forms, one per enquiry (nothing has been submitted):")
    for index, answer in enumerate(answers, start=1):
        print(f"demo:   {index}. {answer['form_url']}", flush=True)
    log("the stack stays up; stop it and delete .n8n-data with: make demo-down")
    return 0


def down() -> int:
    """Stop the compose project and delete the throwaway n8n state."""
    compose = Compose()
    try:
        compose.must("down", "--remove-orphans", timeout=180)
        remove_n8n_data(required=True)
        log("stack stopped and .n8n-data removed")
        return 0
    except E2EError as exc:
        log(f"FAIL: {exc}")
        return 1
    finally:
        compose.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--down",
        action="store_true",
        help="stop the stack and remove .n8n-data instead of starting it",
    )
    parser.add_argument("--lock-wait-s", type=float, default=1800.0)
    args = parser.parse_args()

    set_log_prefix("demo")
    if args.down:
        return down()
    return up(args)


if __name__ == "__main__":
    raise SystemExit(main())

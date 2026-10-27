"""Static checks for the n8n workflow, compose stack and end-to-end harness.

These tests do not start a container. They pin the pieces that must agree:
the compose contract, the exported workflow, the output paths, the committed
canvas screenshot and the absence of private identifiers.

The live run itself is ``make e2e``; it needs Docker or podman, the private
environment and the live-LLM lock, so it is not part of ``make test``.
"""

from __future__ import annotations

import json
import re
import struct
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "compose.yaml"
WORKFLOW = ROOT / "workflows" / "enquiry-triage.json"
SCREENSHOT = ROOT / "results" / "page" / "n8n-workflow.png"
E2E_SCRIPT = ROOT / "scripts" / "e2e" / "run_e2e.py"
BROWSER_SCRIPT = ROOT / "scripts" / "e2e" / "n8n_browser.cjs"


def _nodes() -> dict[str, dict[str, Any]]:
    workflow = json.loads(WORKFLOW.read_text(encoding="utf-8"))
    return {node["name"]: node for node in workflow["nodes"]}


def _workflow() -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(WORKFLOW.read_text(encoding="utf-8"))
    return loaded


def test_compose_pins_n8n_and_keeps_it_on_loopback() -> None:
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    n8n = compose["services"]["n8n"]
    image = str(n8n["image"])
    assert image.startswith("docker.io/n8nio/n8n:")
    assert re.search(r":2\.\d+\.\d+@sha256:[0-9a-f]{64}$", image)
    assert "127.0.0.1:5678:5678" in n8n["ports"]
    assert "./.n8n-data:/data/n8n" in n8n["volumes"]
    assert "./.n8n-data/out:/data/out" in n8n["volumes"]
    assert "./.n8n-data/pending:/data/pending" in n8n["volumes"]

    environment = n8n["environment"]
    assert environment["N8N_USER_FOLDER"] == "/data/n8n"
    assert environment["N8N_DIAGNOSTICS_ENABLED"] == "false"
    assert environment["N8N_PERSONALIZATION_ENABLED"] == "false"
    assert environment["N8N_SECURE_COOKIE"] == "false"
    assert environment["AIPF_GATE_URL"] == "${AIPF_GATE_URL:-http://gate:8765}"
    assert environment["AIPF_BACKEND"] == "${AIPF_BACKEND:-byok}"
    assert "N8N_RESTRICT_FILE_ACCESS_TO" in environment


def test_compose_has_no_literal_secrets() -> None:
    text = COMPOSE.read_text(encoding="utf-8")
    for name in ("AIPF_BYOK_API_KEY", "AIPF_N8N_OWNER_PASSWORD", "AIPF_BYOK_BASE_URL"):
        for line in text.splitlines():
            if name in line:
                assert "${" in line or line.strip().endswith(":"), line
    assert not re.search(r"(?i)(api[_-]?key|password)\s*:\s*['\"]?[A-Za-z0-9_\-]{16,}", text)


def test_workflow_is_active_and_has_no_credentials() -> None:
    workflow = _workflow()
    assert workflow["active"] is True
    assert workflow["id"] == "enquiry-triage"
    raw = WORKFLOW.read_text(encoding="utf-8")
    assert '"credentials"' not in raw
    assert '"authentication": "basicAuth"' not in raw
    assert '"authentication": "headerAuth"' not in raw


def test_workflow_wires_webhook_gate_guard_and_form() -> None:
    nodes = _nodes()
    webhook = nodes["Enquiry webhook"]["parameters"]
    assert webhook["httpMethod"] == "POST"
    assert webhook["path"] == "enquiry"

    process = nodes["Triage via gate"]["parameters"]
    assert "AIPF_GATE_URL" in process["url"]
    assert process["url"].endswith("/process")
    assert "AIPF_BACKEND" in process["jsonBody"]
    assert process["options"]["response"]["response"]["neverError"] is True

    guard = nodes["Guard ok?"]
    assert guard["type"] == "n8n-nodes-base.if"

    form = nodes["Approve reply"]["parameters"]
    assert form["resume"] == "form"
    assert form["formTitle"] == "Approve reply"
    fields = {field["fieldLabel"]: field for field in form["formFields"]["values"]}
    assert {"Decision", "Category", "Urgency", "Redacted text", "Reply draft"} <= set(fields)
    assert fields["Decision"]["fieldType"] == "dropdown"
    options = {item["option"] for item in fields["Decision"]["fieldOptions"]["values"]}
    assert options == {"approve", "edit", "reject"}
    assert "restored_draft" in fields["Reply draft"]["defaultValue"]


def test_workflow_writes_the_review_result_under_data_out() -> None:
    nodes = _nodes()
    write = nodes["Write result"]["parameters"]
    assert write["operation"] == "write"
    assert write["fileName"].startswith("=/data/out/")
    assert write["fileName"].endswith(".json")
    pending = nodes["Write pending"]["parameters"]
    assert pending["fileName"].startswith("=/data/pending/")
    blocked = nodes["Write blocked record"]["parameters"]
    assert blocked["fileName"].startswith("=/data/pending/")


def test_n8n_data_stays_git_ignored() -> None:
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert ".n8n-data/" in ignore


def test_make_e2e_runs_the_harness() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert re.search(r"^e2e:\n\tscripts/e2e/run\.sh$", makefile, re.MULTILINE)
    assert (ROOT / "scripts" / "e2e" / "run.sh").stat().st_mode & 0o111
    assert (ROOT / "scripts" / "e2e" / "run_e2e.py").stat().st_mode & 0o111


def test_harness_holds_the_live_lock_and_checks_the_hash() -> None:
    text = E2E_SCRIPT.read_text(encoding="utf-8")
    assert "aipf-lock-live-llm" in text
    assert "outbound_sha256" in text or "sha256" in text
    assert "assert_log_clean" in text
    assert "/data/out/" in text
    browser = BROWSER_SCRIPT.read_text(encoding="utf-8")
    assert "chromium.launch" in browser
    assert "headless: true" in browser


def test_canvas_screenshot_is_1280_pixels_wide() -> None:
    header = SCREENSHOT.read_bytes()[:24]
    assert header[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", header[16:24])
    assert width == 1280
    assert height > 0


def test_public_files_do_not_carry_private_identifiers() -> None:
    paths = [COMPOSE, WORKFLOW, E2E_SCRIPT, BROWSER_SCRIPT, ROOT / "docs" / "n8n.md"]
    home_prefix = "/" + "home" + "/"
    patterns = (
        re.compile(re.escape(home_prefix)),
        re.compile(r"\.ts\.net", re.IGNORECASE),
        re.compile(r"(?<![\d.])10\.\d{1,3}\.\d{1,3}\.\d{1,3}(?![\d.])"),
        re.compile(r"(?<![\d.])192\.168\.\d{1,3}\.\d{1,3}(?![\d.])"),
        re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),
    )
    for path in paths:
        text = path.read_text(encoding="utf-8")
        for pattern in patterns:
            assert not pattern.search(text), f"{path.name} matches {pattern.pattern}"

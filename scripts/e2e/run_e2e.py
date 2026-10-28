#!/usr/bin/env python3
"""End-to-end check: synthetic enquiries through n8n, the gate and back.

The check starts the local compose stack (gate + n8n), creates the throwaway
n8n owner, imports and activates ``workflows/enquiry-triage.json``, posts five
synthetic dev-split enquiries to the production webhook, completes each review
form in headless Chromium, and asserts:

* five ``/data/out/<id>.json`` files exist and carry a restored draft;
* the outbound text in each file is exactly the text the gate hashed into its
  log, so what n8n stored is what the model received;
* no gold value from the synthetic record occurs in that outbound text, in the
  gate log, or anywhere in the gate-to-model path;
* all three review decisions (approve, edit, reject) are written correctly.

Scratch state stays in the git-ignored ``.n8n-data`` directory. The run starts
from an empty one, and the live-LLM lock is held for its whole duration.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any, Final

ROOT: Final = Path(__file__).resolve().parents[2]
COMPOSE_FILE: Final = ROOT / "compose.yaml"
WORKFLOW_FILE: Final = ROOT / "workflows" / "enquiry-triage.json"
WORKFLOW_ID: Final = "enquiry-triage"
N8N_URL: Final = "http://127.0.0.1:5678"
N8N_DATA: Final = ROOT / ".n8n-data"
OUT_DIR: Final = N8N_DATA / "out"
PENDING_DIR: Final = N8N_DATA / "pending"
SCREENSHOT: Final = ROOT / "results" / "page" / "n8n-workflow.png"
DEV_CORPUS: Final = ROOT / "data" / "synthetic" / "dev.jsonl"
BROWSER_SCRIPT: Final = ROOT / "scripts" / "e2e" / "n8n_browser.cjs"
PLAYWRIGHT_VERSION: Final = "1.64.1"
DEFAULT_RECORDS: Final = 5
PLACEHOLDER: Final = re.compile(r"\[[A-Z_]+_\d+\]")
DECISIONS: Final = ("approve", "edit", "approve", "reject", "approve")


class E2EError(RuntimeError):
    """A failed assertion with a human-readable reason."""


def log(message: str) -> None:
    print(f"e2e: {message}", flush=True)


def run(
    command: Sequence[str],
    *,
    env: dict[str, str] | None = None,
    capture: bool = True,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    log("$ " + " ".join(command))
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=capture,
        timeout=timeout,
        check=False,
    )
    if capture and completed.returncode != 0:
        if completed.stdout:
            sys.stderr.write(completed.stdout)
        if completed.stderr:
            sys.stderr.write(completed.stderr)
    return completed


def require_env(names: Iterable[str]) -> None:
    missing = [name for name in names if not os.environ.get(name)]
    if missing:
        raise E2EError("missing environment variable(s): " + ", ".join(missing))


def wait_n8n(*, timeout_s: float) -> None:
    """Wait until n8n answers its health probe with a real ``ok``."""
    deadline = time.monotonic() + timeout_s
    last = ""
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{N8N_URL}/healthz", timeout=3) as response:
                body = response.read(200).decode("utf-8", errors="replace")
            try:
                if response.status == 200 and json.loads(body).get("status") == "ok":
                    return
                last = "not ready"
            except json.JSONDecodeError:
                last = "starting up"
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            last = type(exc).__name__
        time.sleep(1.0)
    raise E2EError(f"timed out waiting for n8n ({last})")


def remove_n8n_data(*, required: bool) -> None:
    """Remove the bind-mounted n8n state, including container-owned files.

    A rootless container writes its files as a subordinate uid, so a plain
    ``rmtree`` from the host cannot descend into them. ``podman unshare`` runs
    with the same mapping and can.
    """
    if not N8N_DATA.exists():
        return
    podman = shutil.which("podman")
    if podman is not None:
        subprocess.run(
            [podman, "unshare", "rm", "-rf", str(N8N_DATA)],
            capture_output=True,
            check=False,
        )
    if N8N_DATA.exists():
        shutil.rmtree(N8N_DATA, ignore_errors=True)
    if required and N8N_DATA.exists():
        raise E2EError("could not remove .n8n-data")


def acquire_lock(path: Path, *, timeout_s: float) -> None:
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            path.mkdir(parents=True, exist_ok=False)
            log(f"live-LLM lock acquired: {path.name}")
            return
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise E2EError(f"live-LLM lock is held by another task: {path.name}") from None
            time.sleep(10.0)


def release_lock(path: Path) -> None:
    try:
        path.rmdir()
        log(f"live-LLM lock released: {path.name}")
    except FileNotFoundError:
        pass
    except OSError as exc:
        log(f"could not release the lock ({type(exc).__name__})")


class Compose:
    """A compose invocation bound to a private podman socket when needed."""

    def __init__(self) -> None:
        self.env = dict(os.environ)
        self._service: subprocess.Popen[bytes] | None = None
        self._tempdir: Path | None = None
        binary = shutil.which("docker")
        if binary is None:
            raise E2EError("docker (or the podman docker wrapper) was not found")
        self.command = [binary, "compose", "--project-name", "aipf-e2e", "-f", str(COMPOSE_FILE)]
        if not self._usable_docker_host():
            self._start_private_socket()

    def _usable_docker_host(self) -> bool:
        host = self.env.get("DOCKER_HOST", "")
        candidates = [host] if host else []
        runtime_dir = os.environ.get("XDG_RUNTIME_DIR", "/run/user/0")
        candidates.extend(
            [
                f"unix://{Path.home()}/.docker/run/docker.sock",
                "unix:///var/run/docker.sock",
                f"unix://{runtime_dir}/podman/podman.sock",
            ]
        )
        for candidate in candidates:
            if not candidate.startswith("unix://"):
                continue
            path = Path(candidate[len("unix://") :])
            if path.exists() and os.access(path, os.W_OK):
                self.env["DOCKER_HOST"] = candidate
                return True
        return False

    def _start_private_socket(self) -> None:
        podman = shutil.which("podman")
        if podman is None:
            raise E2EError(
                "no Docker socket is available and podman is not installed; "
                "set DOCKER_HOST to a running daemon"
            )
        self._tempdir = Path(tempfile.mkdtemp(prefix="aipf-e2e-"))
        socket_path = self._tempdir / "podman.sock"
        log("starting a private podman service for this run")
        self._service = subprocess.Popen(
            [podman, "system", "service", "--time=0", f"unix://{socket_path}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if socket_path.exists() and self._service.poll() is None:
                self.env["DOCKER_HOST"] = f"unix://{socket_path}"
                return
            time.sleep(0.25)
        raise E2EError("the private podman service did not start")

    def call(
        self,
        *arguments: str,
        capture: bool = True,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return run([*self.command, *arguments], env=self.env, capture=capture, timeout=timeout)

    def must(self, *arguments: str, timeout: float | None = None) -> str:
        completed = self.call(*arguments, timeout=timeout)
        if completed.returncode != 0:
            raise E2EError(f"compose {' '.join(arguments)} failed")
        return completed.stdout

    def close(self) -> None:
        if self._service is not None:
            self._service.terminate()
            try:
                self._service.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._service.kill()
        if self._tempdir is not None:
            shutil.rmtree(self._tempdir, ignore_errors=True)


def playwright_env(prefix: Path) -> dict[str, str]:
    """Install the pinned Playwright package outside the worktree, once."""
    env = dict(os.environ)
    package = prefix / "node_modules" / "playwright" / "package.json"
    if not package.exists():
        prefix.mkdir(parents=True, exist_ok=True)
        install_env = dict(os.environ)
        install_env["PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD"] = "1"
        completed = run(
            [
                "npm",
                "install",
                "--prefix",
                str(prefix),
                "--no-save",
                "--no-audit",
                "--no-fund",
                f"playwright@{PLAYWRIGHT_VERSION}",
            ],
            env=install_env,
            timeout=300,
        )
        if completed.returncode != 0:
            raise E2EError("could not install the pinned Playwright package")
    env["NODE_PATH"] = str(prefix / "node_modules")
    return env


class Browser:
    def __init__(self) -> None:
        prefix = Path(os.environ.get("AIPF_PLAYWRIGHT_PREFIX", "/tmp/opencode/aipf-playwright"))
        self.env = playwright_env(prefix)

    def call(self, *arguments: str, timeout: float = 600) -> None:
        completed = run(
            ["node", str(BROWSER_SCRIPT), *arguments],
            env=self.env,
            timeout=timeout,
        )
        if completed.returncode != 0:
            raise E2EError(f"browser step failed: {arguments[0] if arguments else 'unknown'}")


def post_enquiry(record: dict[str, Any]) -> None:
    body = json.dumps({"id": record["id"], "text": record["text"]}).encode("utf-8")
    request = urllib.request.Request(
        f"{N8N_URL}/webhook/enquiry",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            if response.status >= 400:
                raise E2EError(f"enquiry {record['id']} was rejected with HTTP {response.status}")
    except urllib.error.HTTPError as exc:
        raise E2EError(f"enquiry {record['id']} was rejected with HTTP {exc.code}") from None
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise E2EError(f"enquiry {record['id']} did not reach n8n ({type(exc).__name__})") from None


def wait_pending(identifier: str, *, timeout_s: float = 300.0) -> dict[str, Any]:
    path = PENDING_DIR / f"{identifier}.json"
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if path.exists():
            try:
                answer = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                time.sleep(0.5)
                continue
            if answer.get("status") != "awaiting_approval":
                raise E2EError(
                    f"enquiry {identifier} stopped with {answer.get('status')} "
                    f"(gate {answer.get('gate_status')})"
                )
            if not answer.get("form_url"):
                raise E2EError(f"enquiry {identifier} has no review form URL")
            return answer
        time.sleep(0.5)
    raise E2EError(f"enquiry {identifier} did not reach the review step")


def normalise(text: str) -> str:
    return " ".join(text.casefold().split())


def digits(text: str) -> str:
    return re.sub(r"\D", "", text)


def assert_no_gold(outbound: str, record: dict[str, Any], where: str) -> None:
    haystack = normalise(outbound)
    haystack_digits = digits(outbound)
    for span in record.get("gold_spans", []):
        value = str(span.get("text", ""))
        if len(value.strip()) < 3:
            continue
        if span.get("type") in {"PHONE", "IBAN"}:
            needle = digits(value)
            if len(needle) >= 4 and needle in haystack_digits:
                raise E2EError(f"{where}: a {span['type']} value reached the outbound text")
            continue
        if normalise(value) in haystack:
            raise E2EError(f"{where}: a {span['type']} value reached the outbound text")


def assert_log_clean(gate_log: str, records: Sequence[dict[str, Any]]) -> None:
    haystack = normalise(gate_log)
    haystack_digits = digits(gate_log)
    for record in records:
        for span in record.get("gold_spans", []):
            value = str(span.get("text", ""))
            if len(value.strip()) < 3:
                continue
            if span.get("type") in {"PHONE", "IBAN"}:
                needle = digits(value)
                if len(needle) >= 4 and needle in haystack_digits:
                    raise E2EError("the gate log carries a PHONE or IBAN value")
                continue
            if normalise(value) in haystack:
                raise E2EError(f"the gate log carries a {span['type']} value")


def load_records(count: int) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with DEV_CORPUS.open(encoding="utf-8") as handle:
        for line in handle:
            if len(records) >= count:
                break
            if line.strip():
                records.append(json.loads(line))
    if len(records) != count:
        raise E2EError(f"expected {count} dev-split records, found {len(records)}")
    return records


def approval_plan(answers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    plan: list[dict[str, Any]] = []
    for index, answer in enumerate(answers):
        decision = DECISIONS[index % len(DECISIONS)]
        reply = None
        if decision == "edit":
            reply = "Vielen Dank für Ihre Nachricht. Wir melden uns mit einem Angebot bis Freitag."
        plan.append(
            {
                "id": answer["id"],
                "form_url": answer["form_url"],
                "decision": decision,
                "reply": reply,
                "expected_draft": answer.get("restored_draft", ""),
            }
        )
    return plan


def validate_output(
    result: dict[str, Any],
    record: dict[str, Any],
    answer: dict[str, Any],
    decision: str,
    gate_log: str,
) -> None:
    identifier = record["id"]
    if result.get("id") != identifier:
        raise E2EError(f"{identifier}: output id does not match")
    for field in ("redacted_text", "restored_draft", "category", "urgency", "decision"):
        if not result.get(field):
            raise E2EError(f"{identifier}: output has no {field}")
    if result.get("category") != answer.get("category"):
        raise E2EError(f"{identifier}: category differs between response and output")
    if result.get("urgency") != answer.get("urgency"):
        raise E2EError(f"{identifier}: urgency differs between response and output")
    if result.get("decision") != decision:
        raise E2EError(f"{identifier}: decision differs from the submitted one")
    if decision == "reject":
        if result.get("approved") is not False or result.get("final_reply") is not None:
            raise E2EError(f"{identifier}: rejected enquiry was recorded as approved")
    else:
        if result.get("approved") is not True:
            raise E2EError(f"{identifier}: approved enquiry was recorded as rejected")
        if decision == "approve" and result.get("final_reply") != result.get("restored_draft"):
            raise E2EError(f"{identifier}: approve did not keep the restored draft")

    outbound = str(result["redacted_text"])
    assert_no_gold(outbound, record, identifier)
    digest = hashlib.sha256(outbound.encode("utf-8")).hexdigest()
    if digest not in gate_log:
        raise E2EError(f"{identifier}: the gate log does not contain the outbound hash")

    draft = str(result.get("redacted_draft", ""))
    restored = str(result["restored_draft"])
    if re.search(r"\[[A-Z_]+_\d+\]", restored):
        raise E2EError(f"{identifier}: restored draft still contains a placeholder")
    if result.get("unknown_placeholders"):
        raise E2EError(f"{identifier}: restore reported unknown placeholders")
    placeholders = PLACEHOLDER.findall(draft)
    if placeholders:
        if restored == draft:
            raise E2EError(f"{identifier}: draft placeholders were not restored")
        restored_text = normalise(restored)
        restored_digits = digits(restored)
        for span in record.get("gold_spans", []):
            value = str(span.get("text", ""))
            if span.get("type") in {"PHONE", "IBAN"}:
                if len(digits(value)) >= 4 and digits(value) in restored_digits:
                    return
            elif normalise(value) in restored_text:
                return
        raise E2EError(f"{identifier}: no synthetic gold value was restored into the draft")


def wait_outputs(count: int, *, timeout_s: float) -> dict[str, Path]:
    deadline = time.monotonic() + timeout_s
    files: dict[str, Path] = {}
    while time.monotonic() < deadline:
        files = {path.stem: path for path in OUT_DIR.glob("*.json")}
        if len(files) >= count:
            return files
        time.sleep(1.0)
    raise E2EError(f"only {len(files)} of {count} output files appeared")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records", type=int, default=DEFAULT_RECORDS)
    parser.add_argument("--keep", action="store_true", help="leave the compose stack running")
    parser.add_argument("--no-screenshot", action="store_true")
    parser.add_argument("--lock-wait-s", type=float, default=1800.0)
    args = parser.parse_args()

    require_env(
        [
            "AIPF_N8N_OWNER_EMAIL",
            "AIPF_N8N_OWNER_PASSWORD",
            "AIPF_BYOK_BASE_URL",
            "AIPF_BYOK_API_KEY",
            "AIPF_BYOK_MODEL",
        ]
    )
    os.environ.setdefault("AIPF_BACKEND", "byok")
    os.environ.setdefault("AIPF_GATE_URL", "http://gate:8765")

    lock_path = Path(
        os.environ.get(
            "AIPF_LIVE_LLM_LOCK",
            static_default_lock(),
        )
    )
    compose: Compose | None = None
    locked = False
    succeeded = False
    try:
        acquire_lock(lock_path, timeout_s=args.lock_wait_s)
        locked = True

        log("resetting .n8n-data")
        remove_n8n_data(required=True)
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        PENDING_DIR.mkdir(parents=True, exist_ok=True)
        N8N_DATA.chmod(0o777)
        OUT_DIR.chmod(0o777)
        PENDING_DIR.chmod(0o777)

        compose = Compose()
        compose.must("down", "--remove-orphans", timeout=120)
        compose.must("up", "-d", "--build", timeout=900)

        wait_n8n(timeout_s=240)
        log("n8n is ready")

        browser = Browser()
        browser.call("setup", timeout=180)
        log("n8n owner is ready")

        compose.must(
            "exec",
            "-T",
            "n8n",
            "n8n",
            "import:workflow",
            "--input=/workflows/enquiry-triage.json",
            timeout=180,
        )
        compose.must(
            "exec",
            "-T",
            "n8n",
            "n8n",
            "update:workflow",
            f"--id={WORKFLOW_ID}",
            "--active=true",
            timeout=180,
        )
        # n8n only loads an active workflow at process start. Stop and start
        # (rather than restart) so the new version is definitely the one that
        # registers the production webhook, then give registration a moment.
        compose.must("stop", "n8n", timeout=120)
        compose.must("start", "n8n", timeout=120)
        wait_n8n(timeout_s=240)
        time.sleep(15.0)
        log("workflow imported and activated")

        records = load_records(args.records)
        answers: list[dict[str, Any]] = []
        for record in records:
            post_enquiry(record)
            answer = wait_pending(record["id"])
            assert_no_gold(str(answer.get("redacted_text", "")), record, record["id"])
            answers.append(answer)
            log(f"enquiry {record['id']} reached the review step")

        plan = approval_plan(answers)
        with tempfile.NamedTemporaryFile(
            "w", suffix=".json", prefix="aipf-e2e-plan-", delete=False, encoding="utf-8"
        ) as handle:
            json.dump(plan, handle)
            plan_path = Path(handle.name)
        try:
            browser.call("approve", str(plan_path), timeout=900)
        finally:
            plan_path.unlink(missing_ok=True)
        log("all review forms were submitted")

        outputs = wait_outputs(args.records, timeout_s=180)
        gate_log = compose.must("logs", "--no-color", "gate", timeout=120)
        assert_log_clean(gate_log, records)
        for record, answer, item in zip(records, answers, plan, strict=True):
            path = outputs.get(record["id"])
            if path is None:
                raise E2EError(f"{record['id']}: no output file")
            try:
                result = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise E2EError(f"{record['id']}: output file is not valid JSON") from exc
            validate_output(result, record, answer, str(item["decision"]), gate_log)
        log(f"{len(records)} output files validated")

        if not args.no_screenshot:
            screenshot = SCREENSHOT
            screenshot.parent.mkdir(parents=True, exist_ok=True)
            browser.call("screenshot", WORKFLOW_ID, str(screenshot), timeout=300)
            log(f"canvas screenshot written ({screenshot.stat().st_size} bytes)")

        log("PASS")
        succeeded = True
        return 0
    except E2EError as exc:
        log(f"FAIL: {exc}")
        if compose is not None:
            logs = compose.call("logs", "--no-color", "--tail=60", capture=True)
            if logs.stdout:
                sys.stderr.write(logs.stdout)
        return 1
    finally:
        if compose is not None:
            if not args.keep:
                compose.call("down", "--remove-orphans", capture=True, timeout=180)
            compose.close()
        if locked:
            release_lock(lock_path)
        if succeeded and not args.keep:
            remove_n8n_data(required=False)
            log("scratch .n8n-data removed")


def static_default_lock() -> str:
    """The shared live-LLM lock, kept outside the repository."""
    root = os.environ.get("AIPF_LOCK_DIR")
    base = (
        Path(root).expanduser() if root else Path.home() / ".local" / "state" / "ai-privacy-filter"
    )
    return str(base / "aipf-lock-live-llm")


if __name__ == "__main__":
    raise SystemExit(main())

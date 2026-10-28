"""CLI tests: redact, process, eval and serve against the stand-ins."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from pytest import CaptureFixture, MonkeyPatch
from support import ENTITIES, TEXT, Harness, first_occurrence_only

from ai_privacy_filter import cli
from ai_privacy_filter.models import (
    DetectionResults,
    PageMeta,
    PageResults,
    Span,
    TriageResults,
)


def _run(
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
    argv: list[str],
    harness: Harness | None = None,
) -> tuple[int, str, str]:
    if harness is not None:
        monkeypatch.setattr(cli, "default_pipeline", lambda: harness.pipeline)
    code = cli.main(argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_redact_prints_the_pseudonymised_text_only(
    monkeypatch: MonkeyPatch, capsys: CaptureFixture[str]
) -> None:
    code, out, err = _run(monkeypatch, capsys, ["redact", TEXT], Harness.build())
    assert code == cli.EXIT_OK
    assert "[PERSON_1]" in out
    assert "[EMAIL_1]" in out
    for needle, _ in ENTITIES:
        assert needle not in out
        assert needle not in err
    assert "3 span(s)" in err
    assert "PERSON=1" in err


def test_redact_show_mapping_prints_originals_on_stderr(
    monkeypatch: MonkeyPatch, capsys: CaptureFixture[str]
) -> None:
    code, out, err = _run(monkeypatch, capsys, ["redact", "--show-mapping", TEXT], Harness.build())
    assert code == cli.EXIT_OK
    assert "Erika Mustermann" not in out
    assert "[PERSON_1] -> Erika Mustermann" in err


def test_redact_reads_standard_input(monkeypatch: MonkeyPatch, capsys: CaptureFixture[str]) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(TEXT))
    code, out, _ = _run(monkeypatch, capsys, ["redact"], Harness.build())
    assert code == cli.EXIT_OK
    assert "[PERSON_1]" in out


def test_redact_rejects_empty_text(monkeypatch: MonkeyPatch, capsys: CaptureFixture[str]) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("   \n"))
    code, out, err = _run(monkeypatch, capsys, ["redact"], Harness.build())
    assert code == cli.EXIT_REFUSED
    assert out == ""
    assert "empty text" in err


def test_process_prints_a_json_result(
    monkeypatch: MonkeyPatch, capsys: CaptureFixture[str]
) -> None:
    code, out, _ = _run(
        monkeypatch, capsys, ["process", TEXT, "--backend", "fake"], Harness.build()
    )
    assert code == cli.EXIT_OK
    payload = json.loads(out)
    assert payload["triage"]["backend"] == "fake"
    assert payload["spans_summary"] == {"PERSON": 1, "EMAIL": 1, "PHONE": 1}
    assert "[PERSON_1]" in payload["redacted_text"]
    assert "[PERSON_1]" in payload["triage"]["reply_draft"]
    for needle, _ in ENTITIES:
        assert needle not in out


def test_process_restore_puts_the_value_back(
    monkeypatch: MonkeyPatch, capsys: CaptureFixture[str]
) -> None:
    code, out, _ = _run(monkeypatch, capsys, ["process", TEXT, "--restore"], Harness.build())
    assert code == cli.EXIT_OK
    payload = json.loads(out)
    assert "Erika Mustermann" in payload["triage"]["reply_draft"]


def test_process_rejects_an_unknown_backend(
    monkeypatch: MonkeyPatch, capsys: CaptureFixture[str]
) -> None:
    code, _, err = _run(
        monkeypatch, capsys, ["process", TEXT, "--backend", "nope"], Harness.build()
    )
    assert code == cli.EXIT_REFUSED
    assert "unknown backend" in err


def test_process_refuses_when_the_guard_blocks(
    monkeypatch: MonkeyPatch, capsys: CaptureFixture[str]
) -> None:
    harness = Harness.build(first_occurrence_only)
    text = "Erika Mustermann schreibt. Erneut: Erika Mustermann."
    code, out, err = _run(monkeypatch, capsys, ["process", text], harness)
    assert code == cli.EXIT_REFUSED
    assert out == ""
    assert "outbound guard" in err
    assert "PERSON" in err
    assert "Erika" not in err
    assert harness.classifier.calls == []


def test_stub_pipeline_exits_with_an_error(
    monkeypatch: MonkeyPatch, capsys: CaptureFixture[str]
) -> None:
    def not_ready(text: str) -> list[Span]:
        raise NotImplementedError("stub")

    code, out, err = _run(monkeypatch, capsys, ["redact", TEXT], Harness.build(not_ready))
    assert code == cli.EXIT_ERROR
    assert out == ""
    assert "detection" in err


def test_eval_reports_missing_results(
    monkeypatch: MonkeyPatch, capsys: CaptureFixture[str], tmp_path: Path
) -> None:
    missing = tmp_path / "results.json"
    code, _, err = _run(monkeypatch, capsys, ["eval", "--results", str(missing)])
    assert code == cli.EXIT_ERROR
    assert "no stored results" in err


def test_eval_prints_stored_results(
    monkeypatch: MonkeyPatch, capsys: CaptureFixture[str], tmp_path: Path
) -> None:
    from datetime import UTC, datetime

    results = PageResults(
        detection=DetectionResults(germeval={"f1": 0.5}, synthetic={"f1": 0.6}),
        triage=TriageResults(per_backend={"fake": {"accuracy": 0.4}}),
        meta=PageMeta(
            version="0.1.0",
            commit="0" * 40,
            generated_at=datetime(2026, 10, 27, tzinfo=UTC),
            sha256="0" * 64,
            mock=True,
        ),
    )
    path = tmp_path / "results.json"
    path.write_text(results.model_dump_json(), encoding="utf-8")
    code, out, _ = _run(monkeypatch, capsys, ["eval", "--results", str(path)])
    assert code == cli.EXIT_OK
    payload = json.loads(out)
    assert payload["detection"]["germeval"] == {"f1": 0.5}
    assert payload["meta"]["mock"] is True


def test_eval_rejects_an_invalid_results_file(
    monkeypatch: MonkeyPatch, capsys: CaptureFixture[str], tmp_path: Path
) -> None:
    path = tmp_path / "results.json"
    path.write_text("{}", encoding="utf-8")
    code, _, err = _run(monkeypatch, capsys, ["eval", "--results", str(path)])
    assert code == cli.EXIT_ERROR
    assert "not a valid results file" in err


def test_serve_binds_loopback_by_default(
    monkeypatch: MonkeyPatch, capsys: CaptureFixture[str]
) -> None:
    calls: list[dict[str, object]] = []

    def fake_run(*args: object, **kwargs: object) -> None:
        calls.append({"args": args, **kwargs})

    monkeypatch.delenv("AIPF_BIND", raising=False)
    monkeypatch.setattr(cli.uvicorn, "run", fake_run)
    code, _, err = _run(monkeypatch, capsys, ["serve"])
    assert code == cli.EXIT_OK
    assert calls[0]["host"] == "127.0.0.1"
    assert calls[0]["port"] == 8765
    assert "http://127.0.0.1:8765" in err


def test_serve_honours_aipf_bind(monkeypatch: MonkeyPatch, capsys: CaptureFixture[str]) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setenv("AIPF_BIND", "0.0.0.0:9000")
    monkeypatch.setattr(cli.uvicorn, "run", lambda *a, **k: calls.append(k))
    code, _, _ = _run(monkeypatch, capsys, ["serve"])
    assert code == cli.EXIT_OK
    assert calls[0]["host"] == "0.0.0.0"
    assert calls[0]["port"] == 9000


def test_serve_flags_override_the_environment(
    monkeypatch: MonkeyPatch, capsys: CaptureFixture[str]
) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setenv("AIPF_BIND", "0.0.0.0:9000")
    monkeypatch.setattr(cli.uvicorn, "run", lambda *a, **k: calls.append(k))
    code, _, _ = _run(monkeypatch, capsys, ["serve", "--host", "127.0.0.1", "--port", "8123"])
    assert code == cli.EXIT_OK
    assert calls[0]["host"] == "127.0.0.1"
    assert calls[0]["port"] == 8123


def test_serve_rejects_a_bad_aipf_bind(
    monkeypatch: MonkeyPatch, capsys: CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AIPF_BIND", "nonsense")
    code, _, err = _run(monkeypatch, capsys, ["serve"])
    assert code == cli.EXIT_REFUSED
    assert "AIPF_BIND" in err


def test_serve_reload_uses_the_app_factory(
    monkeypatch: MonkeyPatch, capsys: CaptureFixture[str]
) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.delenv("AIPF_BIND", raising=False)
    monkeypatch.setattr(cli.uvicorn, "run", lambda *a, **k: calls.append({"args": a, **k}))
    code, _, _ = _run(monkeypatch, capsys, ["serve", "--reload"])
    assert code == cli.EXIT_OK
    assert calls[0]["args"] == ("ai_privacy_filter.api:create_app",)
    assert calls[0]["factory"] is True
    assert calls[0]["reload"] is True


def test_parser_requires_a_command() -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli.main([])
    assert excinfo.value.code == 2


def test_parser_lists_every_command() -> None:
    parser = cli.build_parser()
    for command in ("redact", "process", "eval", "serve"):
        assert command in parser.format_help()

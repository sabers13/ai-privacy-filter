# Changelog

All notable changes to this project are documented here.

## [0.1.0] - 2026-10-27

### Added

- T01 foundation: `src`-layout package for Python 3.12 with runtime and dev
  dependency groups.
- Contract models in `src/ai_privacy_filter/models.py`: `Span`, `EntityType`,
  `RedactionResult`, `RestoreResult`, `GuardVerdict`, `TriageResult`,
  `BackendResult`, `Enquiry` and the page data models, documented in
  `docs/contracts.md`.
- Deterministic fake LLM backend keyed by the prompt hash, plus a backend
  registry and typed stubs for the backends that land in T06.
- `scripts/scan_forbidden.py`: built-in privacy and secret checks plus patterns
  from `AIPF_FORBIDDEN_FILE`; prints `file:line` and never the matched value.
- `Makefile` targets for setup, test, lint, the stub pipeline targets, release
  checks, gitleaks and the forbidden-string scan.
- CI workflow for ruff, mypy and pytest without live markers.
- Apache-2.0 licence, `NOTICE`, `THIRD_PARTY.md` and this changelog.

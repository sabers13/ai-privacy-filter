# Changelog

All notable changes to this project are documented here.

## [Unreleased]

### Added

- T03 pseudonymisation: `pseudonymise.redact` replaces `Span` values with
  `[TYPE_n]` placeholders numbered per type in order of first appearance, reuses
  one placeholder for values equal after case and whitespace normalisation, and
  keeps the mapping in memory only.
- T03 restoration: `pseudonymise.restore` puts values back even when the model
  re-cased or re-spaced a placeholder, and reports unknown or invented
  placeholders instead of dropping them.
- T03 guard: `guard.guard` blocks the outbound text when a normalised original
  value occurs, including digit-only forms of phone numbers and IBANs, and
  reports entity types only. `guard.require_clean` raises `GuardBlockedError`
  before any model call.
- `tests/fixtures/synthetic_texts.json`: 50 synthetic German enquiry texts with
  character-exact spans for round-trip and guard tests.
- `tests/test_local_only.py` and `make test-local`: the local filter path runs
  with sockets disabled, as evidence for the "runs entirely on your machine"
  claim. The detector leg is skipped until T02 lands.
- CI runs the no-network module with `--disable-socket` as a separate step.
- T05 GermEval 2014 loader: `data/get_germeval.py` downloads the CC BY 4.0
  train/dev/test TSVs into git-ignored `data/external/germeval/`, verifies pinned
  SHA-256 and byte sizes, and parses sentences with outer-level spans.
  `data/SOURCES.md` records URLs, retrieval date, licence and checksums.
- GermEval label mapping in `src/ai_privacy_filter/datasets/germeval.py`: `PER`
  to `PERSON` and `ORG` to `ORG` for the primary metric, `LOC` to `LOCATION`
  reported separately, `OTH` to `OTHER`, and `deriv`/`part` variants as separate
  unscored classes.
- `.gitleaks.toml` keeps the git-ignored GermEval text out of the generic API-key
  rule, because its citation URLs contain `key=` query strings; every tracked
  file is still scanned.

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
- T04 synthetic corpus: a seeded Faker `de_DE` generator in
  `data/make_synthetic.py` and 240 stratified records (dev 80, test 160) in
  `data/synthetic/`, with character-exact `gold_spans` plus `gold_category` and
  `gold_urgency` for detection and triage evaluation.
- CI workflow for ruff, mypy and pytest without live markers.
- Apache-2.0 licence, `NOTICE`, `THIRD_PARTY.md` and this changelog.

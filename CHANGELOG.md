# Changelog

All notable changes to this project are documented here.

## [Unreleased]

### Added

- T08 HTTP service: `create_app` serves `GET /health`, `POST /redact`,
  `POST /restore` and `POST /process` over the frozen contracts. `/redact`
  returns a short-lived ticket instead of the placeholder mapping, `/restore`
  puts values back from that ticket and answers 410 once it expired, and
  `/process` returns the `TriageResult` next to the exact `redacted_text` that
  was sent to the model. A guard failure answers 422 with entity types only and
  makes no model call.
- T08 ticket store: `tickets.TicketStore` keeps placeholder mappings in process
  memory for 15 minutes, is bounded and thread-safe, and never writes, logs or
  persists a value.
- T08 CLI: `aipf redact|process|eval|serve`. `redact` prints the pseudonymised
  text and a span count, `process` prints the triage result as JSON, `eval`
  summarises a stored results file, and `serve` binds `127.0.0.1:8765` unless
  `AIPF_BIND=host:port` overrides it. The placeholder mapping is printed only
  with `--show-mapping`.
- T08 privacy guards in the service: logs carry lengths, entity counts and
  backend names only, validation errors do not echo the request body, and CORS
  is not enabled.
- T02 detection core: Presidio `AnalyzerEngine` configured with the German
  `de_core_news_lg` model and language `de`, plus `detect(text, profile=...)`
  returning merged, non-overlapping `Span` values with a `strict` and a
  `balanced` profile.
- German recognizers in `src/ai_privacy_filter/recognizers/`: phone (international,
  national, spaced, slashed), IBAN with mod-97 checksum, postcode with city,
  street with house number, VAT ID, date of birth (context only), person names
  from salutations, titles, signatures and form labels, a first-name gazetteer
  from Faker `de_DE`, and organisation names anchored on German legal forms.
  Email and URL use Presidio's patterns; email validation is local so detection
  never fetches the public-suffix list.
- Unit tests per recognizer with positive and negative cases, hypothesis property
  tests that every span offset slices to `span.text`, merge determinism tests and
  an offline test that blocks sockets while detecting.
- T10 evaluation protocol: `METHOD.md` fixes the data splits, the dev-only
  tuning rule, the B0/B1/B2 baselines, the gold-span matching and containment
  rules, Wilson 95 % intervals, leak and over-masking rates, restore exactness,
  the fixed 40-enquiry triage subset with its ids, median latency and the
  `METHOD.md` hash rerun workflow.
- `scripts/method_guard.py`: refuses a test-split run when a stage reruns
  without `--rerun-reason`, when `METHOD.md` changed without
  `--method-approval`, or when a prior claim or run record is malformed. It
  writes a durable claim before any test record is read.
- `tests/test_method.py` pins the corpus hashes, the documented gold counts and
  the fixed subset ids to the committed data; `tests/test_method_guard.py`
  covers the allow/refuse paths and exit codes.
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
  claim. The detector leg exercises `detect.detect` under the same blocked
  sockets.
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
- T06 LLM backends ported from SaberOps (Apache-2.0): `claude-cli` and
  `codex-cli` run the native CLIs with every tool disabled inside a fresh empty
  temporary directory, on a stdin-fed prompt and a hermetic environment;
  `byok` speaks OpenAI-compatible chat completions for OpenCode Go and a local
  Ollama endpoint. Billing pins, native result parsing, quota/rate-limit
  classification and the exact argv are documented in `docs/backends.md`.
- Backend tests with recorded synthetic fixtures: exact argv, isolation cleanup,
  schema validation, error kinds and the quota rules. One `live` canary per
  backend, run once under the shared live-LLM lock.
- T11 detection evaluation: `eval/baselines.py` is the single source of the B0
  regex rules (also written to `web/tryit-rules.json`), `eval/detection.py` runs
  B0, B1 and B2 once on synthetic test and GermEval 2014 test behind the
  `scripts/method_guard.py` access claim, and `eval/scoring.py` applies the
  METHOD.md containment, compatibility and precision rules to every baseline.
  Wilson intervals and an enquiry-cluster bootstrap are in `eval/stats.py`.
- T11 result files: `results/detection_synthetic.json`,
  `results/detection_germeval.json`, `results/leak_rate.json`,
  `results/restore.json`, `results/SUMMARY.md` and `results/SHA256SUMS`, plus the
  run record, per-enquiry scoring (no text) and the append-only
  `docs/eval-reruns.md` row. `results/METHOD_HASH` pins the protocol.
- Dev-only B2 tuning record in `results/tuning/`; the frozen profile is in
  `eval/configs.json` with its hash in every result file. No test number was
  used to choose it.
- `make reproduce` recomputes every detection result from the committed corpus
  and the checksum-verified GermEval download with the network blocked, then
  compares the files byte for byte. `tests/test_eval_results.py` parses
  `SUMMARY.md` back and checks every number against the JSON, verifies the
  hashes and asserts that no GermEval sentence or token run appears in
  `results/`.

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
- `ops/ollama-remote/`: host-agnostic scripts for an optional Ollama runtime on
  a separate machine (pinned install with checksum verification, model pull,
  loopback SSH tunnel, schema-checked smoke request, guarded teardown), with one
  measured run recorded in `results/ollama_server.json`.
- Apache-2.0 licence, `NOTICE`, `THIRD_PARTY.md` and this changelog.

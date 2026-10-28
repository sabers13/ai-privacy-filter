# Changelog

All notable changes to this project are documented here.

## [1.0.0] - 2026-10-27

First public release. The filter runs entirely on your machine: it detects
personal data in German customer enquiries, replaces it with typed
placeholders, guards the outbound text, asks an AI backend for a category,
urgency and a draft reply, and restores the values for human review. Every
headline number, its 95% interval and the protocol behind it are in
`results/` and `METHOD.md`.

### What the release contains

- **Detection.** A Presidio `AnalyzerEngine` with the German
  `de_core_news_lg` model and language `de`, plus German recognizers for
  phone numbers (international, national, spaced, slashed), IBAN with the
  mod-97 checksum, postcode with city, street with house number, VAT ID, date
  of birth (context only), person names from salutations, titles, signatures
  and form labels, a first-name gazetteer, and organisation names anchored on
  German legal forms. Email and URL use Presidio's patterns; email validation
  is local, so detection never fetches the public-suffix list. `detect()`
  returns merged, non-overlapping spans with a `strict` and a `balanced`
  profile.
- **Pseudonymisation.** `pseudonymise.redact` replaces span values with
  `[TYPE_n]` placeholders numbered per type in order of first appearance,
  reuses one placeholder for values equal after case and whitespace
  normalisation, and keeps the mapping in memory only. `restore` puts values
  back even when the model re-cased or re-spaced a placeholder, and reports
  unknown or invented placeholders instead of dropping them.
- **Outbound guard.** `guard.guard` blocks text that still contains a
  normalised original value, including digit-only forms of phone numbers and
  IBANs, and reports entity types only. `guard.require_clean` raises before
  any model call.
- **Triage pipeline.** The full detect → pseudonymise → guard → backend →
  restore path over the fixed 40-enquiry subset defined in `METHOD.md`, with
  the outbound prompt audited against gold values before the call and the
  reply audited again before storage. A leaking prompt or reply is never
  written to disk; the record keeps the error kind, latency and leaked types
  only.
- **HTTP service and CLI.** `create_app` serves `GET /health`, `POST /redact`,
  `POST /restore` and `POST /process`. `/redact` returns a short-lived ticket
  instead of the placeholder mapping, `/restore` puts values back from that
  ticket and answers 410 once it expired, and `/process` returns the
  `TriageResult` next to the exact `redacted_text` that was sent to the
  model. A guard failure answers 422 with entity types only and makes no
  model call. The ticket store is in-process, 15-minute, bounded and
  thread-safe, and never writes, logs or persists a value. Logs carry lengths,
  entity counts and backend names only; request bodies are never echoed and
  CORS is not enabled. `aipf redact|process|eval|serve` covers the same work
  from the command line and binds `127.0.0.1:8765` unless `AIPF_BIND`
  overrides it.
- **Model backends.** `claude-cli` and `codex-cli` run the native CLIs with
  every tool disabled inside a fresh empty temporary directory, on a
  stdin-fed prompt and a hermetic environment; `byok` speaks
  OpenAI-compatible chat completions for a hosted service or a local Ollama
  endpoint. Billing pins, native result parsing, quota and rate-limit
  classification, the exact argv and the isolation model are documented in
  `docs/backends.md`. A deterministic fake backend makes the whole pipeline
  testable without a model.
- **Evaluation.** A seeded German synthetic corpus, the CC BY 4.0 GermEval
  2014 NER data (downloaded, checksum-verified and never redistributed), the
  B0/B1/B2 baselines, gold-span matching and containment rules, Wilson 95%
  intervals, an enquiry-cluster bootstrap, leak and over-masking rates and
  restore exactness. A method guard refuses an unexplained test-split rerun
  or a protocol change without an approval reference, and
  `make reproduce` rebuilds every result from the committed corpus and stored
  responses with no network and no model call.
- **Human review in n8n.** An exported workflow posts an enquiry to the gate,
  branches on the guard verdict, shows a review form with the pseudonymised
  text, category, urgency and restored draft, and writes the
  approve/edit/reject result. A compose stack runs the gate and a pinned n8n
  image on a private network with only n8n published on loopback, and
  `make e2e` proves the full path on synthetic enquiries.
- **Optional Ollama on a separate host.** Host-agnostic scripts install a
  pinned Ollama, pull a model, open a loopback-only SSH tunnel, send a
  schema-checked smoke request and tear the runtime down again. Measured
  runs, including the weaker local-model quality, are recorded in `results/`.
- **Release evidence and checks.** A plain-language `README.md` with the
  problem, the pipeline, measured results and their intervals, the backend
  and data-handling table, honest limits and credits; `project-facts.yml` as
  the machine-readable summary; `results/SHA256SUMS` over the result files;
  `results/RELEASE_CHECK.md` with the release gates and their outputs;
  `scripts/scan_forbidden.py` for privacy and secret patterns; gitleaks for
  the working tree and history; an 85% branch-coverage floor on `src/`; and
  `make clean-clone` to prove setup, tests and reproduction from a fresh
  clone.
- Apache-2.0 licence, `NOTICE`, the third-party inventory and this changelog.

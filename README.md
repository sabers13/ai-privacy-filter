# AI Privacy Filter

The filter pseudonymises personal data in customer enquiries on your machine
before the text reaches an AI model. The model drafts a reply with placeholders
in it; the filter puts the details back for a human to review.

**Status: foundation (T01), detection core (T02), pseudonymisation (T03),
synthetic corpus (T04), GermEval loader (T05), real LLM backends (T06), optional
Ollama runtime (T07) and the HTTP service with the CLI (T08).** The package
layout, the pydantic contracts, detection, placeholder mapping, restoration, the
outbound guard, the deterministic fake backend, the dataset loaders, the four LLM
backends, the FastAPI service, the `aipf` command line, CI and the local checks
exist. The triage prompt (T06/T12) is still a typed stub that raises
`NotImplementedError` until its task lands, so `/process` answers 501 with the
owning task while `/health`, `/redact` and `/restore` work today. Detection
numbers have not been measured yet, so nothing here has been evaluated yet.

## How it is meant to work

1. **Detect** personal data locally (Presidio, a German spaCy model and German
   pattern recognizers).
2. **Pseudonymise** each value into a placeholder such as `[PERSON_1]`, consistent
   within one message.
3. **Guard** the outbound text: if an original value is still present, no LLM call
   happens.
4. **Ask the model** for a category, an urgency and a draft reply.
5. **Restore** the placeholders in the draft so a person can review it.

The filtering step runs entirely on your machine (steps 1–3 and 5). The only
outbound network call is the LLM request, and it carries placeholders instead of
personal data.

## Quick start

Requires Python 3.12 or newer and [uv](https://docs.astral.sh/uv/).

```sh
make setup   # install dependencies and the pinned German spaCy model
make test    # unit and contract tests, no network and no live model calls
make test-local  # run the local filter path with sockets disabled (no-network proof)
make lint    # ruff and mypy --strict on src/
```

The GermEval 2014 evaluation data is optional. Fetch and verify it with
`uv run python data/get_germeval.py`; it stays under the git-ignored
`data/external/` directory and never enters the repository.

Re-run `make setup` after changing dependencies: `uv sync` owns the virtual
environment and installs the separately pinned model again.

`make reproduce` rebuilds the committed detection results from the corpus and the
verified GermEval download, with the network blocked during scoring, and compares
them byte for byte. The remaining targets (`live`, `e2e`, `page-data`) are honest
stubs until their tasks land; they exit non-zero with a message.

## HTTP service

```sh
uv run aipf serve                  # http://127.0.0.1:8765
AIPF_BIND=0.0.0.0:8765 uv run aipf serve   # reachable from a container
```

| endpoint | body | answer |
|---|---|---|
| `GET /health` | – | `{status, version}` |
| `POST /redact` | `{text}` | `{redacted_text, spans_summary, ticket, expires_in_s}` |
| `POST /restore` | `{ticket, text}` | `{text, unknown_placeholders}`; 410 when the ticket is unknown or expired |
| `POST /process` | `{text, backend?}` | `{triage, redacted_text, spans_summary}`; 422 with entity types only when the guard blocks, and then no model call happens |

The ticket stands in for the placeholder mapping, which stays in this process
for 15 minutes and is never logged, persisted or returned. `redacted_text` in the
`/process` answer is exactly what was sent to the model, so a reviewer can see
what left the machine. CORS is off and the default bind is loopback only.

The CLI runs the same pipeline locally:

```sh
uv run aipf redact "Guten Tag, mein Name ist Erika Mustermann."   # pseudonymised text on stdout
uv run aipf redact --show-mapping "..."                           # mapping on stderr (personal data)
uv run aipf process "..." --backend fake --restore                # triage result as JSON
uv run aipf eval --results results/results.json                   # summarise stored results
```

`aipf process` prints the reply draft with placeholders unless `--restore` asks
for the original values back. Exit codes: 0 success, 1 runtime failure, 2 refused
(guard block, unknown backend, empty text).

## What this repository does not claim

- This is pseudonymisation: personal data is replaced with placeholders, and the
  mapping stays in memory. It does not make text anonymous, and it is not legal
  advice.
- Detection rates have not been measured yet. Numbers will come from GermEval 2014
  and synthetic enquiries only.
- No real customer data, real people's data or private infrastructure details are
  part of this repository. Examples use synthetic text.

## Layout

```text
src/ai_privacy_filter/   package source (contracts, detection, filter, service, CLI, backends)
src/ai_privacy_filter/recognizers/  German pattern and gazetteer recognizers
data/                    dataset loaders and source provenance; data/external/ is git-ignored
docs/contracts.md        contract reference
docs/backends.md         backend isolation, exact CLI argv and error kinds
docs/task-notes/         per-task decisions and verification notes
ops/ollama-remote/       optional Ollama runtime on a separate host (T07)
data/SOURCES.md          model and runtime sources with licences
results/                 measured results committed by task
scripts/                 forbidden-string and secret scans
tests/                   contract, service, CLI and scan tests
```

## Licence

Apache-2.0. See `LICENSE`, `NOTICE` and `THIRD_PARTY.md`.

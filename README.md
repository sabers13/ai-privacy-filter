# AI Privacy Filter

The filter pseudonymises personal data in customer enquiries on your machine
before the text reaches an AI model. The model drafts a reply with placeholders
in it; the filter puts the details back for a human to review.

**Status: foundation (T01) and pseudonymisation (T03).** The package layout, the
pydantic contracts, the deterministic fake backend, CI and the local checks exist.
Placeholder mapping, restoration and the outbound guard are implemented.
Detection, the HTTP service, the CLI and the real backends are typed stubs that
raise `NotImplementedError` until their tasks land. Nothing here has been
evaluated yet.

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

Other targets (`reproduce`, `live`, `e2e`, `page-data`) are honest stubs until
their tasks land; they exit non-zero with a message.

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
src/ai_privacy_filter/   package source (contracts, stubs, fake backend)
data/                    dataset loader and source provenance; data/external/ is git-ignored
docs/contracts.md        contract reference
docs/task-notes/         per-task decisions and verification notes
scripts/                 forbidden-string and secret scans
tests/                   contract, stub and scan tests
```

## Licence

Apache-2.0. See `LICENSE`, `NOTICE` and `THIRD_PARTY.md`.

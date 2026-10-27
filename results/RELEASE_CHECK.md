# Release check - v1.0.0

Local-only release. No remote, no push, no pull request and no deployment.

- Date: 2026-10-27
- Project branch: `fm/aipf-t15`, based on `main` `898bdc2`
- Release tag: `v1.0.0` (annotated, local only)
- Page repository: `sabers13.github.io`, local `main`, unpublished
- Environment: Linux x86_64, Python 3.12.3, uv, gitleaks 8.30.1

The six gates below are the release contract. Each command was run from a
clean worktree at the head recorded by the tag, except where a scope is stated.

| # | gate | status |
|---|---|---|
| 1 | clean clone reproduces `results/SHA256SUMS` | PASS |
| 2 | gitleaks over both repositories' full history | PASS |
| 3 | forbidden scan, project and page | PASS for the project and the current page; one documented historical false positive |
| 4 | no tracked GermEval file, no real-person example | PASS |
| 5 | lint, tests and 85% coverage | PASS (87.75%) |
| 6 | licence files and page credits | PASS |

## Gate 1 - clean clone reproduces `results/SHA256SUMS`

Command:

```sh
scripts/clean_clone_check.sh
# equivalent: make clean-clone
```

The script clones the local repository to a temporary directory with
`git clone --no-local`, checks out the requested revision, removes every
`AIPF_*` variable from the environment (the private environment file is never
sourced), then runs:

```sh
make setup
make test
make reproduce
```

It verifies every hash in `results/SHA256SUMS`, requires
`git diff --exit-code -- results` to be empty after reproduction, and compares
the clone's `results/SHA256SUMS` with the committed file.

Result: **PASS** in 159 s. The fresh clone installed the pinned dependencies
and the 541.5 MiB German spaCy model, ran the test suite (652 passed, 1 skipped:
the optional GermEval test before reproduction downloads the data), downloaded
and checksum-verified GermEval for reproduction, reproduced all five detection
result files and the triage metrics/report, and left `results/SHA256SUMS`
identical with no tracked result changed.

## Gate 2 - gitleaks over both repositories' full history

Commands:

```sh
make secrets
# project: gitleaks dir . + gitleaks git .
git -C <page-repo> gitleaks dir .
git -C <page-repo> gitleaks git .
```

Result: **PASS**. Project: `no leaks found` for the working tree and all 26
commits. Page repository: `no leaks found` for the working tree and all 31
commits. gitleaks 8.30.1, redacted output, project `.gitleaks.toml`.

## Gate 3 - forbidden scan on both repositories (one documented page-history exception)

Commands:

```sh
# project repository, every local ref
uv run python scripts/scan_forbidden.py --root . --all-refs

# page repository, the AI Privacy Filter page subtree only
uv run python scripts/scan_forbidden.py --root . --path ai-privacy-filter --all-refs
```

Project result: **PASS**, `scan_forbidden: clean`.

Page result: **PASS for the current page tree; one documented historical
false positive.** The page worktree is clean. The page subtree's full history
contains one hit in a superseded commit: the Faker `de_DE` first-name export at
`ai-privacy-filter/assets/first-names-de.js:3` once contained a common German
first name that collides with the private forbidden list. The value is not
reproduced here. Commit `0797d25` removed that entry, and the current file and
current page contain no hit. The name is a dictionary entry in a generated
public first-name list, not a private identifier or a project value. No history
rewrite was performed, because the site repository is a shared local repository
and rewriting it is outside this task's authority. This is the only
page-subtree history hit.

The whole page repository also contains three unrelated public-contact hits in
the portfolio owner's own `index.html` and in an old `library-management.html`
demo. They are outside `ai-privacy-filter/`, predate this project and were not
edited; the scoped scan above is the project's release scope.

The scanner crash on non-UTF-8 history is fixed in this release:
`scan_history()` now captures bytes and decodes with `errors="replace"`. The
regression test commits a non-UTF-8 blob and a real leak in the same history.

## Gate 4 - no GermEval file tracked, no real-person example

Commands and checks:

```sh
git ls-files data/external        # must print nothing
python3 -c "import json; ..."     # page example ids, mock flags and backends
uv run pytest tests/test_method.py tests/test_eval_results.py -q
```

Results: **PASS**.

- `git ls-files data/external` is empty. GermEval stays under the git-ignored
  `data/external/` directory, verified by SHA-256 before use.
- `results/page/examples.json` has 15 entries, all `syn-test-*`, all
  `mock: false`; 13 have a restored draft and 2 are marked `guard-blocked`.
  They are synthetic Faker `de_DE` and hand-written templates, not real people.
- `results/` contains no GermEval sentence or token run; `tests/test_eval_results.py`
  enforces that, together with the committed corpus hashes.
- No employer, customer, private hostname, private IP, home path or key appears
  in either repository or in the page (gates 2 and 3).

## Gate 5 - lint, tests and coverage

Commands:

```sh
make lint test
make coverage
```

Result: **PASS**.

- `ruff check`: clean.
- `ruff format --check`: 126 files already formatted.
- `mypy --strict src`: no issues in 41 source files.
- `pytest -m "not live and not e2e"`: 653 passed, 3 deselected (the live and
  e2e markers); the clean clone runs 652 passed and 1 optional GermEval skip
  before reproduction downloads the data.
- Coverage: **87.75%** branch coverage on `ai_privacy_filter`, above the 85%
  floor. `make coverage` and CI both fail below 85%.

## Gate 6 - licence files and page credits

Checks:

```sh
ls LICENSE NOTICE THIRD_PARTY.md
python3 -c "..."                  # compare THIRD_PARTY.md with pyproject/uv.lock
# browser: read #credits text from the page, EN and DE
```

Result: **PASS**.

- `LICENSE` is the full Apache License 2.0 text.
- `NOTICE` names the project, the Apache-2.0 grant and the SaberOps attribution.
- `THIRD_PARTY.md` inventories every runtime and development dependency, the
  spaCy model, GermEval 2014, n8n, Ollama, Qwen2.5 1.5B Instruct, SaberOps and
  the optional Inter typeface, with the correct licence for each. The
  hypothesis entry was corrected to MPL-2.0.
- The page credits section names every component and every licence in the
  inventory: MIT, BSD-3-Clause, Apache-2.0, MPL-2.0, CC BY 4.0,
  Sustainable Use License and SIL OFL 1.1. Verified in a browser at 375 px and
  1280 px, EN and DE, with no horizontal overflow and no console errors.
- The page says pseudonymises, keeps "not legal advice" and makes no
  compliance or anonymity claim.

## Release tag

```sh
git tag -a v1.0.0 -m "AI Privacy Filter v1.0.0"
```

The annotated tag is local only. It is not pushed. Use
`git rev-parse v1.0.0^{commit}` for the tagged commit.

## Honest exceptions

- The page repository is a shared local repository. Its history was not
  rewritten; the single page-subtree historical name collision above is
  reported rather than hidden.
- The page's GitHub link returns HTTP 404 until the captain publishes the
  repository. This is recorded in the page checks and is not a release failure.
- The optional GermEval test is skipped in the clean clone before `make
  reproduce` downloads the data; reproduction then verifies the full GermEval
  result file.
- Live model calls and the n8n end-to-end run need the private environment and
  are not part of the offline release gate. Their recorded runs are in
  `results/runs/` and `docs/n8n.md`.

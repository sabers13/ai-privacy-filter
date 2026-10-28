# Release check - v1.0.0

Local-only release. No remote, no push, no pull request and no deployment.

- Date: 2026-10-28
- Base: `main` at `950a761`
- Release tag: `v1.0.0` (annotated, local only; object `4028c83`)
- Page repository: `sabers13.github.io`, local `main` at `5ffb2b4`, 10
  commits ahead of `origin/main`, unpublished
- Environment: Linux x86_64, Python 3.12.3, uv, gitleaks 8.30.1

The six gates below are the release contract. Every command in gates 1, 3, 4
and 5, and both project commands in gate 2, was run on 2026-10-28 from a
worktree at `950a761` with a clean tree. The two history scans in gates 2 and 3
were run again at the final head of this branch, so their counts include the
commit that adds this file. The page half of gates 2 and 3 and the page checks
in gate 6 come from the site lane records named in each gate; the site matrix
was not re-run for this check.

| # | gate | status |
|---|---|---|
| 1 | clean clone reproduces `results/SHA256SUMS` | PASS (254 s) |
| 2 | gitleaks over both repositories' full history | PASS |
| 3 | forbidden scan: project tree and all local refs, page subtree, screenshot source text | PASS for the project and the current page; one documented historical page hit |
| 4 | no tracked GermEval file, no real-person example | PASS |
| 5 | lint, tests and 85% coverage | PASS (88.70%) |
| 6 | licence files and page credits | PASS |

## Final polish state

The last three changes of this release are covered by the gates below; this
section says what they are and where their evidence lives.

### Re-captured review screenshots (`950a761`)

The four n8n review-stack shots were captured again from a `make demo` stack so
that the human review step shows an actual edit: the reviewer chose *Edit*,
corrected the draft, and that corrected reply is what was stored. Capture is
scripted (`scripts/demo/capture_shots.py` plus `scripts/demo/capture_shots.cjs`)
against the three synthetic `syn-dev-0001..0003` enquiries; the reviewer edited
`0001` and approved `0002`/`0003`.

| shot | size | what it shows |
|---|---|---|
| `results/page/screenshots/n8n-execution-success.png` | 2248 x 574 | finished execution, zoom to fit, both branches green |
| `results/page/screenshots/n8n-executions-list.png` | 1140 x 348 | three `Success` rows, no account details |
| `results/page/screenshots/n8n-review-form.png` | 1280 x 1116 | decision `edit`, corrected reply in the field, before submit |
| `results/page/screenshots/n8n-result.png` | 1280 x 900 | result card with decision `edit` and the corrected reply |

The PNGs carry no text chunks, the four captured DOM text dumps they were
rendered from scan clean, and `results/page/MANIFEST.md` carries the new
hashes. `results/runs/` and `results/page/n8n-workflow.png` are unchanged, so
no number moved.

### Rebuilt and synced page bundle (`1c0f7d6`, site `5ffb2b4`)

`make page-data` now rebuilds the whole `results/page/` bundle from committed
results: 37 listed files (plus `MANIFEST.md`), 24 KPIs, five charts with CSV
twins, demos, try-it samples, the worked example, the three demo enquiries and
the four screenshots. Two runs over the same inputs are byte-identical, and a
source-equality test (`tests/test_page_bundle.py`) keeps the bundle tied to the
committed results.

The site lane copied it with `scripts/sync_page_bundle.py --site-root <site>`
from `950a761`: 22 files, of which the four screenshots and
`data/page-bundle-MANIFEST.md` changed and the rest were byte-identical;
`data/results.json` is site-owned and was not touched. The same commit adds the
review gallery captions EN/DE and the closing adoption line with the existing
`/#contact` anchor. Every one of the 37 manifest hashes verifies against the
file on disk.

### Chart rendering fix already on the site (site `ab26d36`)

The 95% CI is now one 1.6 px whisker line with two 10 px end caps drawn over a
solid bar; the pale halo that cut a segment out of the bar is gone, a measured
0% draws a true empty bar, and dark theme keeps a 0.6 px edge for legibility.
No chart JSON, CSV twin or number changed.

Site lane records (not re-run here):

| check | result | log |
|---|---|---|
| KPI and chart value assertion, `assert-numbers.cjs` | 1084 ok, 0 FAIL | `data/site-charts-fix/logs/assert-numbers.log` |
| 12-page matrix after the fix | 53 ok, 0 FAIL | `data/site-charts-fix/logs/checks.log`, `checks.json`, `screenshots/` |
| KPI and chart value assertion after the sync | 1084 ok, 0 FAIL | `data/site-final-sync/assert-numbers.log` |
| 12-page matrix, final site head | 126 ok, 0 FAIL | `data/site-final-sync/checks.log`, `checks.json`, `screenshots/` |
| Caption and adoption-line text, EN/DE | 121 ok | `data/site-final-sync/caption-check.log` |
| Forbidden scan, page subtree | worktree clean, one documented historical hit | `data/site-final-sync/forbidden-site-aipf.log` |

The final matrix covers 375, 768 and 1280 px in light and dark, EN and DE: no
horizontal scroll, three solid-bar charts with one capped CI line each (0% bars
empty), axe WCAG 2.0/2.1 A+AA with no serious or critical violations, no console
or page errors, all four figures loaded with alt text, and the try-it form still
making zero network requests.

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

Result: **PASS** in 254 s. The fresh clone installed the pinned dependencies
and the 541.5 MiB German spaCy model, ran the test suite (650 passed, 1 skipped:
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

Result: **PASS**. Project: `no leaks found` for the working tree and the git
history, 63 commits scanned. Page repository at `5ffb2b4`: `no leaks found` for
the working tree and the git history, 37 commits scanned. gitleaks 8.30.1,
redacted output, project `.gitleaks.toml`.

## Gate 3 - forbidden scan on both repositories (one documented page-history exception)

Commands:

```sh
# project repository, working tree
uv run python scripts/scan_forbidden.py

# project repository, every local ref
uv run python scripts/scan_forbidden.py --root . --all-refs

# screenshot source text, the four DOM dumps the shots were rendered from
uv run python scripts/scan_forbidden.py --root <shots-evidence> --no-history

# page repository, the AI Privacy Filter page subtree only
uv run python scripts/scan_forbidden.py --root . --path ai-privacy-filter --all-refs
```

Project result: **PASS**, `scan_forbidden: clean` for the tree and for the
history of all local refs (67 commits, including this file's own commit). The
screenshots' source text: **PASS**, `scan_forbidden: clean`. The four committed
PNGs also contain no text chunks, and their compressed strings scan clean. The
matched text is never printed.

Page result: **PASS for the current page tree; one documented historical
false positive.** The page worktree is clean. The page subtree's full history
contains one hit in a superseded commit: the Faker `de_DE` first-name export at
`ai-privacy-filter/assets/first-names-de.js:3` once contained a common German
first name that collides with the private forbidden list. The value is not
reproduced here. Commit `0797d25` removed that entry, and the current file and
current page contain no hit. The name is a dictionary entry in a generated
public first-name list, not a private identifier or a project value. No history
rewrite was performed, because the site repository is a shared local repository.
This is the only page-subtree history hit.

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
python3 -c "..."                  # page example ids, mock flags and backends
uv run pytest tests/test_method.py tests/test_eval_results.py -q
```

Results: **PASS**.

- `git ls-files data/external` is empty. GermEval stays under the git-ignored
  `data/external/` directory, verified by SHA-256 before use.
- `results/page/examples.json` has 15 entries, all `syn-test-*`, all
  `mock: false`; 13 have a restored draft and 2 are `guard-blocked`. They are
  synthetic Faker `de_DE` and hand-written templates, not real people. The four
  review screenshots show the same synthetic test enquiries, not real people.
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
- `ruff format --check`: 119 files already formatted.
- `mypy --strict src`: no issues in 42 source files.
- `pytest -m "not live and not e2e"`: 651 passed, 3 deselected (the live and
  e2e markers); the clean clone runs 650 passed and 1 optional GermEval skip
  before reproduction downloads the data.
- Coverage: **88.70%** branch coverage on `ai_privacy_filter`, above the 85%
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
  hypothesis entry was corrected to MPL-2.0. Neither the file nor the
  dependency set changed in the final polish.
- The page credits section names every component and every licence in the
  inventory: MIT, BSD-3-Clause, Apache-2.0, MPL-2.0, CC BY 4.0,
  Sustainable Use License and SIL OFL 1.1. It was verified in a browser at
  375 px and 1280 px, EN and DE, and the final sync diff does not touch the
  credits block. The final site matrix re-checked all 12 variants for layout,
  axe and console cleanliness.
- The page says pseudonymises, keeps "not legal advice" and makes no
  compliance or anonymity claim.

## Command timings on 2026-10-28

| command | wall time | log |
|---|---|---|
| `make lint` | 1 s | `data/aipf-release-final/logs/lint.log` |
| `make test` | 28 s | `data/aipf-release-final/logs/test.log` |
| `make coverage` | 34 s | `data/aipf-release-final/logs/coverage.log` |
| `make reproduce` | 307 s | `data/aipf-release-final/logs/reproduce.log` |
| `scripts/clean_clone_check.sh` | 254 s | `data/aipf-release-final/logs/clean-clone.log` |
| `make secrets` | 3 s | `data/aipf-release-final/logs/gitleaks*.log` |
| page repo `gitleaks dir` / `gitleaks git` | 3 s | `data/aipf-release-final/logs/gitleaks-site-*.log` |
| `scan_forbidden`, tree / all refs / shot source | 3 s / 4 s / <1 s | `data/aipf-release-final/logs/scan-forbidden-*.log` |
| `make page-data` (first / second run) | 5 s / 4 s | `data/aipf-release-final/logs/page-data-run{1,2}.log` |

`make reproduce` reported `bit-for-bit identical` for the five result files,
`SHA256SUMS` and the latest run's metrics, report and scoring files. The two
`make page-data` runs wrote the same 38 files; `diff -r` between the runs is
empty and `git status` stayed clean. The log directory
`data/aipf-release-final/` sits outside this repository.

## Release tag

```sh
git tag -a v1.0.0 -m "AI Privacy Filter v1.0.0"
```

The annotated tag is local only. It is not pushed. Use
`git rev-parse v1.0.0^{commit}` for the tagged commit.

At the time of this check the tag still resolves to `6bc7b95`, the commit
before the re-captured screenshots, while the gates above ran at `950a761` and
the two history scans ran at this branch's final head. Moving the tag to the
final head and updating the handoff are left to the release owner after the
branch is landed.

## Honest exceptions

- The tag target is behind the checked head until the release owner moves it;
  the re-captured screenshots and this document are the only difference.
- The page repository is a shared local repository. Its history was not
  rewritten; the single page-subtree historical name collision above is
  reported rather than hidden.
- The site commits (`ab26d36`, `5ffb2b4`) are local and unpushed; the site
  matrix results in this file are the site lanes' recorded runs, not a re-run
  from this check.
- The page's GitHub link returns HTTP 404 until the repository is
  published. This is recorded in the page checks and is not a release failure.
- The optional GermEval test is skipped in the clean clone before `make
  reproduce` downloads the data; reproduction then verifies the full GermEval
  result file.
- `results/page` meta (`results.json` commit, `demos.json` commit) still names
  two dangling pre-rebase evaluation commits, kept verbatim so the site and
  project bundles stay byte-identical. They were never on any mainline,
  predate the authorship rewrite and are absent from fresh clones.
- Live model calls and the n8n end-to-end run need the private environment and
  are not part of the offline release gate. Their recorded runs are in
  `results/runs/` and `docs/n8n.md`; the screenshot capture run is described in
  the re-capture notes, not committed.

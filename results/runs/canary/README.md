# Live canary logs

One canary per backend, run once under the shared live-LLM lock.
These files are the redacted record; nothing here is hand-written.

| file | backend | what it proves |
|---|---|---|
| `claude-cli.json` | `claude-cli` | exact argv, schema-valid JSON, no permission denial, canary token absent |
| `codex-cli.json` | `codex-cli` | exact argv with billing pins, schema-valid JSON, no tool event, canary token absent |
| `byok.json` | `byok` (OpenCode Go) | one schema-valid JSON call, session header sent, reasoning tokens recorded |

## What is redacted

- Credential values are replaced with `[redacted]`; only header *names* and
  environment variable *names* appear.
- Scratch directory names (`aipf-cli-...`) and canary roots
  (`aipf-canary-...`) are replaced with `<tempdir>` / `<canary-root>`; no
  absolute temporary path remains in an argv record.
- The BYOK endpoint host is not written at all.
- The prompt is stored as a SHA-256 hash; prompts are synthetic anyway.
- No real personal data and no real CLI transcript with real input is stored.

## Reproduce

```sh
set -a; . ~/.config/ai-privacy-filter/private.env; set +a
mkdir /path/to/state/aipf-lock-live-llm
AIPF_CANARY_LOG_DIR=results/runs/canary uv run pytest -m live
rmdir /path/to/state/aipf-lock-live-llm
```

The lock lives outside this repository. Run live calls only while holding it.
Logs are rewritten on each run; a rerun must be recorded with its reason in the
task notes.

# LLM backends

Four backends implement `LLMBackend`
(`src/ai_privacy_filter/backends/base.py`):

| name | transport | auth | where it runs |
|---|---|---|---|
| `fake` | in-process | none | deterministic fixture backend |
| `byok` | OpenAI-compatible HTTP | bearer key from `AIPF_BYOK_API_KEY` | OpenCode Go, or a local Ollama endpoint |
| `claude-cli` | `claude` binary | Claude.ai subscription login | fresh empty temporary directory |
| `codex-cli` | `codex` binary | ChatGPT login | fresh empty temporary directory |

The backend layer is ported from SaberOps, Apache-2.0
(`src/saberops/workers/{base,claude,codex,direct_api,env,exhaustion}.py`).
Attribution headers are kept in every ported module.

## Isolation contract

Tools-off isolation is the point of this layer, not a side effect. Every CLI call
does all of the following:

1. **Fresh empty directory.** `tempfile.mkdtemp(prefix="aipf-cli-")` creates a
   private scratch directory (mode `0700`) that is the process working
   directory. It contains nothing from this repository, no `.git`, no instructions
   files, no sessions. It is removed with `shutil.rmtree` in a `finally` block,
   also after a timeout or a failed call.
2. **Stdin-fed prompt.** The prompt travels on standard input; it never appears in
   argv (`ps` output, shell history, process accounting).
3. **Hermetic environment.** The child gets an allowlist of runtime variables and
   nothing else. See [Environment](#environment).
4. **No tools.** Claude receives `--tools ""`; Codex receives
   `--sandbox read-only` plus a prompt that forbids tools, and any tool or
   command event in the Codex JSON stream is a hard failure
   (`error_kind="tool_use"`).
5. **Timeout kills the group.** The child starts in its own session; on timeout
   the whole process group is killed before the scratch directory is removed.

## Exact argv

Verified against the installed binaries: **claude 2.1.311**, **codex-cli
0.164.0**. The argv is asserted verbatim in
`tests/test_backend_claude.py` and `tests/test_backend_codex.py`.

### claude-cli

```text
claude -p \
  --output-format json \
  --tools "" \
  --strict-mcp-config \
  --no-session-persistence \
  --setting-sources "" \
  --settings '{"disableAllHooks":true,"forceLoginMethod":"claudeai","promptSuggestionEnabled":false}' \
  [--model <model>]
```

| flag | why it is there | help evidence (2.1.311) |
|---|---|---|
| `-p` | print mode; reads the prompt from stdin | "Print response and exit (useful for pipes)" |
| `--output-format json` | one result object on stdout | choices `text`, `json`, `stream-json` |
| `--tools ""` | disables every built-in tool | `Use "" to disable all tools` |
| `--strict-mcp-config` | no `--mcp-config` value means no MCP server | "Only use MCP servers from --mcp-config" |
| `--no-session-persistence` | nothing written that can be resumed | "sessions will not be saved to disk" |
| `--setting-sources ""` | loads no user, project or local settings file | "Comma-separated list of setting sources (user, project, local)" |
| `--settings <json>` | pins the subscription billing method and removes hooks | "Path to a settings JSON file or a JSON string" |

`forceLoginMethod: "claudeai"` keeps the call on the Claude.ai subscription
login. `disableAllHooks` removes hook execution. `promptSuggestionEnabled:
false` is off because a batch call has no prompt box.

**`--bare` is deliberately not used.** The installed CLI documents it as taking
Anthropic auth "strictly ANTHROPIC_API_KEY or apiKeyHelper ... (OAuth and keychain
are never read)". That would replace the subscription login with an API key and
defeat the billing pin, so the compatible choice is the flag set above.

### codex-cli

```text
codex exec \
  --json \
  --ephemeral \
  --skip-git-repo-check \
  --ignore-user-config \
  --sandbox read-only \
  -C <tempdir> \
  --output-schema <tempdir>/schema.json \
  -c forced_login_method="chatgpt" \
  -c model_provider="openai" \
  -c service_tier="default" \
  [--model <model>] \
  -
```

| flag | why it is there | help evidence (0.164.0) |
|---|---|---|
| `--json` | JSONL events on stdout, including usage | "Print events to stdout as JSONL" |
| `--ephemeral` | no session files written | "Run without persisting session files to disk" |
| `--skip-git-repo-check` | the scratch directory is not a repository | "Allow running Codex outside a Git repository" |
| `--ignore-user-config` | drops `$CODEX_HOME/config.toml` and its project trust table | "Do not load `$CODEX_HOME/config.toml`; auth still uses `CODEX_HOME`" |
| `--sandbox read-only` | no writes from a command | possible values include `read-only` |
| `-C <tempdir>` | working root is the fresh directory | "Tell the agent to use the specified directory as its working root" |
| `--output-schema <file>` | the schema is written into the scratch directory and enforced on the final message | "Path to a JSON Schema file describing the model's final response shape" |
| `-c key=value` | billing and provider pins override any config layer | "Override a configuration value ... loaded from `~/.codex/config.toml`" |
| `-` | prompt is read from stdin | "If not provided as an argument (or if `-` is used), instructions are read from stdin" |

The three `-c` pins are identical to the SaberOps adapter:
`forced_login_method="chatgpt"` (never API billing), `model_provider="openai"`
(never another provider) and `service_tier="default"` (never separately billed
fast mode).

## Environment

`hermetic_env(harness)` (`src/ai_privacy_filter/backends/env.py`) projects only:

- exact runtime names: `PATH`, `HOME`, `USER`, `LOGNAME`, `SHELL`, `LANG`, `TZ`,
  `TERM`, `COLORTERM`, `TMPDIR`, the `XDG_*` runtime directories, proxy variables,
  `SSL_CERT_FILE`, `SSL_CERT_DIR`, `NODE_EXTRA_CA_CERTS`;
- every `LC_*` variable;
- the harness configuration directory: `CLAUDE_CONFIG_DIR` and
  `CLAUDE_CODE_DISABLE_FAST_MODE` for Claude, `CODEX_HOME` for Codex.

Everything else is dropped. Credential-shaped overlay keys are denied even if
they look allowlisted (`ANTHROPIC_*`, `CLAUDE_CODE_USE_*` for Claude;
`OPENAI_*`, `CODEX_API_KEY`, `CODEX_ACCESS_*` for Codex), so an exported API key
cannot switch billing. `HOME`/`CLAUDE_CONFIG_DIR`/`CODEX_HOME` stay because the
subscription credential lives there.

## Result parsing

Only native completion evidence counts; an exit code alone is not success.

- **Claude (`--output-format json`)**: one result object. Success requires
  `type == "result"`, `subtype == "success"`, `is_error == false` and an empty
  `permission_denials`. The answer is `result`; the model id comes from the
  `modelUsage` key; usage is `input_tokens`/`output_tokens`/
  `cache_read_input_tokens`. A non-empty `permission_denials` is
  `tool_use`.
- **Codex (`exec --json`)**: JSONL. The answer is the last completed
  `item.completed` event whose item type is `agent_message`; usage comes from
  `turn.completed` (`input_tokens`, `output_tokens`, `cached_input_tokens`,
  `reasoning_output_tokens`). Item types `agent_message`, `reasoning`,
  `todo_list` and `error` are allowed; **any other item type fails closed** with
  `tool_use`, and so does an item without a known type.
- **BYOK**: `choices[0].message.content` only. A `reasoning_content` or
  `reasoning` field is never parsed. `usage.completion_tokens_details.
  reasoning_tokens` is recorded. A fenced code block around the JSON is stripped
  before parsing.
- All backends normalise usage to `input_tokens`, `output_tokens`,
  `total_tokens`, `cached_tokens`, `reasoning_tokens` (values may be `null`).
  The token meanings follow the provider: Codex counts cached input inside
  `input_tokens`, so `total_tokens = input_tokens + output_tokens`; Claude
  reports cache reads separately from `input_tokens`, so its `total_tokens` is
  `input_tokens + output_tokens` and `cached_tokens` is additional context that
  is not folded in. BYOK uses the OpenAI totals verbatim.

Replies are validated against the requested schema by
`src/ai_privacy_filter/backends/schema.py` (a small validator for the keyword
subset `TRIAGE_SCHEMA` uses: `type`, `enum`, `properties`, `required`,
`additionalProperties: false`, `minLength`/`maxLength`, `items`,
`minItems`/`maxItems`). No JSON-schema package is added.

## Error kinds

`BackendResult.error_kind` is `None` on success. Failures never raise out of
`complete_json`.

| `error_kind` | meaning |
|---|---|
| `not_installed` | CLI binary not on `PATH` |
| `not_configured` | BYOK base URL, model or key missing |
| `timeout` | call exceeded `timeout_s`; process group killed |
| `auth_error` | missing, expired or rejected login / key |
| `billing_error` | payment or spend-cap problem (never retried as quota) |
| `quota_exhausted` | plan or usage window spent |
| `rate_limited` | short-lived rate limit |
| `tool_use` | a tool, command or permission denial appeared |
| `cli_error` | any other CLI failure |
| `http_error` | HTTP status that is not auth, billing or rate limit |
| `network_error` | transport failed before a response |
| `empty_response` | empty reply where text was required |
| `invalid_response` | provider response had an unexpected shape |
| `invalid_json` | reply was not parseable JSON |
| `schema_error` | reply parsed but violates the requested schema |

## Quota classification

`src/ai_privacy_filter/backends/quota.py` is the ported SaberOps `exhaustion.py`
classifier (R-001 rules). Verdicts: `QUOTA_EXHAUSTED`, `RATE_LIMITED`, `OTHER`.

- Auth and billing text are checked first and are always `OTHER`: a rejected key
  is never retried as a plan window.
- Claude: quota is a `result` frame with `is_error: true` plus either
  `api_error_status: 429`, a quota phrase in `result`, or
  `usage_limit_reached`. Structured `resetsAt` (epoch) and `resets_at` (ISO)
  win over prose; a zoned prose reset such as `resets 10:20pm (Europe/Zurich)` is
  parsed. A `system`/`api_retry` event or `request rejected (429)` is
  `RATE_LIMITED`. Overloaded/529 and spend-cap frames are `OTHER`.
- Codex: only a terminal `turn.failed`/`error` event is classified, so an
  interrupted stream is never read as a limit. Usage-limit prose is
  `QUOTA_EXHAUSTED`; `rate limit ... please try again in Ns` is
  `RATE_LIMITED` with a relative reset.
- Dropped from the origin: quota windows/model scopes, absolute prose dates in
  host-local time, persistence of quota observations and routing exclusions.
  `QuotaClassification` carries only verdict, reset and rule.

## BYOK and Ollama

`BYOKBackend` posts to `{base}/chat/completions` with:

```text
Authorization: Bearer <AIPF_BYOK_API_KEY>
User-Agent: ai-privacy-filter/<version>
x-opencode-session: <fresh UUID per enquiry>
Content-Type: application/json
```

`max_tokens` defaults to 2000 (`AIPF_BYOK_MAX_TOKENS` overrides it) and the
prompt is one `user` message. OpenCode Go is configured with
`AIPF_BYOK_BASE_URL`, `AIPF_BYOK_API_KEY` and `AIPF_BYOK_MODEL`; the registry
constructs the backend without a network call and reads the values at call time.

The same backend serves Ollama:

```python
from ai_privacy_filter.backends.byok import BYOKBackend

backend = BYOKBackend.for_ollama("qwen2.5:3b")  # 127.0.0.1:11435/v1, 300 tokens
```

`for_ollama` uses `OLLAMA_BASE_URL` (`http://127.0.0.1:11435/v1`), a 300-token
ceiling, no bearer key and a JSON-schema `response_format`. Ollama is reached
through the SSH tunnel, never from CI.

## Honest limitations

- **Codex read-only can still read files.** The Codex sandbox blocks writes, not
  reads. The prompt says not to use tools, and a tool event fails the call, but
  that is detection of what the CLI reports, not prevention. The scratch
  directory is empty so there is nothing useful to read locally, and the canary
  test proves a token one level above it does not reach the answer.
- **Claude's single-object output hides intermediate events.** With
  `--output-format json` only the final result object is visible; a tool call
  would appear as a `permission_denials` entry, which fails the call. `--tools
  ""` means no tool is offered in the first place.
- **User-level instructions can still be context.** `--setting-sources ""` drops
  settings files, and the cwd has no `CLAUDE.md`, but the CLI may still include a
  user-level memory file in the system prompt. It grants no capability because no
  tools are enabled. `--bare` would stop that, but it also stops subscription
  auth (see above).
- **CLI model defaults are not pinned.** Without a model argument the CLI uses
  its own default; the backend reports the model only when the native output
  names it (Claude `modelUsage`, Codex `model` when present). Pass `model=` to pin
  one.
- **No streaming.** Calls capture the full reply. Streaming callbacks from
  SaberOps are intentionally dropped.

## Live canary

`tests/test_live_backends.py` holds one `@pytest.mark.live` test per backend.
Run once, under the shared live-LLM lock, with the private env file loaded.
Results belong in `results/runs/canary/`; see `results/runs/canary/README.md`.

The CLI canaries create a `CANARY.txt` with a random token in the parent of the
fresh working directory. A passing canary means: schema-valid JSON, no tool
event, and the token absent from the answer and parsed payload.

### Canary result

One run on 2026-10-27 under the shared lock, private env loaded, 3 passed in
24.75 s:

| backend | model reported | latency | result |
|---|---|---|---|
| `claude-cli` | `claude-opus-5-5` | 11.5 s | schema-valid JSON, no permission denial, token absent |
| `codex-cli` | native default (not named in the stream) | 9.0 s | schema-valid JSON, zero tool events, token absent |
| `byok` | `deepseek-v4.1-flash` | 3.8 s | schema-valid JSON, `reasoning_tokens=246` recorded |

Logs: `results/runs/canary/`. The Codex argv record shows
`<canary-root>/<tempdir>`; those are the random scratch paths with the path
itself redacted. No rerun was needed, so there is exactly one run per backend.

## Triage run

`scripts/run_triage.py` is the live entry point behind `make live`. It reads the
fixed 40-enquiry subset from `METHOD.md` section 8 and calls each requested
backend once per enquiry, in the listed order. `claude-cli`, `codex-cli` and
`byok` are the primary set; `ollama` is optional and runs last through the SSH
tunnel.

```sh
set -a; . ~/.config/ai-privacy-filter/private.env; set +a
AIPF_LOCK_DIR=/path/to/shared/state make live
AIPF_LOCK_DIR=/path/to/shared/state uv run python scripts/run_triage.py \
    --live --backends ollama --run-id <same-run-id> --resume
```

`AIPF_LOCK_DIR` is the shared state directory that holds
`aipf-lock-live-llm` and `aipf-lock-server`; both are outside the repository.
The live lock is held for every real call and released in `finally`. The Ollama
stage also takes the server lock and is time-boxed to 90 minutes.

Responses are stored under `results/runs/<backend>/responses/` with the raw
text (placeholders only), the parsed payload, latency, usage and a SHA-256
hash; `index.json` pins every response hash. The run record, `metrics.json`,
`report.md` and the text-free `scoring/` files live under
`results/runs/<run-id>/`. `make reproduce` rebuilds the numbers with no network
and no model call.

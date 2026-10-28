# n8n triage workflow (local)

This directory holds the review workflow that sits in front of the privacy
filter. It is a local demonstration, not a hosted service: one compose stack,
one throwaway n8n instance on loopback and the gate on a private compose
network.

## What the workflow does

1. **Webhook** — `POST /webhook/enquiry` takes `{ "id": "...", "text": "..." }`
   and answers immediately with HTTP 200 and "workflow was started".
2. **Pseudonymise** — `POST $AIPF_GATE_URL/redact` replaces personal values with
   placeholders and returns a short-lived ticket.
3. **Triage via gate** — `POST $AIPF_GATE_URL/process` runs the guard and then
   one model call. A guard block (HTTP 422) stops the run; the pending record
   says `blocked` and no model call was made.
4. **Guard ok?** — a branch on the gate status.
5. **Pending record** — writes `{status, form_url, redacted_text, category,
   urgency}` to `/data/pending/<id>.json`. This is how the local e2e harness
   learns the review URL; in normal use a person opens the same URL from n8n.
6. **Restore draft** — `POST $AIPF_GATE_URL/restore` puts the placeholders back
   into the model's reply.
7. **Approve reply** — a Wait form showing the redacted text, category,
   urgency and the restored draft, with `approve`, `edit` and `reject`.
8. **Write result** — `/data/out/<id>.json` with the restored draft, decision,
   final reply and approval flag.

The workflow contains no credentials. The gate URL and backend name come from
`AIPF_GATE_URL` and `AIPF_BACKEND` in the n8n environment.

## Running the stack

```sh
# one-time, in the private environment file:
#   AIPF_N8N_OWNER_EMAIL, AIPF_N8N_OWNER_PASSWORD
#   AIPF_BYOK_BASE_URL, AIPF_BYOK_API_KEY, AIPF_BYOK_MODEL
make e2e
```

`make e2e` resets the git-ignored `.n8n-data/`, builds the gate image, starts
gate and n8n, creates the throwaway owner, imports and activates the workflow,
posts five synthetic dev-split enquiries, completes the five review forms in
headless Chromium and validates the output files. It holds the live-LLM lock
for the whole run and removes the containers afterwards. The run passes twice
in a row from a clean `.n8n-data/`.

The harness also checks that:

- the hash of each outbound text appears in the gate log (the gate logs
  `outbound_sha256` only when `AIPF_E2E_MODE=1`);
- no gold value from the synthetic record appears in that outbound text or in
  the gate log;
- the restored draft contains no placeholder and restore reported none
  unknown;
- `approve`, `edit` and `reject` are written with the right final reply.

The screenshot at `results/page/n8n-workflow.png` is written by the same run at
1280 px in the light theme. It shows the canvas only, no review data and no
local paths.

## Trying it by hand

```sh
make demo
```

starts the same stack and stops before any review: it builds the gate image,
starts gate and n8n, creates the throwaway owner, imports and activates the
workflow and posts three synthetic dev-split enquiries through the normal
webhook. It then prints the n8n URL and one review-form URL per enquiry; open
each form, choose `approve`, `edit` or `reject` and submit it, and n8n writes
`/data/out/<id>.json` next to the other scratch state. The demo holds the
live-LLM lock only while it posts the enquiries, then leaves the stack running
so the forms stay reachable in the browser.

```sh
make demo-down
```

stops the stack and deletes `.n8n-data/`. Nothing is written to the repository:
the forms, pending records and outputs live only in that throwaway directory.

## Files and state

| path | purpose |
|---|---|
| `compose.yaml` | pinned n8n image plus the gate service; only n8n is published, on `127.0.0.1:5678` |
| `Containerfile` | gate image built from this tree and `uv.lock` |
| `workflows/enquiry-triage.json` | the exported, active workflow |
| `.n8n-data/` | n8n's home, review URLs and output files; git-ignored |
| `scripts/e2e/` | compose driver, headless-browser steps, input corpus loading |
| `scripts/demo/` | `make demo` / `make demo-down` entry point over the same driver |

The n8n data directory is world-writable for the duration of the run because
the rootless container user has to write the bind mount. It is disposable and
never committed. The owner password is the throwaway value from the private
environment; if n8n's policy asks for a missing character class, the harness
appends that class for setup and login.

## Limitations

- The workflow is a demonstration of the filter, not a production queue. There
  is no authentication on the webhook, no retry policy and one review form per
  enquiry.
- The n8n file-access allowlist is narrowed to `/data/out` and `/data/pending`;
  the gate container has no host port.
- The e2e run uses the dev split only, so it consumes no final test-split
  number from `METHOD.md`.
- This is pseudonymisation: it does not make text anonymous, and nothing
  here is legal advice.

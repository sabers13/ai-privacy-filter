# Ollama on a separate host

Optional local-model path: Ollama runs on a small separate machine as a normal
user, listens on its loopback only, and this workstation reaches it through an
SSH tunnel. Nothing here is required for the main pipeline; the BYOK backend
stays the default.

The scripts never contain a host, IP, SSH user, key path or absolute home path.
The only connection input is `$AIPF_OLLAMA_SSH` (an ssh destination, supplied at
run time from the private environment file). Everything on the model host lives
under `~/aipf-ollama/` plus one systemd user unit; no sudo, no other service, no
other port.

## What it does

| Script | Runs on | Purpose |
|---|---|---|
| `install.sh` | model host | download pinned Ollama, verify sha256, unpack to `~/aipf-ollama/`, write and start the `systemd --user` unit `aipf-ollama.service` |
| `pull.sh [model]` | model host | pull one model (default `qwen2.5:1.5b`) into `~/aipf-ollama/models/` |
| `tunnel.sh` | this workstation | `ssh -N -L 127.0.0.1:11435:127.0.0.1:11434`, loopback on both ends |
| `smoke.sh [model]` | this workstation | one German triage request through the tunnel, schema-checked, with tokens/s |
| `teardown.sh --yes` | model host | stop and disable the unit, delete the unit file and `~/aipf-ollama/` |

Typical sequence, with the private environment loaded
(`set -a; . ~/.config/ai-privacy-filter/private.env; set +a`):

```sh
ssh "$AIPF_OLLAMA_SSH" 'bash -s' < install.sh
ssh "$AIPF_OLLAMA_SSH" 'bash -s qwen2.5:1.5b' < pull.sh
tmux new-window -n aipf-tunnel ./tunnel.sh
./smoke.sh qwen2.5:1.5b
```

The service binds `OLLAMA_HOST=127.0.0.1:11434`; the tunnel forwards it to
`127.0.0.1:11435` on this machine, so the backend base URL is
`http://127.0.0.1:11435/v1`. Neither end ever listens on `0.0.0.0` or on an
overlay-network address.

## Limits on the box

The daemon is tuned for a small host (measured on 4 vCPU, 3.3 GiB RAM, no GPU):

- `OLLAMA_NUM_PARALLEL=1`, `OLLAMA_MAX_LOADED_MODELS=1`, `OLLAMA_KEEP_ALIVE=10m`
  and `OLLAMA_CONTEXT_LENGTH=4096` keep one model resident and avoid swapping.
- `qwen2.5:1.5b` is 986 MB on disk. Measured peak for the whole user unit was
  1.40 GiB (cgroup peak), with no swap used, and one 193-token prompt / 133-token
  answer took 8.9 s (about 15 tokens/s). The idle daemon is under 30 MiB.
- If a larger model OOMs or an answer takes longer than 60 s, drop to
  `qwen2.5:0.5b` (`pull.sh qwen2.5:0.5b`). Swapping counts as OOM.
- Expect roughly 20–40 s per call on hardware like this with longer prompts; keep
  prompts short and `max_tokens` at 300 for triage.

## What was measured (T07)

`results/ollama_server.json` holds the full record: service active, checksum
verified against the published `sha256sum.txt`, `/v1/models` reachable through the
tunnel, one schema-valid triage JSON with timings and memory. Two honest caveats
from that single call: the 1.5B model chose the wrong category for a quote
request, and it invented a `[NAME_1]` placeholder that was not in the input. The
restore and guard steps must reject unknown placeholders.

Licence: Apache-2.0 for `qwen2.5:1.5b`; see `data/SOURCES.md`.

## Removing it

On the model host:

```sh
ssh "$AIPF_OLLAMA_SSH" 'bash -s -- --yes' < teardown.sh
```

That disables and removes the unit, then deletes `~/aipf-ollama/` (binary, models,
downloads) and nothing else. On this workstation, stop the tmux window
`aipf-tunnel` and that is the whole footprint. `--yes` is required so an
accidental run cannot wipe a working model host.

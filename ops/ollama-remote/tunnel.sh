#!/usr/bin/env bash
# Forward the model host's loopback Ollama port to 127.0.0.1:11435 on this machine.
#
# Usage: ./tunnel.sh
#
# Reads the SSH destination only from $AIPF_OLLAMA_SSH; no host, IP, user or
# key path is built into this file. Both ends of the forward are loopback, so
# the API never listens on a public or overlay-network address.
#
# Run it in a tmux window so it survives the terminal:
#
#   tmux new-window -n aipf-tunnel ./tunnel.sh
#
# Then use base URL http://127.0.0.1:11435/v1.

set -euo pipefail

: "${AIPF_OLLAMA_SSH:?set AIPF_OLLAMA_SSH to an ssh destination before running tunnel.sh}"

LOCAL_PORT="${AIPF_OLLAMA_LOCAL_PORT:-11435}"
REMOTE_PORT="${AIPF_OLLAMA_REMOTE_PORT:-11434}"

printf 'tunnel: 127.0.0.1:%s -> %s:127.0.0.1:%s\n' \
    "${LOCAL_PORT}" "model host" "${REMOTE_PORT}" >&2
printf 'tunnel: leave this running; Ctrl-C closes it\n' >&2

# ExitOnForwardFailure makes a busy local port a hard error instead of a
# silently dead tunnel. Keepalives notice a dropped link.
exec ssh -N \
    -o ExitOnForwardFailure=yes \
    -o ServerAliveInterval=15 \
    -o ServerAliveCountMax=4 \
    -o BatchMode=yes \
    -L "127.0.0.1:${LOCAL_PORT}:127.0.0.1:${REMOTE_PORT}" \
    "${AIPF_OLLAMA_SSH}"

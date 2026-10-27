#!/usr/bin/env bash
# Pull one model into the user-space Ollama install.
#
# Usage: ./pull.sh [model]        # default: qwen2.5:1.5b
#
# Runs on the model host through the unit started by install.sh; the daemon
# must already be active. Model blobs land under ~/aipf-ollama/models/.

set -euo pipefail

MODEL="${1:-qwen2.5:1.5b}"
INSTALL_ROOT="${HOME}/aipf-ollama"

die() {
    printf 'pull: %s\n' "$*" >&2
    exit 1
}

[ -x "${INSTALL_ROOT}/bin/ollama" ] || die "no install at ${INSTALL_ROOT}; run install.sh first"

export OLLAMA_HOST="${OLLAMA_HOST:-127.0.0.1:11434}"
export OLLAMA_MODELS="${OLLAMA_MODELS:-${INSTALL_ROOT}/models}"

printf 'pull: fetching %s from %s\n' "${MODEL}" "${OLLAMA_HOST}"
"${INSTALL_ROOT}/bin/ollama" pull "${MODEL}"
"${INSTALL_ROOT}/bin/ollama" list

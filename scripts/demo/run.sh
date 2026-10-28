#!/usr/bin/env bash
# Start or stop the local n8n demo stack.
#
# `up` loads the private runtime environment into this process only, so the
# values reach the stack through the environment and are never printed. `down`
# needs no secrets.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/../.."

mode="${1:-up}"
case "${mode}" in
  up)
    env_file="${AIPF_PRIVATE_ENV:-${HOME}/.config/ai-privacy-filter/private.env}"
    if [ ! -f "${env_file}" ]; then
      echo "demo: private environment file is missing" >&2
      exit 1
    fi
    set -a
    # shellcheck disable=SC1090
    . "${env_file}"
    set +a
    exec uv run python scripts/e2e/run_demo.py
    ;;
  down)
    exec uv run python scripts/e2e/run_demo.py --down
    ;;
  *)
    echo "usage: scripts/demo/run.sh [up|down]" >&2
    exit 2
    ;;
esac

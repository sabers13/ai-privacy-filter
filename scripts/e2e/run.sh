#!/usr/bin/env bash
# Run the n8n end-to-end check with the private runtime environment loaded.
#
# Values are never printed: the file is sourced into this process only. The
# Python driver receives them through the environment.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/../.."

env_file="${AIPF_PRIVATE_ENV:-${HOME}/.config/ai-privacy-filter/private.env}"
if [ ! -f "${env_file}" ]; then
  echo "e2e: private environment file is missing" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
. "${env_file}"
set +a

exec uv run python scripts/e2e/run_e2e.py "$@"

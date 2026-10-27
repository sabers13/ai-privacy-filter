#!/usr/bin/env bash
# Remove the user-space Ollama install: stop and disable the unit, delete the
# unit file and ~/aipf-ollama/ (binary, models, downloads).
#
# Usage: ./teardown.sh --yes
#
# --yes is required so a stray invocation cannot wipe a working model host.
# Safe to re-run: every step tolerates an already-removed piece. Nothing else
# on the host is touched, and no sudo is needed.

set -euo pipefail

UNIT_NAME="aipf-ollama.service"
INSTALL_ROOT="${HOME:?HOME is not set}/aipf-ollama"
UNIT_DIR="${XDG_CONFIG_HOME:-${HOME}/.config}/systemd/user"

if [ "${1:-}" != "--yes" ]; then
    printf 'teardown: refusing without --yes (this deletes %s and the unit)\n' "${INSTALL_ROOT}" >&2
    exit 2
fi

if command -v systemctl >/dev/null 2>&1; then
    systemctl --user disable --now "${UNIT_NAME}" 2>/dev/null || true
    rm -f "${UNIT_DIR}/${UNIT_NAME}"
    systemctl --user daemon-reload 2>/dev/null || true
    systemctl --user reset-failed "${UNIT_NAME}" 2>/dev/null || true
fi

rm -rf -- "${INSTALL_ROOT}"
printf 'teardown: unit and %s removed\n' "${INSTALL_ROOT}"

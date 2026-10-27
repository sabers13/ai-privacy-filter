#!/usr/bin/env bash
# Install a pinned Ollama build for the current user and run it as a systemd user unit.
#
# Run this on the model host as a normal user. It needs no sudo and touches
# nothing outside ~/aipf-ollama/ and the unit file in the systemd --user
# configuration directory. Host, user and paths are never baked in: $HOME,
# $XDG_CONFIG_HOME and the pinned version decide everything.
#
# Usage: ./install.sh
#
# Exit codes: 0 installed and serving, 1 failed, 2 precondition failed.

set -euo pipefail

OLLAMA_VERSION="${OLLAMA_VERSION:-0.40.11}"
ARCHIVE="ollama-linux-amd64.tar.zst"
BASE_URL="https://github.com/ollama/ollama/releases/download/v${OLLAMA_VERSION}"
INSTALL_ROOT="${HOME}/aipf-ollama"
UNIT_DIR="${XDG_CONFIG_HOME:-${HOME}/.config}/systemd/user"
UNIT_NAME="aipf-ollama.service"
UNIT_PATH="${UNIT_DIR}/${UNIT_NAME}"
LOOPBACK="127.0.0.1:11434"

log() { printf 'install: %s\n' "$*"; }
die() {
    printf 'install: %s\n' "$*" >&2
    exit 1
}
precondition_failed() {
    printf 'install: %s\n' "$*" >&2
    exit 2
}

: "${HOME:?HOME is not set}"

command -v curl >/dev/null 2>&1 || die "curl is required"
command -v tar >/dev/null 2>&1 || die "tar is required"
command -v sha256sum >/dev/null 2>&1 || die "sha256sum is required"
command -v systemctl >/dev/null 2>&1 || die "systemctl is required"
tar --help 2>/dev/null | grep -q -- '--zstd' || command -v zstd >/dev/null 2>&1 \
    || die "zstd is required to unpack ${ARCHIVE}"

# Refuse to take over a port that something else already owns. Only our own
# unit is allowed to hold the loopback port before a reinstall.
if (exec 3<>"/dev/tcp/127.0.0.1/11434") 2>/dev/null; then
    if systemctl --user is-active --quiet "${UNIT_NAME}" 2>/dev/null \
        && [ -x "${INSTALL_ROOT}/bin/ollama" ]; then
        log "own unit is already serving ${LOOPBACK}; reinstalling around it"
    else
        precondition_failed "port 11434 is already in use by another service; not touching it"
    fi
fi

mkdir -p "${INSTALL_ROOT}/download" "${INSTALL_ROOT}/models" "${UNIT_DIR}"

archive_path="${INSTALL_ROOT}/download/${ARCHIVE}"
log "downloading ${BASE_URL}/${ARCHIVE}"
curl -fsSL --retry 3 --retry-delay 5 -o "${archive_path}.part" "${BASE_URL}/${ARCHIVE}"
mv -f "${archive_path}.part" "${archive_path}"

computed="$(sha256sum "${archive_path}" | awk '{print $1}')"
expected=""
if [ -n "${AIPF_OLLAMA_SHA256:-}" ]; then
    expected="${AIPF_OLLAMA_SHA256}"
elif curl -fsSL --retry 2 --max-time 30 -o "${INSTALL_ROOT}/download/sha256sum.txt" \
    "${BASE_URL}/sha256sum.txt"; then
    expected="$(awk -v want="./${ARCHIVE}" '$2 == want {print $1}' \
        "${INSTALL_ROOT}/download/sha256sum.txt")"
fi

if [ -n "${expected}" ]; then
    [ "${computed}" = "${expected}" ] \
        || die "sha256 mismatch for ${ARCHIVE}: published ${expected}, got ${computed}"
    checksum_status="verified against published sha256sum.txt"
else
    checksum_status="published checksum unavailable; computed hash recorded"
    log "warning: ${checksum_status}"
fi
printf 'version=%s\nsha256=%s\nsource=%s/%s\n%s\n' \
    "${OLLAMA_VERSION}" "${computed}" "${BASE_URL}" "${ARCHIVE}" "${checksum_status}" \
    >"${INSTALL_ROOT}/package.txt"

log "extracting into ${INSTALL_ROOT}"
tar --zstd -xf "${archive_path}" -C "${INSTALL_ROOT}"
[ -x "${INSTALL_ROOT}/bin/ollama" ] || die "extraction did not produce bin/ollama"
"${INSTALL_ROOT}/bin/ollama" --version

# A user unit keeps the daemon inside this account: loopback only, a model
# directory under $HOME, one request and one loaded model at a time, and short
# context so it fits a small host.
cat >"${UNIT_PATH}" <<'UNIT'
[Unit]
Description=Local Ollama for ai-privacy-filter (user unit)
After=network-online.target

[Service]
Type=simple
ExecStart=%h/aipf-ollama/bin/ollama serve
Environment=OLLAMA_HOST=127.0.0.1:11434
Environment=OLLAMA_MODELS=%h/aipf-ollama/models
Environment=OLLAMA_NUM_PARALLEL=1
Environment=OLLAMA_MAX_LOADED_MODELS=1
Environment=OLLAMA_KEEP_ALIVE=10m
Environment=OLLAMA_CONTEXT_LENGTH=4096
Restart=on-failure
RestartSec=5
Nice=10

[Install]
WantedBy=default.target
UNIT

systemctl --user daemon-reload
systemctl --user enable --now "${UNIT_NAME}"

for _ in $(seq 1 60); do
    if curl -fsS --max-time 2 "http://${LOOPBACK}/api/version" >/dev/null 2>&1; then
        log "serving on http://${LOOPBACK} ($(systemctl --user is-active "${UNIT_NAME}"))"
        exit 0
    fi
    sleep 1
done

systemctl --user --no-pager status "${UNIT_NAME}" || true
die "unit did not answer on http://${LOOPBACK}/api/version within 60s"

#!/usr/bin/env bash
# Gate 1: prove a fresh local clone reproduces the committed results.
#
# The clone is made from this repository (no remote), checked out at the
# requested revision, and run with every AIPF_* variable removed from the
# environment.  The private environment file is never sourced.  Success means:
#
#   * ``make setup test reproduce`` exits 0;
#   * every hash in results/SHA256SUMS still verifies;
#   * ``make reproduce`` changed no tracked file under results/.
#
# Usage: scripts/clean_clone_check.sh [--rev <ref>] [--keep]
# Exit codes: 0 pass, 1 a check failed, 2 usage or dirty-source error.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REV="HEAD"
KEEP=0

usage() {
  sed -n '2,12p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --rev)
      [ "$#" -ge 2 ] || { usage >&2; exit 2; }
      REV="$2"
      shift 2
      ;;
    --keep)
      KEEP=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "clean_clone_check: unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if ! git -C "$ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "clean_clone_check: not a git repository: $ROOT" >&2
  exit 2
fi
if [ -n "$(git -C "$ROOT" status --porcelain --untracked-files=no)" ]; then
  echo "clean_clone_check: the source worktree has tracked changes; commit first" >&2
  git -C "$ROOT" status --short --untracked-files=no >&2
  exit 2
fi

COMMIT="$(git -C "$ROOT" rev-parse "$REV")"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/aipf-clean-clone.XXXXXX")"
CLONE="$WORK/repo"

cleanup() {
  if [ "$KEEP" = "1" ]; then
    echo "clean_clone_check: kept $WORK" >&2
  else
    rm -rf "$WORK"
  fi
}
trap cleanup EXIT

# Every AIPF_* value belongs to the private runtime.  The gate must pass
# without any of them, so remove them before invoking make.
while IFS= read -r name; do
  unset "$name"
done < <(compgen -e | grep '^AIPF_' || true)
unset AIPF_FORBIDDEN_FILE

echo "clean_clone_check: cloning $ROOT at $COMMIT"
git clone --quiet --no-local "$ROOT" "$CLONE"
git -C "$CLONE" checkout --quiet --detach "$COMMIT"

started="$(date +%s)"
echo "clean_clone_check: make setup test reproduce (no private environment)"
(
  cd "$CLONE"
  make setup
  make test
  make reproduce
)
elapsed="$(( $(date +%s) - started ))"

# 1. Every listed artifact matches its committed hash.
(
  cd "$CLONE/results"
  sha256sum -c SHA256SUMS
)

# 2. Reproduction is byte-for-byte idempotent for every tracked result file.
if ! git -C "$CLONE" diff --exit-code -- results >/dev/null; then
  echo "clean_clone_check: FAIL: make reproduce changed tracked results" >&2
  git -C "$CLONE" diff --stat -- results >&2
  exit 1
fi

# 3. The clone's SHA256SUMS is the committed one, not a locally rewritten file.
if ! cmp -s "$CLONE/results/SHA256SUMS" <(git -C "$CLONE" show "$COMMIT:results/SHA256SUMS"); then
  echo "clean_clone_check: FAIL: results/SHA256SUMS differs from the committed file" >&2
  exit 1
fi

echo "clean_clone_check: PASS in ${elapsed}s: results/SHA256SUMS identical, no tracked result changed"

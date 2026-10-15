#!/usr/bin/env bash
set -Eeuo pipefail
# Run a snapshot in memory because Git may replace this script during the update.
if [[ ${SHOWCAL_UPDATE_SNAPSHOT:-} != 1 ]]; then
  export SHOWCAL_UPDATE_SNAPSHOT=1
  exec bash -c "$(cat -- "$0")" "$0" "$@"
fi
if [[ "${1:-}" == --help ]]; then
  printf '%s\n' 'Usage: ./scripts/update-lxc.sh' 'Backs up the database/configuration, fast-forwards main, installs dependencies, migrates and restarts systemd services.'
  exit 0
fi
source "$(dirname -- "$0")/common.sh"
[[ $# == 0 ]] || fail 'No arguments supported.'
require_tools
[[ -f .deploy/installed && -f /var/lib/showcal/showcal.sqlite3 ]] || fail 'No completed native installation found.'
[[ "$(git rev-parse --show-toplevel)" == "$ROOT" ]] || fail 'Run from the Showcal Git checkout.'
[[ "$(git branch --show-current)" == main ]] || fail 'Checkout main before updating.'
[[ -z "$(git status --porcelain)" ]] || fail 'Working tree has changes; commit or move them before updating.'
load_config
git fetch origin main
git merge-base --is-ancestor HEAD origin/main || fail 'main has local-only commits; resolve divergence first.'
BACKUP_DIR="$ROOT/backups/$(date -u +%Y%m%dT%H%M%SZ)-$$"
mkdir -p "$BACKUP_DIR"
git rev-parse HEAD > "$BACKUP_DIR/revision-before.txt"
cp .env "$BACKUP_DIR/config.env"
trap 'systemctl stop showcal showcal-worker 2>/dev/null || true; printf "Update stopped; services remain stopped. Backup: %s. See docs/DEPLOYMENT.md for recovery.\n" "$BACKUP_DIR" >&2' ERR
systemctl stop showcal showcal-worker
"$PYTHON_BIN" scripts/native.py backup "$BACKUP_DIR/database.sqlite3"
git merge --ff-only origin/main
git rev-parse HEAD > "$BACKUP_DIR/revision-after.txt"
source "$ROOT/scripts/common.sh"
load_config
build_environment
start_services
printf '\nUpdate complete. Backup: %s\n' "$BACKUP_DIR"

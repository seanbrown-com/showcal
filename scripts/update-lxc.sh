#!/usr/bin/env bash
set -Eeuo pipefail
# Keep the running updater in memory while Git replaces files in this checkout.
if [[ ${SHOWCAL_UPDATE_SNAPSHOT:-} != 1 ]]; then
  export SHOWCAL_UPDATE_SNAPSHOT=1
  exec bash -c "$(cat -- "$0")" "$0" "$@"
fi
if [[ "${1:-}" == --help ]]; then
  printf '%s\n' 'Usage: ./scripts/update-lxc.sh' 'Fast-forwards main from origin, builds images, backs up SQLite and .env, migrates and restarts. Refuses local changes.'
  exit 0
fi
source "$(dirname -- "$0")/common.sh"
[[ $# == 0 ]] || fail 'No arguments supported; see --help.'
require_tools
[[ -f .env ]] || fail 'No .env found. Run install-lxc.sh first.'
[[ "$(git rev-parse --show-toplevel)" == "$ROOT" ]] || fail 'Run from the root of a Showcal Git checkout.'
[[ "$(git branch --show-current)" == main ]] || fail 'Checkout main before updating.'
[[ -z "$(git status --porcelain)" ]] || fail 'Working tree has changes. Commit or move them before updating; nothing was overwritten.'
git remote get-url origin >/dev/null || fail 'Add your Git repository as origin before updating.'
load_compose
PREVIOUS_REVISION="$(git rev-parse HEAD)"
printf 'Fetching origin/main…\n'
git fetch origin main
# Refuse divergence and local-only commits, even if a merge would be fast-forward.
git merge-base --is-ancestor HEAD origin/main || fail 'main contains commits not on origin/main. Resolve this manually.'
git merge --ff-only origin/main
# Reload helpers from the new revision before deployment.
source "$ROOT/scripts/common.sh"
load_compose
BACKUP_DIR="$ROOT/backups/$(date -u +%Y%m%dT%H%M%SZ)-$$"
mkdir -p "$BACKUP_DIR"
printf '%s\n' "$PREVIOUS_REVISION" > "$BACKUP_DIR/revision-before.txt"
git rev-parse HEAD > "$BACKUP_DIR/revision-after.txt"
cp .env "$BACKUP_DIR/config.env"
trap 'printf "Update stopped. Backup directory: %s. Source may be newer than running containers. Do not restart against a failed migration without reviewing docs/DEPLOYMENT.md.\n" "$BACKUP_DIR" >&2' ERR
# Build while the previous containers still serve traffic.
"${COMPOSE[@]}" build --pull
"${COMPOSE[@]}" stop web worker
"${COMPOSE[@]}" run --rm --no-deps -T web python -c '
import os, pathlib, sqlite3, sys
path = pathlib.Path(os.environ["DATABASE_PATH"])
if not path.is_file():
    raise SystemExit("No database found to back up; use the installer for a new deployment.")
source = sqlite3.connect("file:"+str(path)+"?mode=ro", uri=True)
destination = sqlite3.connect("/tmp/showcal-backup.sqlite3")
with destination:
    source.backup(destination)
result = destination.execute("PRAGMA integrity_check").fetchone()[0]
source.close(); destination.close()
if result != "ok":
    raise SystemExit("SQLite backup failed its integrity check.")
sys.stdout.buffer.write(pathlib.Path("/tmp/showcal-backup.sqlite3").read_bytes())
' > "$BACKUP_DIR/database.sqlite3.partial"
[[ -s "$BACKUP_DIR/database.sqlite3.partial" ]] || fail 'Database backup was empty; services remain stopped.'
mv "$BACKUP_DIR/database.sqlite3.partial" "$BACKUP_DIR/database.sqlite3"
start_services
touch .deploy/installed
printf '\nUpdate complete. Backup: %s\n' "$BACKUP_DIR"

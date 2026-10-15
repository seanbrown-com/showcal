#!/usr/bin/env bash
set -Eeuo pipefail
if [[ "${1:-}" == --help ]]; then
  printf '%s\n' 'Usage: ./scripts/install-lxc.sh --url URL [--bind IP] [--port 8003] [--import-db PATH --import-env PATH]' 'Requires root, systemd and Python 3.11+. Existing .env is preserved when no configuration arguments are supplied.'
  exit 0
fi
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
require_tools
[[ ! -f .deploy/installed && ! -e /var/lib/showcal/showcal.sqlite3 ]] || fail 'Existing installation/data found; use update-lxc.sh. See deployment guide for failed-install recovery.'
[[ ! -e /etc/systemd/system/showcal.service && ! -e /etc/systemd/system/showcal-worker.service ]] || fail 'Showcal service units already exist; inspect them before installing.'
IMPORT_DB=''
IMPORT_ENV=''
CONFIG_ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --import-db) [[ $# -ge 2 ]] || fail 'Missing import database path'; IMPORT_DB="$2"; shift 2 ;;
    --import-env) [[ $# -ge 2 ]] || fail 'Missing import configuration path'; IMPORT_ENV="$2"; CONFIG_ARGS+=("$1" "$2"); shift 2 ;;
    --url|--bind|--port) [[ $# -ge 2 ]] || fail 'Missing configuration value'; CONFIG_ARGS+=("$1" "$2"); shift 2 ;;
    *) fail "Unknown argument: $1" ;;
  esac
done
if [[ -n "$IMPORT_DB" ]]; then
  [[ -f "$IMPORT_DB" && -f "$IMPORT_ENV" ]] || fail 'Import requires both --import-db and --import-env to preserve the encryption key.'
fi
if [[ -f .env ]]; then
  [[ ${#CONFIG_ARGS[@]} == 0 ]] || fail 'Existing .env preserved. Edit it and rerun without configuration arguments, or move it aside before importing.'
else
  "$PYTHON_BIN" scripts/configure.py create "${CONFIG_ARGS[@]}"
fi
prepare_user
load_config
"$PYTHON_BIN" scripts/native.py port
trap 'systemctl stop showcal showcal-worker 2>/dev/null || true; printf "Installation stopped. Configuration and data were preserved; see docs/DEPLOYMENT.md.\n" >&2' ERR
build_environment
if [[ -n "$IMPORT_DB" ]]; then
  .venv-server/bin/python scripts/native.py import "$IMPORT_DB"
  mkdir -p backups/import-original
  cp -- "$IMPORT_ENV" backups/import-original/config.env
  "$PYTHON_BIN" scripts/native.py backup "$ROOT/backups/import-original/database.sqlite3"
fi
start_services
touch .deploy/installed
printf '\nInstallation complete. Imported users can sign in with their existing passwords.\n'

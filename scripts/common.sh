#!/usr/bin/env bash
# Shared by the installer/updater. Do not source .env: Compose reads it as data.
set -Eeuo pipefail
umask 077
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
fail() { printf 'Error: %s\n' "$*" >&2; exit 1; }
require_tools() {
  [[ "$(uname -s)" == Linux ]] || fail 'Run this deployment script inside your Linux LXC.'
  for tool in docker git python3 flock; do
    command -v "$tool" >/dev/null || fail "Missing $tool. See docs/DEPLOYMENT.md."
  done
  docker compose version >/dev/null || fail 'Docker Compose v2 is required.'
  docker info >/dev/null 2>&1 || fail 'Docker is not running or this user cannot access it.'
  mkdir -p .deploy
  exec 9>.deploy/operation.lock
  flock -n 9 || fail 'Another installation/update is already running.'
}
load_compose() {
  python3 scripts/configure.py validate
  chmod 600 .env
  PROJECT="$(python3 scripts/configure.py project)"
  if [[ "$PROJECT" == showcal ]] && docker volume inspect ticket-watch_ticket-data >/dev/null 2>&1; then
    fail 'Legacy Ticket Watch data found. Set COMPOSE_PROJECT_NAME=ticket-watch and DATABASE_FILENAME=ticket-watch.sqlite3 in .env to reuse it; see docs/DEPLOYMENT.md.'
  fi
  COMPOSE=(docker compose --project-name "$PROJECT" --env-file "$ROOT/.env" -f "$ROOT/compose.yaml")
  "${COMPOSE[@]}" config --quiet
}
start_services() {
  "${COMPOSE[@]}" run --rm --no-deps migrate
  "${COMPOSE[@]}" up -d --no-deps --wait --wait-timeout 180 web worker
  "${COMPOSE[@]}" ps
  printf '\nShowcal: %s\n' "$(python3 scripts/configure.py url)"
}

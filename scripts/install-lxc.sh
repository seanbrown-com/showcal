#!/usr/bin/env bash
set -Eeuo pipefail
if [[ "${1:-}" == --help ]]; then
  printf '%s\n' 'Usage: ./scripts/install-lxc.sh --url https://tickets.example.com [--bind 0.0.0.0] [--port 8000]' 'Requires Git, Python 3, Docker Engine and Compose v2 inside your LXC. Existing .env is preserved.'
  exit 0
fi
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
require_tools
[[ ! -f .deploy/installed ]] || fail 'Already installed. Use ./scripts/update-lxc.sh.'
if [[ ! -f .env ]]; then
  python3 scripts/configure.py create "$@"
elif [[ $# -gt 0 ]]; then
  fail 'An .env already exists. Edit it, then run this script without arguments; it will not be overwritten.'
fi
load_compose
# Do not run a first-install migration over an existing production database.
if docker volume inspect "${PROJECT}_ticket-data" >/dev/null 2>&1; then
  fail 'An existing showcal data volume was found. Use update-lxc.sh to back up and upgrade it.'
fi
trap 'printf "Installation stopped. Configuration and any created volume were preserved. See docs/DEPLOYMENT.md for recovery.\n" >&2' ERR
"${COMPOSE[@]}" build --pull
start_services
touch .deploy/installed
printf '\nAdd Ticketmaster configuration to .env, then run ./scripts/update-lxc.sh to apply them. Create your account in the web app.\n'

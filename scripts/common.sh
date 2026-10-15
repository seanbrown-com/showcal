#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PYTHON_BIN="${PYTHON_BIN:-python3}"
fail() { printf 'Error: %s\n' "$*" >&2; exit 1; }
require_tools() {
  [[ "$(uname -s)" == Linux && "$(id -u)" == 0 ]] || fail 'Run as root inside your Linux LXC.'
  for tool in git flock systemctl runuser useradd "$PYTHON_BIN"; do
    command -v "$tool" >/dev/null || fail "Missing $tool; see docs/DEPLOYMENT.md."
  done
  systemctl show --property=Version --value >/dev/null || fail 'systemd must be running.'
  "$PYTHON_BIN" -c 'import sys; assert sys.version_info >= (3,11), "Python 3.11+ required"'
  [[ "$ROOT" == /opt/* && "$ROOT" =~ ^[/a-zA-Z0-9_.-]+$ ]] || fail 'Install under /opt using a path without spaces.'
  mkdir -p .deploy
  exec 9>.deploy/operation.lock
  flock -n 9 || fail 'Another installation/update is running.'
}
prepare_user() {
  if ! id showcal >/dev/null 2>&1; then
    useradd --system --user-group --home-dir /var/lib/showcal --shell /usr/sbin/nologin showcal
  fi
  [[ "$(id -u showcal)" != 0 ]] || fail 'The showcal service user must not be root.'
  install -d -o showcal -g showcal -m 700 /var/lib/showcal
}
load_config() {
  "$PYTHON_BIN" scripts/configure.py validate
  chown root:showcal .env
  chmod 640 .env
}
build_environment() {
  "$PYTHON_BIN" -m venv .venv-server
  .venv-server/bin/python -m pip install -r requirements.lock.txt
  # The service user needs read/execute access, never write access to application code.
  chmod -R a+rX .venv-server
  find static templates concerts config scripts -type d -exec chmod a+rx {} +
  find static templates concerts config scripts -type f -exec chmod a+r {} +
  chmod a+rx "$ROOT"
  chmod a+r manage.py
}
manage() {
  runuser -u showcal -- "$ROOT/.venv-server/bin/python" "$ROOT/scripts/run_service.py" manage "$@"
}
start_services() {
  manage migrate --noinput
  # Static assets are deployer-owned and read-only to the service user.
  .venv-server/bin/python scripts/run_service.py manage collectstatic --noinput
  chmod -R a+rX staticfiles
  "$PYTHON_BIN" scripts/native.py clear-lease
  "$PYTHON_BIN" scripts/native.py units
  systemctl daemon-reload
  systemctl enable showcal showcal-worker
  systemctl restart showcal showcal-worker
  "$PYTHON_BIN" scripts/native.py health
  printf '\nShowcal: %s\n' "$("$PYTHON_BIN" scripts/configure.py url)"
}

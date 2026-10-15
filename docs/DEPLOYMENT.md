# Deploy Showcal with systemd in your Proxmox LXC

Showcal runs directly in your utilities CT, alongside your other services. Run server commands as root **inside the LXC**, not on the Proxmox host. No Docker setup is needed. The scripts create only Showcal’s user, data directory and two services; they do not change other apps or your proxy.

## 1. Push from your Mac

```sh
cd ~/Documents/git/seanbrown-com/showcal
git add -A
git commit -m "Deploy Showcal with systemd"
git push origin main
```

## 2. Prepare the LXC

Debian 12+ or Ubuntu 24.04+, Python 3.11+, and a working systemd instance are required. The installer checks Python rather than replacing the system interpreter. If you use a separately installed compatible Python, set `PYTHON_BIN=/path/to/python3.13` for both installation and updates; that interpreter needs venv support.

```sh
apt-get update
apt-get install -y git python3 python3-venv ca-certificates util-linux
python3 --version
systemctl --version
git clone --branch main https://github.com/seanbrown-com/showcal.git /opt/showcal
cd /opt/showcal
```

A private repository requires GitHub credentials or an SSH deploy key. Clone as root so later root-run updates use the same Git ownership and authentication. Do not copy your Mac’s `.venv` or run the installer from a home directory; the services cannot access home directories.

The default port is **8003** (Bookkeeper uses 8000, Landing-page 8001, Coin-watch 8002 in their installers). Actual running ports may differ. Showcal checks that its selected address/port is free before installing. Use `--port 8004` and update the direct-access URL if needed.

## 3. Export and copy your local data (optional)

Skip this section for a completely fresh account. For your existing account, favorites, imported concerts, Jellyfin connection and calendar subscription, move both the database and its matching configuration.

On your Mac, stop the local web server and worker, then:

```sh
cd ~/Documents/git/seanbrown-com/showcal
.venv/bin/python scripts/export-local.py
```

This creates a timestamped directory under `backups/` containing `database.sqlite3` and `config.env`. It uses SQLite’s backup API, checks database integrity and verifies that the encryption key can decrypt saved Jellyfin tokens. Changes after this snapshot will not transfer. The files contain private data and credentials and are ignored by Git.

Replace `LXC_IP` with your actual container address and `TIMESTAMP` with the directory printed by the export command. These examples assume root SSH access; otherwise transfer as your SSH user and move the files as root inside the LXC.

```sh
ssh root@LXC_IP 'umask 077; mkdir -p /root/showcal-transfer'
scp backups/lxc-transfer-TIMESTAMP/{database.sqlite3,config.env} root@LXC_IP:/root/showcal-transfer/
```

## 4. Install

For initial LAN access, inside the LXC:

```sh
cd /opt/showcal
./scripts/install-lxc.sh \
  --url http://LXC_IP:8003 --bind 0.0.0.0 \
  --import-db /root/showcal-transfer/database.sqlite3 \
  --import-env /root/showcal-transfer/config.env
```

Omit both import options for a fresh installation. For HTTPS behind your existing reverse proxy, use `--url https://shows.eurekabeacon.com` (only after configuring that hostname). Keep `--bind 0.0.0.0` if the proxy is on another machine; omit it if the proxy is in this CT. Proxy to the CT’s selected port, preserve the Host header and terminate HTTPS at the proxy. Restrict the app port to your LAN/proxy. The script does not configure DNS or certificates.

The installer:

- Creates a dedicated `showcal` system user and root-owned `.venv-server` virtual environment.
- Generates production configuration. With `--import-env`, it preserves the original encryption key, Django secret and API keys while generating the server URL, hosts, port and cookie settings for this installation.
- Checks the listening port, installs pinned dependencies and validates the imported database/key pair.
- Copies the database into `/var/lib/showcal/showcal.sqlite3`, preserves a pre-migration import backup, applies migrations and collects static files.
- Installs and enables `showcal.service` (Gunicorn) and `showcal-worker.service` (calendar/Jellyfin refreshes), then checks HTTP health and the worker heartbeat.

It refuses existing native data or service units. It also preserves an existing `.env`; if one exists, edit it and run without configuration arguments. To use the import workflow, move an old `.env` aside first so the installer can generate a clean deployment configuration. Never regenerate the encryption key for an existing database.

Open the configured address and log in with your existing credentials. Check Artists and Settings → Jellyfin. The **Subscribe to calendar** button on Discover provides the new device URL. Calendar tokens and event UIDs survive the move, but devices subscribed to localhost need the new server address. Google Calendar subscriptions require a publicly reachable URL; LAN-only addresses work only for clients that can reach your LAN.

Keep the Mac worker stopped after cutover so only the LXC refreshes integrations.

## Configuration and daily operation

| Item | Location |
| --- | --- |
| Git checkout | `/opt/showcal` |
| Private configuration | `/opt/showcal/.env` (root:showcal, mode 640) |
| Python environment | `/opt/showcal/.venv-server` |
| Persistent database | `/var/lib/showcal/showcal.sqlite3` |
| Update backups | `/opt/showcal/backups/<timestamp>-<pid>/` |
| Imported original backup | `/opt/showcal/backups/import-original/` |
| Unit files | `/etc/systemd/system/showcal.service`, `showcal-worker.service` |

The service runner fixes the database path to `/var/lib/showcal/showcal.sqlite3`; a legacy `DATABASE_PATH` setting is ignored in production. Code is read-only to the services; only the data directory is writable. The `.env` file is parsed by Python, not sourced as shell code. Gunicorn request access logging is disabled so private calendar tokens are not routinely written to its access log; configure your reverse proxy’s logging appropriately too.

```sh
systemctl status showcal showcal-worker
journalctl -u showcal -u showcal-worker -n 100 --no-pager
systemctl restart showcal showcal-worker
```

To change the domain, edit `APP_URL`, `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, and `COOKIE_SECURE` together. HTTPS uses `COOKIE_SECURE=true`; private-LAN HTTP uses `false`. `PORT` controls the listener; `BIND_ADDRESS` controls its interface. Preserve `SECRET_KEY` and `CREDENTIAL_KEY`. Restart both services after configuration changes.

To change a password:

```sh
cd /opt/showcal
runuser -u showcal -- .venv-server/bin/python scripts/run_service.py manage changepassword USERNAME
```

## Updates

After committing and pushing new code from your Mac:

```sh
cd /opt/showcal
./scripts/update-lxc.sh
```

The updater requires a completed native installation and a clean `main` checkout. It fetches `origin/main` and refuses divergent/local-only commits before stopping services. It then stops both services, creates an integrity-checked SQLite backup and saves `.env` plus the old revision, fast-forwards Git, installs dependencies, migrates, collects static files and restarts/checks health. It clears any stale worker lease while services are stopped. Configuration is preserved. There is downtime while dependencies and migrations run. No backups are automatically deleted; keep off-machine copies.

If backup fails, the updater does not merge or migrate. If a later step fails, services remain stopped and the backup location is printed. Do not blindly restart older code against a newer database.

## Recovery

For a failed first installation, inspect the error and `journalctl` first. Fix the cause (such as a dependency download failure). The installer refuses a database that already exists. If import failed before copying data, fix the configuration/key pair and retry from a clean deployment configuration while preserving your original transfer files. If data was successfully imported or migrated, keep it and complete deployment as root:

```sh
cd /opt/showcal
source scripts/common.sh
require_tools
prepare_user
load_config
build_environment
start_services
touch .deploy/installed
```

Run recovery commands one at a time and stop if any command fails. These commands intentionally use the current checkout and existing database. They do not import again. If you have important data, make a separate backup before another migration attempt.

For an update rollback: stop both services, preserve the failed database/configuration separately, restore the backup’s `database.sqlite3` to `/var/lib/showcal/showcal.sqlite3` and `config.env` to `.env`, then check out the revision recorded in `revision-before.txt`. Move any SQLite `-wal`/`-shm` files aside with the failed database while all services are stopped. Restore database ownership to `showcal:showcal` and mode 600, and configuration to `root:showcal` mode 640. Rebuild the virtual environment from that revision’s lockfile, collect static files and reinstall/start that revision’s units. If the previous revision used Docker, follow its own deployment instructions instead. A rollback is manual and must restore matching code, database and encryption key.

## If you already installed the Docker version

Do not run this installer on top of an active Docker deployment. Stop its web/worker containers and export a consistent SQLite backup from its volume along with the matching `.env`. Use the native import options with those files; Docker data is not automatically discovered or deleted. Keep the old volume until the native deployment is verified. The current repository no longer includes Compose files, so retain the old deployment checkout/configuration for managing those containers.

# Deploy Showcal from Git to a Proxmox LXC

Run these commands inside the Linux LXC, not on the Proxmox host. Use the same checkout and Compose project for later updates. The scripts do not modify Proxmox security settings or install/replace Docker.

## Publish to GitHub

Create an empty repository named `showcal`, then run on your Mac:

```sh
cd ~/Documents/git/seanbrown-com/showcal
git add .
git diff --cached --stat
git commit -m "Initial Showcal concert calendar"
git remote add origin YOUR_GIT_REPO_URL
git push -u origin main
```

If an `origin` remote already exists, use `git remote set-url origin YOUR_GIT_REPO_URL` instead. Use your real SSH or HTTPS repository URL without embedded access tokens. `.env`, databases, backups, browser diagnostics and generated files are ignored. No open-source license has been selected automatically.

## Prepare the LXC

As root inside Debian/Ubuntu:

```sh
apt-get update
apt-get install -y git python3 ca-certificates curl util-linux
```

Install Docker Engine and the Compose v2 plugin using Docker's instructions for [Debian](https://docs.docker.com/engine/install/debian/) or [Ubuntu](https://docs.docker.com/engine/install/ubuntu/). Confirm Docker works in your guest before installing the app. Nested containers require suitable host/kernel configuration; use a VM if your LXC cannot run Docker.

```sh
docker info
docker compose version
git clone --branch main YOUR_GIT_REPO_URL /opt/showcal
cd /opt/showcal
```

A private repository requires Git credentials or a read-only deploy key in the LXC.

## Install

For a public HTTPS hostname behind a reverse proxy on another machine:

```sh
./scripts/install-lxc.sh --url https://shows.example.com --bind 0.0.0.0
```

Replace the hostname with one you have configured. Point its proxy at the LXC's LAN IP on port 8000, preserve the Host header and terminate HTTPS there. The installer does not create DNS records or certificates. Keep the published port restricted to the LAN/proxy.

For initial private-LAN testing, use your LXC address:

```sh
./scripts/install-lxc.sh --url http://192.168.1.50:8000 --bind 0.0.0.0
```

Default binding is `127.0.0.1`, suitable for a proxy on the same LXC. `--port 8080` changes the published port; adjust the URL when connecting directly.

The installer generates unique `SECRET_KEY` and `CREDENTIAL_KEY` values in a permission-600 `.env`, builds the image, migrates the persistent database and starts web/worker. Existing `.env` files are never overwritten: edit your existing configuration, then run the installer without arguments. A first installation refuses an existing volume to avoid migrating it without backup.

Add `TICKETMASTER_API_KEY` to `.env`, then run the updater to apply configuration changes. `BRAVE_API_KEY` is optional for Facebook searches. Create your account in the app and connect Jellyfin under Settings. SMTP, browser downloads and inventory feeds are no longer used.

## Updates and backups

```sh
cd /opt/showcal
./scripts/update-lxc.sh
```

Updates fetch `origin/main` and fast-forward only. Dirty checkouts, divergence and local-only commits are rejected. Images build while existing containers serve traffic. The updater then stops web/worker, backs up SQLite with an integrity check, saves `.env` and revision IDs under `backups/<UTC timestamp>-<PID>/`, migrates and waits for service health. Backups contain private configuration; keep off-machine copies. No backups are automatically deleted.

If a build fails, existing containers remain running, although the source checkout may be newer. If backup/migration fails after services stop, they remain stopped for inspection. Do not blindly restart old code against a changed database. To roll back, preserve a separate copy of the current database, restore both the backup database and matching `.env`, and deploy `revision-before.txt`. Restore database ownership to container UID 10001.

The default Compose project is `showcal`, its volume is `showcal_ticket-data`, and the database path is `/data/showcal.sqlite3`. Never use `docker compose down -v` to update; that deletes persistent data.

```sh
docker compose -p showcal ps
docker compose -p showcal logs --tail=100 web worker
docker compose -p showcal exec web python manage.py changepassword USERNAME
```

After a failed first install that already created a volume, inspect the error and retry manually:

```sh
docker compose -p showcal build
docker compose -p showcal run --rm --no-deps migrate
docker compose -p showcal up -d --no-deps --wait web worker
```

## Upgrade an existing Ticket Watch Docker installation

Preserve the existing volume and stop the old worker through the updater. In the existing `.env`, set:

```dotenv
COMPOSE_PROJECT_NAME=ticket-watch
DATABASE_FILENAME=ticket-watch.sqlite3
```

Then use `./scripts/update-lxc.sh` from the renamed checkout. These legacy storage names intentionally remain unchanged so accounts, favorites and calendar tokens survive. Use `docker compose -p ticket-watch` for manual operations on this installation. The scripts refuse to start a new `showcal` deployment when they detect the legacy volume, rather than silently presenting an empty database.

The removal migration drops price-watch, alert and browser-snapshot tables, price-range fields and the price-email preference. The updater backs them up first. Remove obsolete `SMTP_*`, `INVENTORY_*`, `BROWSER_*` and `POLL_SECONDS` settings when convenient; Showcal ignores them. The optional browser Compose file and image are no longer used.

## Move existing localhost data

A Git clone starts with an empty database. Accounts, favorites, imported events and calendar subscriptions are not Git content.

Stop local web/worker processes, use Python's `sqlite3.Connection.backup()` to create a consistent database copy, and securely transfer that copy and `.env` to the LXC. Preserve `CREDENTIAL_KEY`, update `APP_URL`, `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, `DEBUG=false` and cookie settings for deployment. Restore the database into the chosen volume/path as UID 10001 while web/worker are stopped, then migrate/start.

Existing calendar tokens and event UIDs are retained. Legacy `@ticket-watch` event UIDs intentionally stay stable to avoid duplicate subscribed events. If the server address changes, resubscribe devices using the new feed URL. Never commit a database or `.env`.

# Showcal

A self-hosted concert discovery and calendar app for systemd in a Proxmox LXC. Find nearby shows by your favorite artists, import artists from Jellyfin, and subscribe to a private six-month concert calendar.

## Features

- Accounts with ZIP code and a configurable 1–250-mile radius (50 miles by default).
- Ticketmaster concert discovery with compact dated cards and calendar-month browsing. “My artists this month” is the default; switch to All concerts when needed.
- Manually selected favorite artists and weekly Jellyfin artist imports. Removed favorites stay removed.
- A private calendar subscription covering the next six months, refreshed daily by the worker. Subscription links can be replaced or disabled.
- Individual ICS downloads and Google Calendar links, with venue-local dates and times.
- Links to ticket providers and Facebook event searches.
- Individually imported AXS shows included when venue coordinates establish that they are within your radius.

Showcal does **not** monitor ticket prices, scrape inventory, set ticket budgets or send price-alert emails. Ticket purchases and price checks happen on the provider's website. Automatic AXS discovery is not implemented. Discovery coverage depends on the provider and is limited to 1,000 results per month. Distances are straight-line estimates from ZIP centroids; event end times are estimates.

## Install on your LXC

Run as root inside Debian 12+/Ubuntu 24.04+ with systemd and Python 3.11+. No Docker is required. The native deployment uses a dedicated user, virtual environment and separate web/worker services. Port 8003 avoids the default ports of Bookkeeper, Landing-page and Coin-watch; the installer checks availability.

```sh
apt-get update
apt-get install -y git python3 python3-venv ca-certificates util-linux
git clone --branch main https://github.com/seanbrown-com/showcal.git /opt/showcal
cd /opt/showcal
./scripts/install-lxc.sh --url http://YOUR_LXC_IP:8003 --bind 0.0.0.0
```

For a configured HTTPS reverse proxy, use `--url https://shows.example.com`. The installer does not configure DNS, certificates or the proxy. Use `--bind 0.0.0.0` when your proxy is on another machine; otherwise the default is loopback. Use `--port` to change the listening port.

**Bringing your local data?** Add `--import-db /root/showcal-transfer/database.sqlite3 --import-env /root/showcal-transfer/config.env` to the installation command. Do this on the first installation, before creating another account. See the [step-by-step deployment and transfer guide](docs/DEPLOYMENT.md).

Configuration stays in `/opt/showcal/.env`; the database lives at `/var/lib/showcal/showcal.sqlite3`. Preserve `CREDENTIAL_KEY` to keep Jellyfin connections readable. The service runner loads `.env` as data without executing shell expressions. Background work runs in `showcal-worker.service`.

For updates after pushing to `main`:

```sh
cd /opt/showcal
./scripts/update-lxc.sh
```

The updater requires a clean `main` checkout. It stops services, backs up SQLite and configuration, fast-forwards from `origin/main`, installs dependencies, migrates and restarts. Updates involve downtime. It leaves services stopped if deployment fails; see the recovery guide before restarting.

## Local development

Python 3.11+:

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.lock.txt
.venv/bin/python manage.py migrate
.venv/bin/python manage.py runserver 127.0.0.1:8000
```

In another terminal, start `.venv/bin/python manage.py worker`. For local development, Django does not load `.env` automatically; export needed configuration into both processes. Production systemd services load it through `scripts/run_service.py`. Keep your existing `CREDENTIAL_KEY` when moving or renaming the project, so saved Jellyfin tokens remain readable.

Use **Subscribe to calendar** at the top of Discover, or open `/calendar/`. `APP_URL` must be reachable from subscribing devices; localhost works only on the same computer. Google Calendar needs a public URL. Calendar clients control their refresh timing.

## Checks

```sh
.venv/bin/python manage.py test
.venv/bin/python manage.py check
.venv/bin/python manage.py makemigrations --check --dry-run
.venv/bin/python -m unittest discover -s scripts -p 'test_*.py'
bash -n scripts/common.sh scripts/install-lxc.sh scripts/update-lxc.sh
```

GitHub Actions runs application and deployment checks on Python 3.11 and 3.13. Integration details are in [INTEGRATIONS.md](docs/INTEGRATIONS.md). This personal app has no email verification or self-service password reset; restrict access to trusted users.

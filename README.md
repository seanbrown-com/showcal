# Showcal

A self-hosted concert discovery and calendar app for Docker in a Proxmox LXC. Find nearby shows by your favorite artists, import artists from Jellyfin, and subscribe to a private six-month concert calendar.

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

Requires Docker Engine, Docker Compose v2, Git, Python 3, Bash and `flock` inside a Docker-capable Linux LXC.

```sh
git clone --branch main YOUR_GIT_REPO_URL /opt/showcal
cd /opt/showcal
./scripts/install-lxc.sh --url https://shows.example.com --bind 0.0.0.0
```

Use your actual configured hostname. The installer generates private configuration and keys, builds the app, migrates SQLite in a persistent Docker volume, starts web/worker and checks health. Configure `TICKETMASTER_API_KEY` in `.env`; connect Jellyfin through Settings. SMTP and Chromium are not required.

To update from `origin/main`:

```sh
cd /opt/showcal
./scripts/update-lxc.sh
```

The updater requires a clean `main` checkout, preserves `.env`, backs up the database, applies migrations and restarts services. See the [deployment guide](docs/DEPLOYMENT.md) for Git publishing, reverse proxies, backups, localhost data transfer and upgrades from Ticket Watch.

## Local development

Python 3.13+:

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.lock.txt
.venv/bin/python manage.py migrate
.venv/bin/python manage.py runserver 127.0.0.1:8000
```

In another terminal, start `.venv/bin/python manage.py worker`. Django does not load `.env` automatically outside Docker Compose; export needed configuration into both processes. Keep your existing `CREDENTIAL_KEY` when moving or renaming the project, so saved Jellyfin tokens remain readable.

Calendar subscriptions are under **Settings → Your concert calendar**, or `/calendar/`. `APP_URL` must be reachable from subscribing devices; localhost works only on the same computer. Google Calendar needs a public URL. Calendar clients control their refresh timing.

## Checks

```sh
.venv/bin/python manage.py test
.venv/bin/python manage.py check
.venv/bin/python manage.py makemigrations --check --dry-run
.venv/bin/python -m unittest discover -s scripts -p 'test_*.py'
bash -n scripts/common.sh scripts/install-lxc.sh scripts/update-lxc.sh
```

GitHub Actions runs checks and builds the application image. Integration details are in [INTEGRATIONS.md](docs/INTEGRATIONS.md). This personal app has no email verification or self-service password reset; restrict access to trusted users.

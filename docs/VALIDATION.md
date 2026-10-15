# Showcal validation — 2026-10-14

- 51 Django tests passed, covering discovery, favorites, Jellyfin connections, calendar subscriptions, worker coordination, and removal of price-monitoring routes and controls.
- Seven deployment tests passed, covering configuration, update ordering, database backups, dirty-checkout refusal and migration failures.
- Django migration consistency, installer/updater Bash syntax and Docker Compose configuration checks passed.
- The local database was backed up before applying `0007_remove_price_monitoring`. Account, profile, artist, event and subscription records were compared before and after migration, excluding intentionally removed price fields and the email preference. All retained values matched; SQLite integrity checks passed.
- Calendar subscription tokens and legacy event UIDs remain unchanged to avoid disrupting existing subscriptions.
- Ticket-price collection, budgets, email alerts and Chromium worker components have been removed. The remaining worker refreshes calendars daily and Jellyfin artists weekly.

A complete Docker image build/container health check and installation on Proxmox LXC remain unverified. The previous build attempt failed retrieving the Python base image from Docker Hub. Live external integrations and calendar subscriptions on physical devices were not retested during this change. Deployment tests use controlled fixtures rather than the remote LXC.

# Showcal validation — native systemd deployment

- 51 Django tests passed; Django system checks and migration consistency checks passed.
- 16 deployment tests passed. They cover configuration preservation and private permissions, non-execution of configuration values, SQLite backup integrity, overwrite refusal, matching Jellyfin encryption keys, occupied ports, unprivileged unit definitions, successful data-import flow, incomplete-transfer refusal, Git updates, dirty checkouts, backup failures and migration failures.
- Installer/updater Bash syntax checks and Git whitespace checks passed.
- The deployment-flow tests use real temporary Git repositories and SQLite databases while replacing host administration and dependency installation with controlled fixtures. They do not modify systemd, user accounts or production data on the development computer.
- CI is configured to run application and deployment tests on Python 3.11 and 3.13. Local tests ran using the existing Python 3.14 environment; the new CI matrix has not been run here.
- The native installation has not yet been executed on the user's Proxmox LXC. Live systemd sandbox compatibility, dependency downloads and remote service health remain to be verified during installation. Live external integrations and device calendar subscriptions were not retested for this deployment-only change.

Docker deployment files and Docker CI checks were removed. No application schema or local user data was changed by this deployment update. Existing database backups remain ignored by Git.

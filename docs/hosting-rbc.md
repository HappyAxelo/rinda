# Hosting RINDA at RBC

A guide for RBC ICT (or the national data centre) to run RINDA on infrastructure the programme owns. It complements
`deployment.md`, which covers the public demonstration on Render.

**Status:** draft for discussion with RBC ICT. Nothing here has been agreed or tested on RBC infrastructure.

## Why own the hosting

- The operations database holds staff accounts, field records and GPS positions. Under Law No. 058/2021, the
  controller must know where the data lives and who can reach it.
- The free demonstration host wipes its disk on every redeploy. Operational records need persistent storage with
  backups.
- Programme ownership is one of the adoption conditions in the PhD proposal: the system has to run without the
  researcher.

## What it needs

| Resource | Minimum | Notes |
|---|---|---|
| Server or VM | 2 vCPU, 2 GB RAM, 20 GB disk | Serving needs about 460 MB of memory. Building the image fits nothing at start because the v3 models ship in `study/v3/` |
| OS | Any Linux that runs Docker | Tested only as a Docker image (`Dockerfile`) |
| Persistent volume | Mounted at `/app/var` | Database, backups, model cache, logs |
| HTTPS | Reverse proxy (nginx, Traefik) with an RBC certificate | Set `RINDA_SECURE_COOKIES=1` |
| Outbound internet | Optional | Basemap tiles (Esri, CARTO) and fonts. Without it the map has no imagery; see backlog B-14 for self-hosted tiles |
| Inbound | HTTPS from staff networks and mobile data | Field phones need to reach the server, at least now and then, to sync |

## Steps

1. Get the image: `docker build -t rinda:<version> .` from a tagged release of `HappyAxelo/rinda`. Record the commit
   hash.
2. Create the volume and the environment file (keep it out of version control):

   ```
   RINDA_DEMO=0
   RINDA_PUBLIC_READ=0
   RINDA_SECURE_COOKIES=1
   RINDA_ADMIN_EMAIL=<rbc ict admin>
   RINDA_ADMIN_PASSWORD=<from the RBC secret store>
   ```

3. Run: `docker run -d --name rinda --restart unless-stopped -p 127.0.0.1:7860:7860 -v rinda-var:/app/var --env-file rinda.env rinda:<version>`
4. Put the reverse proxy in front with HTTPS. Check `https://<host>/api/v1/ready` returns `"status": "ready"`.
5. Sign in as the administrator, change the password, and create accounts with the narrowest role, district and
   language (see `admin-guide.md`).
6. Schedule backups: `POST /api/v1/admin/backup` with an administrator session, or copy `/app/var/backups/` nightly
   to RBC backup storage. Run a restore drill before the pilot starts (`deployment.md`, Backup and restore).
7. Monitor `/api/v1/ready` from RBC's monitoring system (backlog B-08).

## Identity

RINDA has its own accounts (scrypt-hashed passwords, 12-hour sessions). If RBC has a central identity provider,
connecting it (OIDC or SAML through the reverse proxy) is planned work and is not built. Until then, accounts are
issued and removed by the RINDA administrator. MFA is not yet available (backlog B-07).

## Updates

- Build the new tag, take a backup, replace the container, then check `/ready` and the audit trail.
- Database migrations are additive, so rolling back to the previous image is safe.

## Integrations RBC may connect later

| Integration | State in RINDA | Needs from RBC |
|---|---|---|
| DHIS2 / HMIS | Weekly `dataValueSet` export file (`/api/v1/reports/dhis2`) | Data set, data element and org unit UIDs in `config/dhis2_mapping.json`; a test import on the DHIS2 training instance |
| SMS / WhatsApp | Outbox drafts messages; nothing is sent | Approved provider (e.g. an aggregator RBC already uses), sender ID, budget, consent wording |
| Live satellite and weather inputs | Not built (backlog B-01) | Earth Engine service account; Meteo Rwanda data agreement |

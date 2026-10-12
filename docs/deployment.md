# Deployment, rollback and recovery

## Environments

| Environment | How | Purpose |
|---|---|---|
| Local | `powershell -File run.ps1 [-Demo]` or `uvicorn --app-dir server main:app --port 8740` | Development |
| CI | GitHub Actions `ci.yml`: tests, memory budget, Docker build-and-serve | Gate for `main` |
| Public demonstration | Render Blueprint `render.yaml` (free plan, Frankfurt), auto-deploy from `main` | Evaluation by partners |
| Programme (recommended) | Docker image on an RBC or national data-centre server with a persistent volume, HTTPS proxy and the ministry's identity provider | Operational pilot |

## Configuration

| Variable | Default | Notes |
|---|---|---|
| `PORT` | 7860 (image) | Render sets 10000 |
| `RINDA_STUDY` | `./study` | Study bundle |
| `RINDA_VAR` | `./var` | Models, database, backups. **Mount persistent storage here** |
| `RINDA_ADMIN_EMAIL` / `RINDA_ADMIN_PASSWORD` | `admin@rinda.local` / generated | Used once, when no administrator exists |
| `RINDA_PUBLIC_READ` | 1 | 0 requires sign-in for everything |
| `RINDA_DEMO` | 0 | 1 enables demonstration sign-in; must be 0 in production |
| `RINDA_SECURE_COOKIES` | 0 | Set 1 behind HTTPS |
| `RINDA_STATION_KEYS` | none | JSON `{ "node-id": "key" }` |

## Health

- `GET /api/v1/health`: process alive (liveness).
- `GET /api/v1/ready`: models loaded and database reachable; 503 while starting (readiness; used by Render and Docker).
- Forecast freshness is separate: `ready` can be true while `forecast_freshness` is `stale`.

Three different things can fail independently: **application uptime**, **forecast-engine readiness** and **availability
of a fresh forecast**. Today the third is permanently false because no live inputs are connected.

## Deploying

1. Merge to `main`; CI must pass.
2. Render rebuilds the image (models are fitted during the build) and switches traffic after `/api/v1/ready` succeeds.
3. Check `/api/v1/ready` and sign in.

Database migrations (`server/db.py`) are additive and run automatically at start; none drops or rewrites data.

## Rollback

- Render dashboard → service → Deploys → choose the last good deploy → **Rollback**. Or `git revert` the commit and push.
- Migrations are additive, so an older version runs against a newer database (new columns are ignored).

## Backup and restore

- Create: Administration → Backups → **Create backup**, or `POST /api/v1/admin/backup`. Uses SQLite's online backup API,
  so it is consistent while the service runs. Download the file and store it off the server.
- Restore: stop the service, replace `var/operations.db` with the backup file, start the service. Verify by signing
  in and checking the audit trail's latest entry.
- The restore path is covered by `tests/test_workflow.py::test_backup_produces_restorable_copy` (backup opens, schema
  and users present).
- **Render free plan:** the disk is wiped at every deploy and on sleep/restart. Treat all records there as disposable.

## Incident response (minimum)

1. Check `/api/v1/ready` and the Render logs (JSON lines; filter `"event": "unhandled"` and quote `rid`).
2. If the forecast engine is not ready after 3 minutes, roll back.
3. If data integrity is in doubt, set the service to read-only by removing user roles, take a backup, investigate.
4. Record the incident and actions in the audit trail (admin action) and in the issue tracker.

## Costs observed

| Item | Cost |
|---|---|
| Render free web service | $0; sleeps when idle; no persistent disk |
| Render Starter + 1 GB disk | about $7 + $0.25 per month (check current pricing) |
| GitHub Actions on a public repository | $0 |
| Basemap tiles (Esri, CARTO) | $0 at demonstration volume; review terms before operational use or self-host tiles |
| Sentinel-1 via Earth Engine | Free for research; commercial or government operational use needs a licence check |

# RINDA API reference

Generated from the OpenAPI schema by `scripts/gen_api_docs.py`. Interactive version: `/api/docs`; machine-readable: `/api/openapi.json`.

## Conventions

- **Base path** `/api/v1`. `/api` is a compatibility alias with identical behaviour, hidden from the schema; it will be removed no earlier than six months after a v2 is published.
- **Authentication** HttpOnly session cookie `rinda_session` (SameSite=Strict, 12 h) from `POST /auth/login`. Field stations use `X-Station-Key` on `/telemetry/{station_id}` only.
- **CSRF** every POST, PATCH and DELETE must send `X-RINDA: 1`. Requests without it get 403 `csrf`.
- **Errors** `{"error": {"code": "...", "message": "..."}}` with codes `bad_request`, `unauthenticated` (401), `forbidden` (403), `not_found`, `conflict` (409), `too_large` (413), `invalid` (422), `rate_limited` (429), `internal` (500, with `request_id`).
- **Rate limits** per client IP: 10 sign-in attempts per minute, 120 writes per minute; telemetry 120 per minute per station.
- **Uploads** at most 20 MB; batch scoring at most 200 000 rows.
- **Pagination** lists are bounded server-side (audit trail `limit` ≤ 2000, feedback 200). Forecast lists return one issue (≤ 600 rows).
- **Request IDs** every response carries `X-Request-ID`; quote it when reporting a problem.

## Endpoints

### Admin

| Method | Path | Who | Purpose |
|---|---|---|---|
| GET | `/api/v1/admin/audit` | supervisor, admin (`read:audit`) | Admin Audit |
| POST | `/api/v1/admin/backup` | admin (`admin:system`) | Admin Backup |
| GET | `/api/v1/admin/backup/{name}` | admin (`admin:system`) | Admin Backup Download |
| GET | `/api/v1/admin/status` | admin (`admin:system`) | Admin Status |
| GET | `/api/v1/admin/users` | admin (`admin:users`) | Admin Users |
| POST | `/api/v1/admin/users` | admin (`admin:users`) | Admin Create User |
| PATCH | `/api/v1/admin/users/{uid}` | admin (`admin:users`) | Admin Update User |

### Field Surveillance

| Method | Path | Who | Purpose |
|---|---|---|---|
| GET | `/api/v1/alerts` | viewer, field_officer, analyst, supervisor, admin (`read:forecast`) | List Alerts |
| POST | `/api/v1/alerts/{aid}` | supervisor, admin (`act:alert`) | Act Alert |
| GET | `/api/v1/feedback` | admin (`admin:users`) | List Feedback |
| POST | `/api/v1/feedback` | public | Feedback |
| GET | `/api/v1/field/assignees` | supervisor, admin (`assign:task`) | People a supervisor can assign inspections to (names and roles only). |
| GET | `/api/v1/field/export/{kind}.csv` | supervisor, admin (`export:field`) | Export |
| GET | `/api/v1/field/summary` | public | Counts only: safe for every role, including public viewers. |
| GET | `/api/v1/interventions` | field_officer, analyst, supervisor, admin (`read:field`) | List Interventions |
| POST | `/api/v1/interventions` | supervisor, admin (`authorize:intervention`) | Authorise |
| POST | `/api/v1/interventions/{iid}/followup` | supervisor, admin (`review:observation`) | Followup |
| POST | `/api/v1/interventions/{iid}/performed` | field_officer, supervisor, admin (`record:intervention`) | Performed |
| POST | `/api/v1/observations` | field_officer, analyst, supervisor, admin (`write:observation`) | Create Observation |
| GET | `/api/v1/observations` | field_officer, analyst, supervisor, admin (`read:field`) | List Observations |
| POST | `/api/v1/observations/{oid}/review` | supervisor, admin (`review:observation`) | Review Observation |
| POST | `/api/v1/tasks` | supervisor, admin (`assign:task`) | Create Task |
| GET | `/api/v1/tasks` | field_officer, analyst, supervisor, admin (`read:field`) | List Tasks |
| POST | `/api/v1/tasks/{tid}/status` | field_officer, supervisor, admin (`update:own_task`) | Task Status |

### Validation

| Method | Path | Who | Purpose |
|---|---|---|---|
| GET | `/api/v1/assurance` | viewer, field_officer, analyst, supervisor, admin (`read:forecast`) | Assurance |
| GET | `/api/v1/replay` | viewer, field_officer, analyst, supervisor, admin (`read:forecast`) | What the forecast said on an issue date, which inputs it had, and what the |
| GET | `/api/v1/replay/export.csv` | analyst, supervisor, admin (`export:validation`) | Replay Export |
| GET | `/api/v1/verification` | viewer, field_officer, analyst, supervisor, admin (`read:forecast`) | Verification |

### Auth

| Method | Path | Who | Purpose |
|---|---|---|---|
| POST | `/api/v1/auth/demo` | public | Auth Demo |
| POST | `/api/v1/auth/login` | public | Auth Login |
| POST | `/api/v1/auth/logout` | public | Auth Logout |
| GET | `/api/v1/auth/me` | public | Auth Me |
| POST | `/api/v1/auth/password` | viewer, field_officer, analyst, supervisor, admin (`read:forecast`) | Auth Password |

### Forecast

| Method | Path | Who | Purpose |
|---|---|---|---|
| GET | `/api/v1/bootstrap` | viewer, field_officer, analyst, supervisor, admin (`read:forecast`) | Bootstrap |
| GET | `/api/v1/explain` | viewer, field_officer, analyst, supervisor, admin (`read:forecast`) | Explain |
| GET | `/api/v1/forecast` | viewer, field_officer, analyst, supervisor, admin (`read:forecast`) | Forecast |
| GET | `/api/v1/forecast/meta` | viewer, field_officer, analyst, supervisor, admin (`read:forecast`) | Forecast Meta |
| GET | `/api/v1/scenario` | viewer, field_officer, analyst, supervisor, admin (`read:forecast`) | Scenario |
| POST | `/api/v1/score` | analyst, supervisor, admin (`score:batch`) | Score |
| GET | `/api/v1/score/template` | viewer, field_officer, analyst, supervisor, admin (`read:forecast`) | Score Template |
| GET | `/api/v1/site/{site_id}` | viewer, field_officer, analyst, supervisor, admin (`read:forecast`) | Site |

### Programme

| Method | Path | Who | Purpose |
|---|---|---|---|
| GET | `/api/v1/bulletin` | viewer, field_officer, analyst, supervisor, admin (`read:forecast`) | Bulletin |
| POST | `/api/v1/economics` | viewer, field_officer, analyst, supervisor, admin (`read:forecast`) | Economics |
| GET | `/api/v1/evidence` | viewer, field_officer, analyst, supervisor, admin (`read:forecast`) | Evidence |
| GET | `/api/v1/governance` | viewer, field_officer, analyst, supervisor, admin (`read:forecast`) | Governance |

### Flight Logs

| Method | Path | Who | Purpose |
|---|---|---|---|
| GET | `/api/v1/flightlogs` | viewer, field_officer, analyst, supervisor, admin (`read:forecast`) | Flightlog List |
| POST | `/api/v1/flightlogs` | analyst, supervisor, admin (`upload:flightlog`) | Flightlog Upload |
| GET | `/api/v1/flightlogs/analysis` | viewer, field_officer, analyst, supervisor, admin (`read:forecast`) | Flightlog Analysis |
| GET | `/api/v1/flightlogs/schema` | viewer, field_officer, analyst, supervisor, admin (`read:forecast`) | Flightlog Schema |
| GET | `/api/v1/flightlogs/template` | viewer, field_officer, analyst, supervisor, admin (`read:forecast`) | Flightlog Template |
| DELETE | `/api/v1/flightlogs/{log_id}` | supervisor, admin (`delete:flightlog`) | Flightlog Delete |
| POST | `/api/v1/missions/{mid}/synthetic-log` | analyst, supervisor, admin (`upload:flightlog`) | Flightlog Synthetic |

### System

| Method | Path | Who | Purpose |
|---|---|---|---|
| GET | `/api/v1/health` | public | Liveness: the process is serving requests. Says nothing about forecast validity. |
| HEAD | `/api/v1/health` | public | Liveness: the process is serving requests. Says nothing about forecast validity. |
| GET | `/api/v1/ready` | public | Readiness: models loaded and database reachable. 503 until both hold. |

### Inputs

| Method | Path | Who | Purpose |
|---|---|---|---|
| GET | `/api/v1/meteo` | viewer, field_officer, analyst, supervisor, admin (`read:forecast`) | Meteo |
| GET | `/api/v1/meteo/summary` | viewer, field_officer, analyst, supervisor, admin (`read:forecast`) | Meteo Summary |
| GET | `/api/v1/telemetry` | viewer, field_officer, analyst, supervisor, admin (`read:forecast`) | Telemetry |
| POST | `/api/v1/telemetry/{station_id}` | public | Telemetry Ingest |

### Missions

| Method | Path | Who | Purpose |
|---|---|---|---|
| GET | `/api/v1/missions` | viewer, field_officer, analyst, supervisor, admin (`read:forecast`) | Missions |
| POST | `/api/v1/missions/plan` | viewer, field_officer, analyst, supervisor, admin (`read:forecast`) | Planning preview is open to every reader; committing needs plan:mission. |
| GET | `/api/v1/missions/{mid}` | viewer, field_officer, analyst, supervisor, admin (`read:forecast`) | Mission |
| GET | `/api/v1/missions/{mid}/export` | analyst, supervisor, admin (`plan:mission`) | Mission Export |
| POST | `/api/v1/missions/{mid}/status` | analyst, supervisor, admin (`plan:mission`) | Mission Status |
| POST | `/api/v1/timing` | viewer, field_officer, analyst, supervisor, admin (`read:forecast`) | Timing |

## Examples

```bash
# forecast for the latest 7-day issue, with provenance and freshness
curl -s https://<host>/api/v1/forecast?h=7 | jq '.meta | {issue_date, valid_window, status, model_status}'

# sign in and record an observation
curl -c jar -H 'X-RINDA: 1' -H 'content-type: application/json' -d '{"email":"...","password":"..."}' https://<host>/api/v1/auth/login
curl -b jar -H 'X-RINDA: 1' -H 'content-type: application/json' -d @observation.json https://<host>/api/v1/observations
```

Observation body (fields beyond these are ignored):

```json
{"client_uuid": "6f0d...", "site_id": "G0221", "task_id": 12, "lat": -1.84774, "lon": 30.18259, "gps_accuracy_m": 6,
 "observed_at": "2026-10-12T07:40:00Z", "water_present": "yes", "habitat_type": "rice_paddy",
 "larvae_present": "yes", "dips": 10, "larvae_count": 14, "anopheles_present": "yes",
 "species": ["an_gambiae_sl"], "stages": ["late_instar"], "pupae_present": "not_sampled"}
```

Field-station telemetry:

```bash
curl -H 'X-Station-Key: <key>' -H 'content-type: application/json' \
  -d '{"seq":18234,"ts_utc":"2026-01-19T06:00:00Z","rain_mm":0.4,"water_level_cm":7.5}' https://<host>/api/v1/telemetry/ESP32-GKM-01
```

## Integration status

No integration with a national surveillance or reporting system (for example DHIS2 / HMIS) has been built or tested. It should be specified only after the partner confirms the target system, authorisation and data-sharing terms.

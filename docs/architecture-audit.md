# Architecture audit

Audit date 2026-10-12, against commit `a4d0b1d` (before this work) and the live deployment at
`https://rinda.onrender.com`. Findings are evidence-based: each was reproduced or measured.

## 1. Architecture map

```
Browser (vanilla ES modules, ECharts 5.5.1, Leaflet 1.9.4; no build step)
   │  same-origin HTTPS, session cookie, X-RINDA header on writes
   ▼
FastAPI app  server/main.py ── /api/v1 (documented) and /api (alias)
   ├── security.py   users, scrypt hashes, sessions, role → permission map, rate limit
   ├── field.py      tasks, observations, reviews, interventions, alerts, exports, feedback
   ├── rules.py      forecast provenance, freshness, model status, verification categories
   ├── engine.py     XGBoost + isotonic calibration (refit once from study tables, cached)
   ├── data.py       study products, radar series, weather, verification against radar
   ├── planner.py    sorties, launch zones, routing, timing/energy models, flight files
   ├── ops.py        missions, flight logs, telemetry, economics, audit trail
   ├── bulletin.py   printable A4 bulletin
   └── db.py         SQLite (WAL), versioned additive migrations, online backup
Static data  study/   frozen study bundle (config, tables, outputs, review statistics)
Config       config/operational_rules.json   verification rules, freshness, alert and intervention rules
State        var/     models cache, operations.db, backups, station keys
```

| Concern | Implementation | State |
|---|---|---|
| Frontend | Hash router `web/js/app.js`, one module per page in `web/js/views/` | Working |
| Forecast engine | In-process; models fitted at image build; inference < 50 ms per site | Working |
| Data ingestion | None live. Study bundle is a static export; field-node telemetry endpoint exists, no nodes deployed | Not operational |
| Geospatial | Point sites (50 m buffers), Leaflet with Esri/CARTO tiles | Working |
| Persistence | SQLite in `var/`; Render free plan has no persistent disk | Works; not durable on free plan |
| Auth | Added in this work (sessions, roles, server-side permissions) | Working |
| Deployment | Docker → Render Blueprint, auto-deploy from `main`; GitHub Actions CI | Working |
| Monitoring | Structured JSON request logs, in-process latency metrics, readiness endpoint | Basic |

## 2. Baseline before changes

- Tests: 7 API smoke tests, all passing. CI: tests, Linux memory budget (peak RSS 391 MB), Docker build, all green.
- Live site: every endpoint answered in 0.6–1.5 s warm; responses Brotli-compressed by Cloudflare; no console errors.
- "Forecast-engine connection": not reproducible warm. On the free plan the service sleeps after 15 minutes idle and the
  first request waits for the container (observed ~50–60 s). The old boot screen showed "Connecting to forecast engine"
  for that whole time and gave up after 60 attempts. Cause: hosting cold start, not a broken connection.

## 3. Defects found and fixed

| # | Defect | Evidence | Fix |
|---|---|---|---|
| D1 | No authentication; any visitor could commit or approve missions, delete flight logs, read the audit trail | `DELETE /api/flightlogs/1` succeeded anonymously | Sessions, five roles, permission check on every protected route |
| D2 | Stale forecast presented as current | Latest issue 2026-01-20, 265 days old, labelled "Operating picture" | Freshness in every forecast response; critical banner; stale chip; bulletin stamp |
| D3 | Model status hidden although a persistence baseline outperforms it | `reference_forecasts.csv`: B1 PR-AUC 0.628 vs model 0.581 (7 d) | Research-stage status computed from the reference results and shown everywhere |
| D4 | Arbitrary probability bins (0.2 steps) produced contradictions such as a "formation" call labelled "Low" | Point G0473, p = 0.327: call formation, class Low | Documented verification categories from the conformal set, `config/operational_rules.json` |
| D5 | Forecast plus onboard check could trigger spraying with no recorded human authorisation | Planner emitted VERIFY_AND_TREAT, export open to all | Flight files released only after supervisor approval; interventions need a verified observation |
| D6 | Scenario Lab returned 500 on the latest issue | NaN rainfall (weather ends 5 days before issue) broke JSON encoding | Missing inputs returned as `null` plus a `missing_inputs` list; regression test |
| D7 | Saving a mission failed after schema migration | `table missions has 11 columns but 8 values` | Named-column insert; covered by journey test |
| D8 | `HEAD /` returned 404, breaking uptime monitors | `curl -X HEAD` | GET/HEAD route |
| D9 | `/api/health` exposed internal log lines | Response body | Liveness `/health` and readiness `/ready` separated, no internals |
| D10 | No security headers, no rate limit, unbounded uploads | Response headers; code review | CSP, nosniff, frame and referrer policy, HSTS behind HTTPS; rate limits; 20 MB cap |
| D11 | Mission status could jump states and be set by anyone | Code review | State machine PLANNED→APPROVED→IN_FLIGHT→COMPLETED, approval needs supervisor |

## 4. Known limitations and technical debt

- **Data are a frozen archive.** No pipeline ingests new Sentinel-1 passes or weather; the platform cannot produce a
  current forecast. This is the single largest blocker to operational use.
- **SQLite on ephemeral disk.** On the free plan, users, field records and audit history vanish at every redeploy.
  A persistent disk (paid plan) or managed Postgres is required before real records are entered.
- **In-process metrics** reset on restart; no external error monitoring or alerting is wired up.
- **Single process.** Rate limits and metrics are per process; scaling horizontally would need a shared store.
- **Flight-operations analytics** are built but have only synthetic data; no airframe integration exists.
- **Frontend has no automated browser tests**; journeys are covered at API level and were checked manually in a browser.
- **Duplicate OpenAPI operation IDs** from the `/api` alias produce a FastAPI warning (harmless, alias is hidden).

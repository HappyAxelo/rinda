# Prioritised backlog

Ordered by risk reduction and user value. Status as of 2026-11-06.

| ID | Item | Phase | Status |
|---|---|---|---|
| A-01 | Server-side authentication, roles and permissions | 4 | **Done** |
| A-02 | Forecast provenance, freshness and stale warnings on every forecast | 2 | **Done** |
| A-03 | Research-stage status computed from reference comparison, shown everywhere | 2 | **Done** |
| A-04 | Documented verification categories replacing arbitrary probability bins | 2 | **Done** |
| A-05 | Field workflow: tasks → observations → review → authorised intervention → follow-up | 3 | **Done** |
| A-06 | Forecasts can never authorise treatment; flight files only after supervisor approval | 3 | **Done** |
| A-07 | Replay & validation with input timestamps, hits/false alarms/misses, both horizons, export | 2 | **Done** |
| A-08 | Alerts with ownership, expiry, acknowledge/dismiss/action states, dedup per point and issue | 3 | **Done** |
| A-09 | Offline capture with idempotent sync | 3 | **Done** |
| A-10 | Liveness/readiness split, security headers, CSRF, rate limits, upload caps, uniform errors, `/api/v1` | 1/4 | **Done** |
| A-11 | Structured logs, latency metrics, admin status, online backup | 4 | **Done** (in-process only) |
| A-12 | User guide, glossary, onboarding, feedback route | 4 | **Done** |
| A-13 | Regression tests for D6 (NaN) and D7 (migration insert) | 1 | **Done** |
| B-01 | **Live ingestion**: scheduled Sentinel-1 pulls, weather feed, forecast job writing immutable forecast records | 2 | Open, blocked on Earth Engine service account and Meteo Rwanda data access |
| B-02 | Persistent storage: paid disk or managed Postgres; automated off-site backups; restore drill | 4 | Open, needs a hosting budget decision |
| B-03 | Immutable forecast store (inputs' timestamps, model fingerprint at issue) for true operational replay | 2 | Open, depends on B-01 |
| B-04 | Beat persistence baseline (add persistence features; retest on held-out period) | 2 | Open, research |
| B-05 | Field validation: Target C from verified observations | 2 | Open, needs RBC field campaign |
| B-06 | Partner approval of `operational_rules.json` and a Moderate threshold | 2 | Open, needs RBC |
| B-07 | MFA and account lockout; `pip-audit` in CI; external penetration test | 4 | Open |
| B-08 | External uptime and freshness alerting (e.g. monitor on `/api/v1/ready`) | 4 | Open |
| B-09 | Browser end-to-end tests (Playwright) for the twelve journeys | 4 | Open; journeys currently covered at API level plus manual browser checks |
| B-10 | Formal accessibility audit (WCAG 2.1 AA) and usability sessions with district staff | 4 | Open |
| B-11 | Kinyarwanda localisation after terminology sessions | 4 | **Partial** (2026-11-06): EN/RW switch; draft strings for navigation and field screens; weekly report and messages. Needs review sessions and the remaining pages |
| B-12 | Email/SMS notification of assigned tasks (only if users ask for it) | 3 | **Partial**: outbox drafts task notices and weekly digests; no provider connected |
| B-13 | DHIS2 / HMIS integration (only after partner specifies it) | 5 | **Partial**: weekly dataValueSet export; UIDs pending from RBC HMIS; no push |
| B-17 | RE-AIM monitoring indicators from operational records | 5 | **Done** (2026-11-06) |
| B-18 | Field-registered habitats, larval density, habitat attributes | 3 | **Done** (2026-11-06) |
| B-19 | Offline app shell (service worker) | 3 | **Done** (2026-11-06); maps still need a connection (see B-14) |
| B-20 | Programme-manager role and proposed RBC job titles | 4 | **Done**; titles to confirm with RBC |
| B-21 | Cell / Village names for points and habitats | 3 | Open; needs NISR administrative boundaries |
| B-22 | Single sign-on with an RBC identity provider | 4 | Open |
| B-23 | SMS/WhatsApp provider adapter | 3 | Open; needs an approved provider and contract |
| B-14 | Self-hosted basemap tiles for offline and licence certainty | 4 | Open |
| B-15 | Remove `'unsafe-inline'` from CSP (move boot and print scripts to files) | 4 | Open |
| B-16 | Aggregate field-outcome analysis (forecast vs verified larvae) once data exist | 5 | Open, depends on B-05 |

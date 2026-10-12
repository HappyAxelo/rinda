# RINDA · Vector Habitat Intelligence

[![ci](https://github.com/HappyAxelo/rinda/actions/workflows/ci.yml/badge.svg)](https://github.com/HappyAxelo/rinda/actions/workflows/ci.yml)
[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/HappyAxelo/rinda)

Forecast-to-flight operations console for larval source management. RINDA takes the
7- and 14-day habitat-formation forecast from the Gikomero study and carries it through
mission planning, onboard verification, treatment records, programme economics and
bulletins. It is built for a national malaria programme (RBC Malaria & NTD Division,
district vector control officers, drone operators and analysts).

*Rinda* is Kinyarwanda for "protect".

## Run it

Local (Windows):

```powershell
pip install -r requirements.txt
powershell -ExecutionPolicy Bypass -File run.ps1
```

Local (any OS):

```bash
pip install -r requirements.txt
uvicorn --app-dir server main:app --port 8740
```

Docker:

```bash
docker build -t rinda .
docker run -p 7860:7860 rinda
```

Open http://localhost:8740 (or :7860 for Docker). The first start fits both forecast models
from the bundled study tables (under a minute) and caches them in `var/models/`; the Docker
image fits them at build time. API reference: `/api/docs` (documented base path `/api/v1`).

Tests: `pip install pytest httpx && pytest -q tests`.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `RINDA_STUDY` | `./study` | Study bundle to serve. Point it at a live `habitat-forecast` checkout to follow new pipeline runs. |
| `RINDA_VAR` | `./var` | Models cache, operations database, logs. Mount persistent storage here in production. |
| `RINDA_STATION_KEYS` | none | JSON map of field-node id to key, e.g. `{"ESP32-GKM-01":"..."}`. Placeholder keys never authenticate. |
| `RINDA_ADMIN_EMAIL`, `RINDA_ADMIN_PASSWORD` | generated | First administrator (used once) |
| `RINDA_PUBLIC_READ` | 1 | Anonymous read access to forecasts |
| `RINDA_DEMO` | 0 | Demonstration sign-in per role |
| `RINDA_SECURE_COOKIES` | 0 | Set 1 behind HTTPS |
| `PORT` | 7860 (Docker) | Listening port. |

## Deployment

- **Hosted service (Render):** `render.yaml` is a Render Blueprint. In the Render dashboard
  choose New → Blueprint and select this repository; Render builds the Dockerfile and
  redeploys on every push to `main`. The free plan sleeps after 15 minutes idle (about a
  minute to wake) and resets the operations database on redeploy; the Starter plan stays on,
  and a disk mounted at `/app/var` keeps missions and flight logs.
- **Hugging Face Space (PRO accounts):** `deploy/huggingface/` holds a Space that builds
  straight from this repository.
- **Programme use (RBC / MoH):** run the Docker image on a server in the national data centre
  or at RBC, with `RINDA_VAR` on persistent storage, behind the ministry's identity provider
  and HTTPS.

## Status

**Technically deployable for controlled evaluation; not scientifically validated for intervention decisions.**
The forecast model is research stage (a persistence baseline still scores higher) and the data archive ends on
2026-01-20, so every forecast is shown as stale. See `docs/readiness-assessment.md`.

## Roles and sign-in

Viewer, Field officer, Technical analyst, Supervisor and Institution administrator; permissions are enforced on the
server (`server/security.py`). With `RINDA_PUBLIC_READ=1` anyone can read forecasts and validation without an account.
With `RINDA_DEMO=1` (the hosted demonstration) a visitor can sign in as any role without a password; everything such
an account creates is labelled DEMO. Operational deployments set `RINDA_DEMO=0` and create accounts under Administration.

## Documentation

| Document | Contents |
|---|---|
| `docs/architecture-audit.md` | Architecture map, baseline, defects fixed, technical debt |
| `docs/product-requirements.md` | Users, workflow, acceptance criteria |
| `docs/model-validation.md` | Target, data, method, results against baselines, limitations |
| `docs/api.md` | Generated API reference with roles per endpoint |
| `docs/security.md` | Controls, threat model, privacy, open risks |
| `docs/deployment.md` | Configuration, health, deploy, rollback, backup and restore |
| `docs/user-guide.md`, `docs/admin-guide.md` | Operating instructions |
| `docs/backlog.md`, `docs/changes-2026-10-12.md`, `docs/readiness-assessment.md` | Plan, change record, readiness |

## Modules and the research they answer

| Module | What it does | Objective / RQ |
|---|---|---|
| Command Overview | Operating picture for one issue: calls, verify-first sites, skill, rainfall, priority queue, last issue checked against radar | O1 |
| Habitat Forecast | Map and table of every assessed site with class, 90% conformal call, uncertainty, priority; CSV/GeoJSON export; site dossier with radar history and model drivers | O1 |
| Scenario Lab | Live queries to the model: rescale rainfall, shift temperature, see probability and call respond; batch scoring of feature CSVs | O1 |
| Model Assurance | Holdout metrics with block-bootstrap CI, reference forecasts, random-split optimism, reliability, ablation, SHAP, rainfall memory, seasonal strata, fusion, leakage guard, engine reproduction | O1, RQ5 |
| Mission Planning | Forecast targets to sorties: launch zones, 2-opt routes, endurance and tank limits, verify-and-treat vs inspect-only, lawnmower comparison; exports QGroundControl `.plan`, MAVLink `.waypoints`, GeoJSON, CSV; mission register with approval workflow | O2, RQ-C, RQ6 |
| Flight Operations | Loop timing designer for `L ≤ d/v`, energy per hectare; flight-log ingestion and analytics (latency CDF, deadline-miss ratio, stage tails, verification precision/recall); compression frontier from board benchmarks | O2, RQ1–RQ4, RQ6 |
| Field Network | Weather station record with humidity gate; ESP32 node registry, uplink contract, packet delivery ratio, bill of materials | RQ-D |
| Impact & Economics | Manual LSM vs two-pass drone vs forecast-guided: cost, response time against the aquatic window, larvicide; national scale-up planner; outcome slots for the trial | O3 |
| Bulletins | Printable A4 bulletin per issue with map, coordinates, sorties, reliability statement and sign-off | O3 |
| Programme Evidence | The review's 11-step evidence chain (140 studies) with what the platform delivers at each step; objectives and RQ status | Review gap |
| Data & Governance | Source registry, claim control, data audit, model registry, append-only audit trail | — |

## What is measured and what is not

- **Measured (from the study):** every forecast, metric, reliability bin, ablation arm and
  driver shown. The 7-day engine reproduces the published holdout PR-AUC (0.5807 vs
  0.5809). The 14-day refit differs by 0.015 because of multithreaded tree construction,
  which is well inside the bootstrap interval.
- **Planning estimates (editable):** aircraft profile, timing stage budgets, energy model,
  economics inputs. They are labelled as assumptions wherever they appear.
- **Awaiting field data:** onboard verification accuracy, in-flight latency, deposition,
  entomological outcomes. The ingestion contracts are live; the numbers appear once
  logs arrive. Bench-test files can be generated to exercise the analytics; they are
  stamped SYNTHETIC on every screen that shows them.

## Findings the console surfaces

- At 7 days the simple persistence rule "30-day water frequency" scores PR-AUC 0.628
  against the model's 0.581 on the same test rows (`outputs/reference_forecasts.csv`).
  At 14 days it scores 0.629 against 0.310. Model Assurance states this plainly.
- Weather comes from one station, so site-to-site differences come from radar alone.
- Mode is hindcast until a numerical rainfall forecast feed is connected.

## Layout

```
server/
  engine.py     model loading/fitting (reuses mhf), scoring, explanations, scenarios
  data.py       study products, verification against radar, site dossiers, meteo
  planner.py    sortie partition, launch zones, routing, timing and energy models, exports
  ops.py        SQLite record: missions, flight logs, telemetry, audit; economics
  bulletin.py   A4 bulletin renderer
  main.py       FastAPI app and static console
web/            console (HTML, CSS, ES modules); vendor/ holds ECharts 5.5.1 and Leaflet 1.9.4
study/          frozen study bundle: mhf package, paper-run config, processed tables,
                Sentinel-1 site series, weather record, outputs, reports, review statistics
tests/          API smoke tests
deploy/         Hugging Face Space definition
var/            models, operations.db, station keys, logs (created at run time, not committed)
```

## Deployment notes for a programme

- Runs on one server on-premise or in the national data centre; the console needs no
  external scripts. Basemap tiles come from Esri/CARTO and can be swapped for a local
  tile server.
- Field nodes authenticate with per-node keys in `var/station_keys.json`; replace the
  placeholder keys before installing nodes.
- Roles in the header select a workspace. Put the app behind the ministry's identity
  provider before multi-user use.

## Data and citation

`study/` contains the Gikomero habitat-formation study: Sentinel-1 backscatter extracted
for 600 sites (Copernicus Sentinel data, 2023–2026), the Gikomero automatic weather station
daily record (2023-03-22 to 2026-01-15), processed modelling tables and model outputs, and
summary statistics from the systematic scoping review. Cite the study manuscript when using
these data.

© 2026 Happy Axel Muyombano, African Centre of Excellence in Internet of Things,
University of Rwanda. All rights reserved.

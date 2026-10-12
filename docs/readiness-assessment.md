# Readiness assessment

**Classification: technically deployable for controlled evaluation. Not scientifically validated for operational
intervention decisions.**

## Ready (implemented and tested)

- Forecast archive service with full provenance, freshness and research-stage labelling on every forecast, map,
  dossier, export and bulletin.
- Documented verification categories; no arbitrary thresholds; shape + colour + text symbology; table alternatives.
- Field workflow with server-enforced roles: inspection tasks, offline-capable observations with quality checks,
  independent review, supervisor-authorised interventions tied to verified evidence, treatment records, follow-up.
- Replay and validation without future information, with input timestamps and both horizons.
- Session authentication, permissions, CSRF protection, rate limits, security headers, audit trail, online backups.
- Liveness/readiness endpoints, JSON logs, in-process metrics, Docker image, CI with memory budget, Render Blueprint.
- Documentation set in `docs/`.

## Experimental (works, but not evidence for decisions)

- The forecast model: does not beat a persistence baseline; outcome is uncalibrated radar water; no field validation.
- 14-day horizon: detection rate 58% against 86% at 7 days.
- Mission planning, onboard timing and energy models, flight-log analytics: planning estimates and synthetic data only.
- Impact & economics: planning model from editable inputs; no measured costs or savings.

## Blocking deployment for real operations

1. **No live data.** The archive ends 2026-01-20; nothing produces a current forecast (backlog B-01).
2. **No durable storage on the current host.** Records are lost at every redeploy (B-02).
3. **No partner approval** of operational rules, roles, retention or data-sharing (B-06, security §Privacy).
4. **Model not validated** against field observations and not better than persistence (B-04, B-05).
5. **Security gaps** for an institutional system: no MFA, no penetration test, no dependency scanning (B-07).

## Twelve required journeys

| # | Journey | Evidence |
|---|---|---|
| 1 | Open the overview | Manual browser check; `/api/v1/forecast` tests |
| 2 | Select an operating area | Single area configured; other districts listed as not onboarded (multi-area not implemented) |
| 3 | Request a 7-day forecast | `test_forecast_carries_provenance_and_status` |
| 4 | Request a 14-day forecast | `test_14_day_forecast_carries_horizon_caution` |
| 5 | Forecast engine unavailable | Boot waits on `/api/v1/ready` with explanation and retry; per-page error with retry; readiness returns 503 while starting. Not covered by an automated test |
| 6 | Identify stale data | `test_forecast_carries_provenance_and_status`; banner, chip, bulletin stamp (manual) |
| 7 | Inspect a location | `/api/v1/site` and dossier (manual); field evidence restricted by role (test) |
| 8 | Review a historical forecast | `test_replay_uses_only_out_of_sample_issues` |
| 9 | Record a field observation | `test_full_field_journey`, `test_observation_quality_rules`, `test_offline_resubmission_is_idempotent`, manual UI run |
| 10 | Review and authorise an intervention | `test_full_field_journey`, manual UI run |
| 11 | Export an authorised report | Field CSV export with DEMO label (test); bulletin (test); validation export (manual) |
| 12 | Act without permission | `test_role_matrix_enforced_on_server`, `test_anonymous_can_read_but_not_write`, approval gate test |

## Before a public-health institution relies on it

Live ingestion and a forecast store; durable hosting with backups and a restore drill; partner-approved rules and
data-protection basis; field validation campaign; model improvement past the persistence baseline; MFA and an
external security review; usability sessions with district teams and a decision on Kinyarwanda.

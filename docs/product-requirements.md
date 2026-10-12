# Product requirements

## Purpose

Help authorised public-health teams decide where and when to inspect for new mosquito-breeding water, record what
they find, and act only on verified evidence, while keeping the forecast's reliability visible at every step.

RINDA keeps four concepts apart and never presents one as another:

| Concept | Source in RINDA |
|---|---|
| Predicted new surface water (habitat formation) | Forecast model |
| Confirmed larval / *Anopheles* presence | Verified field observations |
| Malaria transmission risk | Not modelled |
| Intervention priority | Supervisor decision from verified observations, under protocol |

## Users

| Role | Primary needs | Permissions (enforced server-side) |
|---|---|---|
| Viewer (incl. public evaluators) | See forecasts, validation, reports | read forecast |
| Field officer | Own inspection list; record observations offline; record treatments performed | + field read, write observation, update own task, record intervention |
| Technical analyst | Validate the model; plan missions; batch scoring; validation exports | + plan mission, upload flight log, score, export validation |
| Supervisor | Assign inspections, review records, authorise interventions, approve missions, act on alerts | + assign, review, authorise, approve, act on alerts, export field, audit |
| Institution administrator | Accounts, backups, system status | everything + admin |

Institutional buyer: a national malaria/vector-control programme (in Rwanda, RBC Malaria & NTD Division). Purchasing
authority, budget line and procurement route are **not yet identified** and must be confirmed with the partner.

## Core workflow and acceptance criteria

| Step | Acceptance criteria | Status |
|---|---|---|
| Forecast generated | Every forecast carries issue time, valid window, target, units, resolution, input timestamps, model version, status, warnings | Done (archive only; no live generation) |
| Point prioritised | Category from documented rules; never colour-only; table alternative to map | Done |
| Alert raised | One per point per issue for High; states open / acknowledged / dismissed (reason required) / actioned / expired | Done |
| Inspection assigned | Supervisor assigns to an active field officer with a due date; alert marked actioned | Done |
| Observation recorded | GPS, time, habitat, larvae with sampling effort, species, stages, pupae; "not sampled" distinct from "no"; quality checks; offline queue; idempotent resend | Done |
| Finding reviewed | Second person verifies / returns / rejects with reason | Done |
| Intervention authorised | Only from a verified eligible observation, with protocol reference, by a supervisor | Done |
| Intervention recorded | Quantity and time by the person treating | Done |
| Follow-up | Later verified observation at the same site closes the intervention with an outcome | Done |
| Outcome evaluated | Replay compares forecasts with radar; field outcomes per intervention | Partial: no aggregate field-outcome analysis yet |

## Questions the product must answer, and where

| Question | Where |
|---|---|
| Where to prioritise surveillance? | Situation overview, Habitat forecast |
| When might conditions develop? | Valid window on every forecast; target date per alert |
| What evidence supports the forecast? | Site dossier: drivers, radar history, inputs |
| How reliable is it here and at this horizon? | Research-stage banner, Replay & validation, Model assurance |
| What to inspect first, and why? | Priority queue with reason; alerts |
| What did officers observe? | Field surveillance, site dossier |
| What was authorised and done? | Interventions with protocol, authoriser, performer |
| Did later observations support the forecast? | Replay (radar); follow-up outcomes (field) |
| Did RINDA improve decisions or reduce cost? | **Not yet answerable**: needs a pilot with a comparison arm (Impact & economics holds a planning model only) |
| Can an institution adopt it? | `docs/` set, readiness assessment |

## Non-functional requirements

| Requirement | Target | Evidence |
|---|---|---|
| Works on ordinary phones | No horizontal scroll at 375 px; core flows usable | Checked manually on all main pages |
| Low bandwidth | No framework bundle; largest asset ECharts at 344 KB Brotli-compressed | ECharts size measured on Render; full page weight not yet profiled |
| Memory on 512 MB host | Served peak RSS < 460 MB | CI check |
| Accessibility | Keyboard navigation, focus states, labels, no colour-only meaning, reduced motion | Implemented; no formal audit yet |
| Security | Server-side permissions, CSRF, rate limits, security headers, hashed passwords | Tests in `tests/test_workflow.py` |
| Traceability | Audit trail for every state change | Implemented |

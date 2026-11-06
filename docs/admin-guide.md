# Administrator guide

## First start

1. Set `RINDA_ADMIN_EMAIL` and `RINDA_ADMIN_PASSWORD` before the first start (on Render the Blueprint generates the
   password: Dashboard → service → Environment). Without them, a random password is written to
   `var/initial_admin.txt`; read it, sign in, change it under Account, delete the file.
2. Set `RINDA_DEMO=0` for operational use and `RINDA_SECURE_COOKIES=1` behind HTTPS.
3. Replace placeholder field-node keys (`RINDA_STATION_KEYS` or `var/station_keys.json`).

## Users

**Administration → Users → Add user.** Choose the narrowest role that lets the person do their job:

| Role | Proposed RBC job (to confirm with RBC) | Give to |
|---|---|---|
| Viewer | Partner or evaluator | People who only need to read forecasts and reports |
| Field officer | Environmental Health Officer / larval survey technician | Staff doing inspections |
| Technical analyst | Entomologist or data manager | Staff validating the model or planning missions |
| Supervisor | District Vector Control Officer | District leads who assign, review and authorise |
| Programme manager | National Malaria Programme Manager (RBC) | National staff who need indicators and weekly reports, not record-level field data |
| Institution administrator | System administrator (RBC ICT) | One or two people managing accounts and backups |

Set each person's **district**, **language** (English or draft Kinyarwanda) and, if they agree, a **phone number**
for SMS or WhatsApp notices. Phone numbers are visible only to administrators and appear masked in the outbox.

Deactivating an account signs it out everywhere immediately. You cannot deactivate or demote yourself.

## Operational rules

`config/operational_rules.json` defines categories, freshness limits, alert and intervention rules, and observation
quality checks. Change it only after written agreement with the programme, through a reviewed commit; update
`version` and `approved_by`. The current file is **pending approval**, and the Moderate category is disabled until
a threshold is agreed.

## DHIS2 mapping and messaging

- `config/dhis2_mapping.json` maps RINDA's ten weekly counts to DHIS2 data elements. Every UID starts as
  `UNMAPPED`. Fill them in with the RBC HMIS team through a reviewed commit, and test-import on a DHIS2 training
  instance before real use.
- The outbox sends nothing until an SMS or WhatsApp provider is approved and connected (`RINDA_SMS_PROVIDER`;
  provider code is not written yet). Until then, staff send messages by hand and mark them as sent.

## Monitoring

**Administration** shows application uptime, engine readiness, forecast freshness, input dates, database size,
request counts with p50/p95 latency and 5xx errors per endpoint group (since last restart), backups, the audit
trail and user feedback. Hosting logs are JSON lines; search for `"event": "unhandled"` and the request ID a user reports.

Recommended external checks (not yet configured): an uptime monitor on `/api/v1/ready`, and an alert when
`forecast_freshness` stays `stale` once live ingestion exists.

## Backups

Create one before any configuration change and at least weekly; download it and store it off the server. See
`docs/deployment.md` for restore steps. On the free hosting plan the disk is wiped at every deploy.

## Troubleshooting

| Symptom | Likely cause | Action |
|---|---|---|
| "Service waking up after inactivity" for up to a minute | Free plan cold start | Wait; upgrade the plan for always-on |
| Everyone sees "Stale forecast" | No live inputs; archive ends 2026-01-20 | Expected until ingestion is built |
| User cannot sign in | Deactivated, wrong password, or rate limited (10/min) | Check Users; wait a minute |
| 403 "Missing X-RINDA header" from a script | Writes need the header | Add `X-RINDA: 1` |
| Field node gets 401 | Placeholder or wrong key | Set a real key for that node |
| Records disappeared after an update | Free plan disk reset | Restore from the latest downloaded backup; move to persistent storage |

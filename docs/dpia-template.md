# Data-protection impact assessment: RINDA (draft)

**DRAFT for review by the RBC and University of Rwanda data-protection officers.** It describes how RINDA handles
personal data as built on 2026-11-06, so the assessment starts from facts. The parties must decide the
legal basis, the risk ratings they accept and the sign-off.

## 1. Processing described

| Item | As built |
|---|---|
| Purpose | Plan, record, review and evaluate larval source management; evaluate RINDA itself |
| Data subjects | Programme staff (field officers, supervisors, analysts, managers, administrators) |
| Personal data | Name, work email, role, organisation, district, optional phone, language, sign-in times, actions in the audit trail, authorship of field records |
| Not processed | Patient, household or resident data; photographs; biometric data |
| Location data | GPS of habitats and of where an observation was recorded (therefore where a staff member was at that time) |
| Storage | SQLite database on the host (`var/operations.db`), backups in `var/backups/`; on field phones: queued observations and the last task list (browser storage and service-worker cache) |
| Recipients | Staff by role; DHIS2 (weekly aggregates only, via a file the HMIS team imports); SMS/WhatsApp provider if one is connected (message text and phone) |
| Retention | Not yet set: see the data-sharing agreement template |

## 2. Legal basis (to decide)

[Public-interest task of RBC / consent of staff / contract of employment / other]. Ethics approval for research use:
[number].

## 3. Risks and controls

| Risk | Likelihood / impact (to rate) | Controls in RINDA | Residual |
|---|---|---|---|
| Account takeover leads to falsified field records | [ ] | scrypt hashes, rate limit, 12 h sessions, audit trail, reviewer ≠ observer | No MFA yet (B-07) |
| Staff movements inferred from observation GPS and times | [ ] | Field detail limited to field, analyst and supervisor roles; programme managers see aggregates | Analysts see all records |
| Lost or shared phone exposes cached tasks | [ ] | Cache holds the user's own tasks; cleared at sign-out; HttpOnly session cookie expires in 12 h | Data stays until sign-out or expiry if the phone is lost while signed in |
| Phone numbers leaked | [ ] | Optional; admin only; masked in the outbox; never in exports | Stored unencrypted in the database |
| Message provider processes staff phone numbers | [ ] | No provider connected; outbox records every message | Depends on the provider contract |
| Wrong data imported into HMIS | [ ] | `UNMAPPED:` identifiers until mapped; demonstration data stamped DO NOT IMPORT; RINDA never pushes | Human error in mapping |
| Loss of records | [ ] | Online backups, additive migrations, restore test | Off-site backup depends on the host |
| Decisions over-rely on an unvalidated forecast | [ ] | Status line on every page, bulletin and report; forecasts cannot authorise treatment | Training and SOP needed |

## 4. Rights of data subjects

Staff can see their account details under Account. Administrators can correct or deactivate accounts. Deletion of
authored field records conflicts with the audit requirement. The parties must decide whether to pseudonymise them
instead.

## 5. Sign-off

| Role | Name | Decision | Date |
|---|---|---|---|
| Data protection officer, RBC | | | |
| Data protection officer, University of Rwanda | | | |
| System owner | | | |

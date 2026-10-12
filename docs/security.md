# Security and privacy

## Controls in place

| Area | Control |
|---|---|
| Authentication | Email + password; scrypt (N=16384, r=8, p=1) with per-user salt; minimum 12 characters with mixed case and a digit |
| Sessions | 32-byte random token in an HttpOnly, SameSite=Strict cookie; only a SHA-256 hash is stored; 12 h expiry; revoked on sign-out and on account deactivation; `Secure` when `RINDA_SECURE_COOKIES=1` |
| Authorisation | Role → permission map in `server/security.py`; every protected route declares its permission and the server refuses otherwise. The interface only hides what the server already blocks |
| Separation of duties | Observers cannot verify their own records; only supervisors authorise interventions and approve missions |
| CSRF | SameSite=Strict cookie and a required `X-RINDA: 1` header on all writes |
| Rate limiting | 10 sign-in attempts and 120 writes per minute per IP; 120 telemetry posts per minute per station |
| Input validation | Pydantic schemas with ranges and enumerations; domain checks on observations; uploads ≤ 20 MB; batch ≤ 200 000 rows |
| Output encoding | All user-supplied text is HTML-escaped before rendering |
| Headers | Content-Security-Policy (self + named tile and font hosts), X-Content-Type-Options, X-Frame-Options, Referrer-Policy, Permissions-Policy, HSTS behind HTTPS |
| Secrets | None in the client or the repository. Admin password and station keys come from environment variables; first-run fallback writes to `var/initial_admin.txt` (git-ignored) |
| Field nodes | Per-node keys compared in constant time; placeholder keys never authenticate |
| Audit | Append-only audit table for sign-ins, failed sign-ins, user changes, tasks, reviews, interventions, missions, exports, backups |
| Logging | JSON request logs (method, path, status, latency, request ID). No bodies, cookies, query values or passwords; unhandled errors log traceback frames only |
| Demo isolation | Demo accounts only exist when `RINDA_DEMO=1`; their sessions stop working when it is turned off; their records are flagged and labelled in exports |

## Threat model (summary)

| Threat | Impact | Mitigation | Residual risk |
|---|---|---|---|
| Unauthorised treatment decision | Larvicide applied without evidence | Interventions require a verified observation and supervisor role; forecasts cannot authorise | Social engineering of a supervisor account |
| Account takeover | Falsified records | Strong hashes, rate limit, session revocation, audit trail | No MFA; no lockout beyond rate limit |
| CSRF / XSS | Actions on a user's behalf | SameSite=Strict, custom header, escaping, CSP | CSP allows inline scripts (`'unsafe-inline'`) for the boot script and print button |
| Exposure of field records | Locations and names of officers disclosed | Field detail needs field/analyst/supervisor roles; public sees counts only | Analysts see all observations |
| Data loss | Operational history lost | Online backup endpoint; restore procedure | **Free hosting wipes the disk on redeploy**; no automatic off-site backup |
| Spoofed field telemetry | Corrupted inputs | Per-node keys, size and rate limits | Keys are static; no rotation workflow |
| Denial of service | Unavailable during response | Rate limits, upload caps | Single small instance; no WAF beyond Cloudflare defaults |
| Demo misuse | Public writes clutter a shared demo | Records flagged DEMO; demo off in production | Demo database is public by design |

## Privacy

- Personal data held: staff names, emails, roles, sign-in times; observer and reviewer IDs on records. No patient or
  household data.
- Locations are of monitoring points and field observations, not of homes.
- Before collecting real records, the institution must document the lawful basis (Rwanda Law No. 058/2021 on the
  protection of personal data and privacy), retention periods and access policy. Recommended starting retention:
  audit 5 years, field records per programme policy, sessions 12 h, feedback 2 years.

## Unresolved risks before institutional use

1. No multi-factor authentication and no account lockout.
2. No managed secret store; environment variables on the host.
3. No automated off-site backups or tested disaster recovery on the hosting platform.
4. No dependency vulnerability scanning in CI (add `pip-audit`).
5. No independent penetration test.
6. CSP still allows inline scripts.
7. No data-protection impact assessment or institutional data-sharing agreement.

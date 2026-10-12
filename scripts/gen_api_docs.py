"""Regenerate docs/api.md from the running application's OpenAPI schema.

    python scripts/gen_api_docs.py

Run after changing any route; CI-free on purpose so the doc diff is reviewed.
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("RINDA_VAR", tempfile.mkdtemp(prefix="rinda-doc-"))
sys.path.insert(0, str(ROOT / "server"))

import main  # noqa: E402
from security import PERMS  # noqa: E402

PERM_OF = {}
for route in main.api.routes:
    deps = getattr(route, "dependant", None)
    found = None
    for d in (deps.dependencies if deps else []):
        fn = getattr(d.call, "__closure__", None)
        if fn:
            for cell in fn:
                if isinstance(cell.cell_contents, str) and ":" in cell.cell_contents:
                    found = cell.cell_contents
    for m in getattr(route, "methods", []):
        PERM_OF[(m, "/api/v1" + route.path)] = found


def roles_for(perm):
    if perm is None:
        return "public"
    return ", ".join(r for r, ps in PERMS.items() if perm in ps)


spec = main.app.openapi()
out = ["# RINDA API reference", "",
       "Generated from the OpenAPI schema by `scripts/gen_api_docs.py`. Interactive version: `/api/docs`; "
       "machine-readable: `/api/openapi.json`.", "",
       "## Conventions", "",
       "- **Base path** `/api/v1`. `/api` is a compatibility alias with identical behaviour, hidden from the schema; "
       "it will be removed no earlier than six months after a v2 is published.",
       "- **Authentication** HttpOnly session cookie `rinda_session` (SameSite=Strict, 12 h) from `POST /auth/login`. "
       "Field stations use `X-Station-Key` on `/telemetry/{station_id}` only.",
       "- **CSRF** every POST, PATCH and DELETE must send `X-RINDA: 1`. Requests without it get 403 `csrf`.",
       "- **Errors** `{\"error\": {\"code\": \"...\", \"message\": \"...\"}}` with codes `bad_request`, `unauthenticated` (401), "
       "`forbidden` (403), `not_found`, `conflict` (409), `too_large` (413), `invalid` (422), `rate_limited` (429), `internal` (500, with `request_id`).",
       "- **Rate limits** per client IP: 10 sign-in attempts per minute, 120 writes per minute; telemetry 120 per minute per station.",
       "- **Uploads** at most 20 MB; batch scoring at most 200 000 rows.",
       "- **Pagination** lists are bounded server-side (audit trail `limit` ≤ 2000, feedback 200). Forecast lists return one issue (≤ 600 rows).",
       "- **Request IDs** every response carries `X-Request-ID`; quote it when reporting a problem.", "",
       "## Endpoints", ""]
by_tag: dict[str, list] = {}
for path, ops in sorted(spec["paths"].items()):
    for method, op in ops.items():
        by_tag.setdefault((op.get("tags") or ["other"])[0], []).append((method.upper(), path, op))
for tag, items in by_tag.items():
    out += [f"### {tag.title()}", "", "| Method | Path | Who | Purpose |", "|---|---|---|---|"]
    for m, p, op in items:
        perm = PERM_OF.get((m, p))
        doc = (op.get("description") or op.get("summary") or "").split("\n")[0].replace("|", "/")
        out.append(f"| {m} | `{p}` | {roles_for(perm)}{f' (`{perm}`)' if perm else ''} | {doc} |")
    out.append("")
out += ["## Examples", "",
        "```bash", "# forecast for the latest 7-day issue, with provenance and freshness",
        "curl -s https://<host>/api/v1/forecast?h=7 | jq '.meta | {issue_date, valid_window, status, model_status}'", "",
        "# sign in and record an observation",
        "curl -c jar -H 'X-RINDA: 1' -H 'content-type: application/json' -d '{\"email\":\"...\",\"password\":\"...\"}' https://<host>/api/v1/auth/login",
        "curl -b jar -H 'X-RINDA: 1' -H 'content-type: application/json' -d @observation.json https://<host>/api/v1/observations",
        "```", "",
        "Observation body (fields beyond these are ignored):", "",
        "```json",
        '{"client_uuid": "6f0d...", "site_id": "G0221", "task_id": 12, "lat": -1.84774, "lon": 30.18259, "gps_accuracy_m": 6,',
        ' "observed_at": "2026-10-12T07:40:00Z", "water_present": "yes", "habitat_type": "rice_paddy",',
        ' "larvae_present": "yes", "dips": 10, "larvae_count": 14, "anopheles_present": "yes",',
        ' "species": ["an_gambiae_sl"], "stages": ["late_instar"], "pupae_present": "not_sampled"}',
        "```", "",
        "Field-station telemetry:", "",
        "```bash",
        "curl -H 'X-Station-Key: <key>' -H 'content-type: application/json' \\",
        "  -d '{\"seq\":18234,\"ts_utc\":\"2026-01-19T06:00:00Z\",\"rain_mm\":0.4,\"water_level_cm\":7.5}' https://<host>/api/v1/telemetry/ESP32-GKM-01",
        "```", "",
        "## Integration status", "",
        "No integration with a national surveillance or reporting system (for example DHIS2 / HMIS) has been built or tested. "
        "It should be specified only after the partner confirms the target system, authorisation and data-sharing terms."]
(ROOT / "docs").mkdir(exist_ok=True)
(ROOT / "docs" / "api.md").write_text("\n".join(out) + "\n", encoding="utf-8")
print(f"wrote docs/api.md: {sum(len(v) for v in by_tag.values())} operations")

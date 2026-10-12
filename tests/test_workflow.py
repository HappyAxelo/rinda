"""End-to-end field journey and permission enforcement.

Forecast → alert → inspection task → observation → review → authorised
intervention → treatment recorded → follow-up → closed, with each role doing
only what it may.
"""
from datetime import datetime, timedelta, timezone

from conftest import W, client_as


def iso(minutes_ago=0):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()


def obs(site="G0221", **kw):
    base = {"client_uuid": f"u-{datetime.now().timestamp()}-{kw.get('tag', '')}", "site_id": site,
            "lat": -1.84774, "lon": 30.18259, "gps_accuracy_m": 6, "observed_at": iso(5),
            "water_present": "yes", "habitat_type": "rice_paddy", "larvae_present": "yes", "dips": 10,
            "larvae_count": 14, "anopheles_present": "yes", "species": ["an_gambiae_sl"], "stages": ["late_instar"]}
    kw.pop("tag", None)
    return base | kw


def test_anonymous_can_read_but_not_write(app):
    anon = client_as(None)
    assert anon.get("/api/v1/forecast?h=7").status_code == 200
    assert anon.get("/api/v1/observations").status_code == 401
    assert anon.post("/api/v1/observations", json=obs(tag="anon"), headers=W).status_code == 401
    assert anon.get("/api/v1/admin/users").status_code == 401
    me = anon.get("/api/v1/auth/me").json()
    assert me["authenticated"] is False and me["role"] == "viewer"


def test_role_matrix_enforced_on_server(roles):
    v, fo, an = roles["viewer"], roles["field_officer"], roles["analyst"]
    assert v.get("/api/v1/tasks").status_code == 403
    assert fo.post("/api/v1/tasks", json={"site_id": "G0221", "due_date": "2026-12-01", "assignee_id": 1}, headers=W).status_code == 403
    assert an.post("/api/v1/interventions", json={"observation_id": 1, "method": "bti_ground", "protocol_ref": "SOP-1"}, headers=W).status_code == 403
    assert fo.get("/api/v1/admin/status").status_code == 403
    assert an.get("/api/v1/field/export/observations.csv").status_code == 403


def test_full_field_journey(roles):
    sup, fo = roles["supervisor"], roles["field_officer"]
    fo_id = fo.get("/api/v1/auth/me").json()["id"]

    # 1. a high-priority alert on a historical issue
    alerts = sup.get("/api/v1/alerts?h=7&date=2025-11-26").json()
    a = alerts[0]
    assert a["category"] == "high" and a["expired"] is True and a["action"]

    # 2. supervisor assigns an inspection from the alert
    people = sup.get("/api/v1/field/assignees").json()
    assert any(p["id"] == fo_id for p in people)
    t = sup.post("/api/v1/tasks", json={"site_id": a["site_id"], "due_date": "2026-12-31", "assignee_id": fo_id,
                                         "priority": "high", "alert_id": a["id"], "issue_date": a["issue_date"], "horizon": 7}, headers=W)
    assert t.status_code == 201
    tid = t.json()["id"]
    assert next(x for x in sup.get("/api/v1/alerts?h=7&date=2025-11-26").json() if x["id"] == a["id"])["state"] == "actioned"

    # 3. field officer sees only their task, starts it, records a finding near the site
    mine = fo.get("/api/v1/tasks").json()
    assert all(x["assignee_id"] == fo_id for x in mine)
    assert fo.post(f"/api/v1/tasks/{tid}/status", json={"status": "in_progress"}, headers=W).status_code == 200
    site = sup.get(f"/api/v1/site/{a['site_id']}").json()
    o = fo.post("/api/v1/observations", json=obs(a["site_id"], task_id=tid, lat=site["lat"], lon=site["lon"], tag="j1"), headers=W)
    assert o.status_code == 201, o.text
    oid = o.json()["id"]
    assert next(x for x in fo.get("/api/v1/tasks").json() if x["id"] == tid)["status"] == "completed"

    # 4. intervention is refused before verification, and the observer cannot self-verify
    assert sup.post("/api/v1/interventions", json={"observation_id": oid, "method": "bti_ground", "protocol_ref": "NMCP-LSM-SOP-4"}, headers=W).status_code == 409
    sup_obs = sup.post("/api/v1/observations", json=obs(a["site_id"], tag="selfcheck"), headers=W).json()["id"]
    assert sup.post(f"/api/v1/observations/{sup_obs}/review", json={"decision": "verified"}, headers=W).status_code == 403

    # 5. supervisor verifies, then authorises with a protocol reference
    assert sup.post(f"/api/v1/observations/{oid}/review", json={"decision": "verified"}, headers=W).status_code == 200
    iv = sup.post("/api/v1/interventions", json={"observation_id": oid, "method": "bti_ground", "protocol_ref": "NMCP-LSM-SOP-4"}, headers=W)
    assert iv.status_code == 201
    iid = iv.json()["id"]

    # 6. field officer records the treatment; follow-up must come after it
    assert fo.post(f"/api/v1/interventions/{iid}/performed", json={"performed_at": iso(3), "quantity_used": "0.2 kg Bti in 8 L"}, headers=W).status_code == 200
    early = fo.post("/api/v1/observations", json=obs(a["site_id"], observed_at=iso(10), larvae_count=0, larvae_present="no", anopheles_present="no", species=[], stages=[], lat=site["lat"], lon=site["lon"], tag="early"), headers=W).json()["id"]
    sup.post(f"/api/v1/observations/{early}/review", json={"decision": "verified"}, headers=W)
    assert sup.post(f"/api/v1/interventions/{iid}/followup", json={"observation_id": early, "outcome": "larvae_absent"}, headers=W).status_code == 422
    later = fo.post("/api/v1/observations", json=obs(a["site_id"], observed_at=iso(0), larvae_present="no", larvae_count=0, anopheles_present="no", species=[], stages=[], lat=site["lat"], lon=site["lon"], tag="later"), headers=W).json()["id"]
    sup.post(f"/api/v1/observations/{later}/review", json={"decision": "verified"}, headers=W)
    assert sup.post(f"/api/v1/interventions/{iid}/followup", json={"observation_id": later, "outcome": "larvae_absent"}, headers=W).status_code == 200
    closed = next(x for x in sup.get("/api/v1/interventions").json() if x["id"] == iid)
    assert closed["status"] == "closed" and closed["demo"] == 1

    # 7. authorised export carries the demonstration label; the site dossier shows the evidence
    csv = sup.get("/api/v1/field/export/interventions.csv").text
    assert "DEMONSTRATION" in csv
    assert sup.get(f"/api/v1/site/{a['site_id']}").json()["field"]["interventions"]
    assert roles["viewer"].get(f"/api/v1/site/{a['site_id']}").json()["field"]["restricted"] is True


def test_observation_quality_rules(roles):
    fo = roles["field_officer"]
    bad = [obs(larvae_present="no", larvae_count=3, tag="q1"), obs(larvae_present="yes", dips=None, tag="q2"),
           obs(observed_at=(datetime.now(timezone.utc) + timedelta(days=1)).isoformat(), tag="q3"),
           obs(water_present="no", tag="q4"), obs(species=["mosquito"], tag="q5")]
    for b in bad:
        assert fo.post("/api/v1/observations", json=b, headers=W).status_code == 422
    far = fo.post("/api/v1/observations", json=obs(lat=-1.80, lon=30.10, tag="far"), headers=W).json()
    assert far["status"] == "needs_review" and any(f.startswith("gps_") for f in far["quality_flags"])
    # "not sampled" is accepted without dips and is not turned into "no larvae"
    ns = fo.post("/api/v1/observations", json=obs(larvae_present="not_sampled", dips=None, larvae_count=None, anopheles_present="not_identified", species=[], stages=[], tag="ns"), headers=W)
    assert ns.status_code == 201
    rec = next(x for x in fo.get("/api/v1/observations").json() if x["id"] == ns.json()["id"])
    assert rec["larvae_present"] == "not_sampled"


def test_offline_resubmission_is_idempotent(roles):
    fo = roles["field_officer"]
    o = obs(tag="dup") | {"client_uuid": "offline-queue-0001"}
    a = fo.post("/api/v1/observations", json=o, headers=W).json()
    b = fo.post("/api/v1/observations", json=o, headers=W).json()
    assert a["id"] == b["id"] and b["duplicate"] is True


def test_alert_dismissal_needs_reason(roles):
    sup = roles["supervisor"]
    aid = sup.get("/api/v1/alerts?h=7&date=2025-10-15").json()[0]["id"]
    assert sup.post(f"/api/v1/alerts/{aid}", json={"state": "dismissed"}, headers=W).status_code == 422
    assert sup.post(f"/api/v1/alerts/{aid}", json={"state": "dismissed", "note": "inspected under routine round"}, headers=W).status_code == 200


def test_admin_user_lifecycle_and_password_login(roles, app):
    adm = client_as(None)
    r = adm.post("/api/v1/auth/login", json={"email": "admin@rinda.local", "password": "Test-Admin-Password-1"}, headers=W)
    assert r.status_code == 200 and r.json()["role"] == "admin"
    weak = adm.post("/api/v1/admin/users", json={"email": "fo1@rbc.gov.rw", "name": "Field One", "role": "field_officer", "password": "short"}, headers=W)
    assert weak.status_code == 422
    ok = adm.post("/api/v1/admin/users", json={"email": "fo1@rbc.gov.rw", "name": "Field One", "role": "field_officer", "password": "Strong-Pass-123"}, headers=W)
    assert ok.status_code == 201
    uid = ok.json()["id"]
    fo = client_as(None)
    assert fo.post("/api/v1/auth/login", json={"email": "fo1@rbc.gov.rw", "password": "Strong-Pass-123"}, headers=W).status_code == 200
    assert fo.get("/api/v1/tasks").status_code == 200
    assert adm.patch(f"/api/v1/admin/users/{uid}", json={"active": False}, headers=W).status_code == 200
    assert fo.get("/api/v1/tasks").status_code == 401  # session revoked on deactivation
    assert client_as(None).post("/api/v1/auth/login", json={"email": "fo1@rbc.gov.rw", "password": "wrong"}, headers=W).status_code == 401


def test_backup_produces_restorable_copy(roles):
    import sqlite3
    import main
    adm = roles["admin"]
    b = adm.post("/api/v1/admin/backup", headers=W).json()
    path = main.VAR / "backups" / b["file"]
    with sqlite3.connect(path) as c:
        assert c.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] >= 4
        assert c.execute("SELECT COUNT(*) FROM users").fetchone()[0] >= 5
    assert adm.get(f"/api/v1/admin/backup/{b['file']}").status_code == 200
    assert adm.get("/api/v1/admin/backup/..%2Fusers.db").status_code == 404


def test_login_rate_limited():
    c = client_as(None)
    codes = [c.post("/api/v1/auth/login", json={"email": "x@y.z", "password": "nope"}, headers=W).status_code for _ in range(12)]
    assert 429 in codes

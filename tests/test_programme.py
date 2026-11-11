"""Programme uptake: field-registered habitats, survey fields, RBC roles,
monitoring-and-evaluation indicators, weekly report, DHIS2 export, outbox,
language preference and the offline shell."""
from datetime import date

from conftest import W, client_as
from test_workflow import iso, obs


def _week_of_today():
    y, w, _ = date.today().isocalendar()
    return f"{y}-W{w:02d}", f"{y}W{w}"


def test_new_habitat_is_registered_and_can_be_followed_up(roles):
    fo, sup = roles["field_officer"], roles["supervisor"]
    r = fo.post("/api/v1/observations", json=obs(site=None, tag="hab1", new_habitat=True, habitat_type="ditch_drain",
                                                 habitat_description="ditch behind the health post", lat=-1.8601, lon=30.2012,
                                                 habitat_permanence="semi_permanent", sun_exposure="sunlit",
                                                 distance_to_house_m=40), headers=W)
    assert r.status_code == 201, r.text
    j = r.json()
    code = j["habitat_code"]
    assert code and code.startswith("H") and j["status"] == "needs_review" and "new_habitat" in j["quality_flags"]
    habs = fo.get("/api/v1/habitats").json()
    assert any(h["code"] == code and h["permanence"] == "semi_permanent" for h in habs)
    # a later visit to the same habitat by its code, at the same place: no distance flag
    r2 = fo.post("/api/v1/observations", json=obs(site=code, tag="hab2", lat=-1.8601, lon=30.2012), headers=W)
    assert r2.status_code == 201 and r2.json()["quality_flags"] == []
    # a supervisor can assign a follow-up inspection to the habitat code
    fo_id = fo.get("/api/v1/auth/me").json()["id"]
    assert sup.post("/api/v1/tasks", json={"site_id": code, "due_date": "2026-12-31", "assignee_id": fo_id}, headers=W).status_code == 201


def test_new_habitat_needs_gps(roles):
    r = roles["field_officer"].post("/api/v1/observations", json=obs(site=None, tag="hab3", new_habitat=True, lat=None, lon=None), headers=W)
    assert r.status_code == 422 and "GPS" in r.text


def test_observation_needs_a_site_or_new_habitat(roles):
    r = roles["field_officer"].post("/api/v1/observations", json=obs(site=None, tag="nosite"), headers=W)
    assert r.status_code == 422


def test_larvae_per_dip_is_derived(roles):
    fo, sup = roles["field_officer"], roles["supervisor"]
    oid = fo.post("/api/v1/observations", json=obs(tag="dens", dips=8, larvae_count=20), headers=W).json()["id"]
    o = next(x for x in sup.get("/api/v1/observations").json() if x["id"] == oid)
    assert o["larvae_per_dip"] == 2.5
    neg = fo.post("/api/v1/observations", json=obs(tag="neg", larvae_present="no", larvae_count=0, anopheles_present="no",
                                                   species=[], stages=[]), headers=W).json()["id"]
    assert next(x for x in sup.get("/api/v1/observations").json() if x["id"] == neg)["larvae_per_dip"] == 0.0


def test_programme_manager_sees_aggregates_not_records(app):
    pm = client_as("programme_manager")
    me = pm.get("/api/v1/auth/me").json()
    assert me["role"] == "programme_manager" and "RBC" in me["role_title"]
    assert pm.get("/api/v1/me/indicators").status_code == 200
    assert pm.get("/api/v1/reports/weekly").status_code == 200
    assert pm.get("/api/v1/observations").status_code == 403
    assert pm.get("/api/v1/tasks").status_code == 403
    assert pm.post("/api/v1/interventions", json={"observation_id": 1, "method": "bti_ground", "protocol_ref": "SOP-1", "habitat_suitability": "suitable_for_ground"},
                   headers=W).status_code == 403


def test_role_titles_offered_for_every_role(app):
    roles = client_as(None).get("/api/v1/auth/me").json()["roles"]
    assert {r["role"] for r in roles} >= {"supervisor", "programme_manager"}
    assert all(r["title"] for r in roles)


def test_me_permissions(roles):
    assert roles["field_officer"].get("/api/v1/me/indicators").status_code == 403
    assert roles["viewer"].get("/api/v1/reports/weekly").status_code == 403
    assert roles["analyst"].get("/api/v1/me/indicators").status_code == 200
    assert roles["analyst"].get("/api/v1/reports/dhis2").status_code == 403


def test_indicators_never_turn_missing_data_into_zero(roles):
    r = roles["supervisor"].get("/api/v1/me/indicators?weeks=4").json()
    assert [s["key"] for s in r["sections"]] == ["reach", "effectiveness", "adoption", "implementation", "maintenance"]
    for s in r["sections"]:
        for i in s["indicators"]:
            if i["unit"] == "share":
                if i["denominator"] == 0:
                    assert i["value"] is None and i["status"] == "no_data"
                else:
                    assert i["value"] == round(i["numerator"] / i["denominator"], 3)
    assert any("Malaria incidence" in x for x in r["not_measured"])
    assert len(r["weekly"]) == 4


def test_weekly_report_counts_this_weeks_records(roles):
    fo, sup = roles["field_officer"], roles["supervisor"]
    week, period = _week_of_today()
    before = sup.get(f"/api/v1/reports/weekly?week={week}").json()
    fo.post("/api/v1/observations", json=obs(site="G0300", tag="wk", observed_at=iso(1)), headers=W)
    after = sup.get(f"/api/v1/reports/weekly?week={week}").json()
    assert after["period"] == period
    assert after["counts"]["lsm_inspections_completed"] == before["counts"]["lsm_inspections_completed"] + 1
    assert after["counts"]["lsm_sites_larvae_positive"] >= 1
    assert "not validated for intervention" in after["statement"]
    html = sup.get(f"/api/v1/reports/weekly.html?week={week}&lang=rw").text
    assert "DEMONSTRATION DATA" in html and "AGATEGANYO" in html
    assert sup.get("/api/v1/reports/weekly?week=nonsense").status_code == 422


def test_dhis2_export_is_marked_pending_and_never_importable_by_accident(roles):
    sup = roles["supervisor"]
    week, period = _week_of_today()
    j = sup.get(f"/api/v1/reports/dhis2?week={week}").json()
    assert j["period"] == period and j["_rinda"]["mapping_status"] == "pending"
    assert all(v["dataElement"].startswith("UNMAPPED:") for v in j["dataValues"])
    assert all(v["comment"] == "DEMONSTRATION DATA - DO NOT IMPORT" for v in j["dataValues"])
    csv = sup.get(f"/api/v1/reports/dhis2?week={week}&format=csv")
    assert csv.headers["content-type"].startswith("text/csv")
    assert csv.text.splitlines()[0].startswith("dataelement,period,orgunit")


def test_task_assignment_drafts_a_message_that_is_never_sent_automatically(roles):
    sup, fo = roles["supervisor"], roles["field_officer"]
    fo_id = fo.get("/api/v1/auth/me").json()["id"]
    t = sup.post("/api/v1/tasks", json={"site_id": "G0221", "due_date": "2026-12-31", "assignee_id": fo_id}, headers=W).json()
    box = sup.get("/api/v1/messages").json()
    assert box["provider_status"] == "not_connected"
    m = next(x for x in box["messages"] if x["id"] == t["message_id"])
    assert m["kind"] == "task_notice" and m["status"] == "draft" and f"T{t['id']}" in m["body"]
    assert sup.post(f"/api/v1/messages/{m['id']}", json={"status": "sent_manually"}, headers=W).status_code == 200
    assert sup.post(f"/api/v1/messages/{m['id']}", json={"status": "cancelled"}, headers=W).status_code == 409
    assert fo.get("/api/v1/messages").status_code == 403


def test_weekly_digest_uses_each_persons_language(roles):
    sup = roles["supervisor"]
    assert sup.post("/api/v1/auth/preferences", json={"language": "rw"}, headers=W).status_code == 200
    d = sup.post("/api/v1/messages/digest", json={}, headers=W)
    assert d.status_code == 201 and d.json()["drafted"] >= 1
    msgs = sup.get("/api/v1/messages").json()["messages"]
    mine = [m for m in msgs if m["id"] in d.json()["ids"] and m["recipient_label"] == "Demo supervisor"]
    assert mine and mine[0]["language"] == "rw" and "igenzura" in mine[0]["body"]
    sup.post("/api/v1/auth/preferences", json={"language": "en"}, headers=W)
    assert sup.post("/api/v1/auth/preferences", json={"language": "fr"}, headers=W).status_code == 422


def test_admin_records_district_phone_and_language(roles):
    adm = roles["admin"]
    r = adm.post("/api/v1/admin/users", json={"email": "dvco.gasabo@example.rw", "name": "Gasabo DVCO", "role": "supervisor",
                                              "district": "Gasabo", "phone": "+250 788 000 111", "language": "rw",
                                              "password": "Strong-Password-123"}, headers=W)
    assert r.status_code == 201, r.text
    u = next(x for x in adm.get("/api/v1/admin/users").json() if x["email"] == "dvco.gasabo@example.rw")
    assert u["district"] == "Gasabo" and u["language"] == "rw" and u["role_title"] == "District Vector Control Officer"
    bad = adm.post("/api/v1/admin/users", json={"email": "x@example.rw", "name": "X", "role": "viewer", "phone": "call me",
                                                "password": "Strong-Password-123"}, headers=W)
    assert bad.status_code == 422


def test_offline_shell_is_served(app):
    sw = app.get("/sw.js")
    assert sw.status_code == 200 and "rinda-shell" in sw.text and sw.headers["cache-control"] == "no-cache"
    mf = app.get("/manifest.webmanifest")
    assert mf.status_code == 200 and mf.json()["short_name"] == "RINDA"

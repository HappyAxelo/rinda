"""Forecast, provenance and platform guarantees, exercised through the API."""
from conftest import W


def test_health_and_readiness_are_separate(app):
    assert app.get("/api/v1/health").json()["status"] == "ok"
    r = app.get("/api/v1/ready").json()
    assert r["status"] == "ready" and r["engine"] and r["database"]
    assert "log" not in app.get("/api/v1/health").json()  # no internal detail leaks


def test_head_root_for_uptime_monitors(app):
    assert app.head("/").status_code == 200


def test_legacy_api_alias_still_works(app):
    assert app.get("/api/forecast?h=7").status_code == 200


def test_served_model_is_v3_and_beats_persistence(app):
    m = app.get("/api/v1/bootstrap").json()["model"]
    assert m["model_version"] == "v3"
    for h in ("7", "14"):
        sc = m["horizons"][h]["test_scores"]
        assert sc["pr_auc"] > sc["B1_pr_auc"]


def test_forecast_carries_provenance_and_status(app):
    f = app.get("/api/v1/forecast?h=7").json()
    m = f["meta"]
    assert "No entomological claim" in f["claim"]
    for k in ("issue_date", "target_date", "valid_window", "generated_utc", "inputs", "model", "spatial_resolution", "units"):
        assert k in m
    assert m["model_status"]["status"] == "research_stage"
    # the archive ends in January 2026: it must be flagged, never shown as current
    assert m["status"] == "stale" and any(w["code"] == "stale_forecast" for w in m["warnings"])
    assert m["inputs"]["radar_last_pass"] <= m["issue_date"] and m["inputs"]["weather_last_day"] <= m["issue_date"]


def test_14_day_forecast_carries_horizon_caution(app):
    m = app.get("/api/v1/forecast?h=14").json()["meta"]
    assert any(w["code"] == "long_horizon" for w in m["warnings"])


def test_categories_follow_documented_rules(app):
    f = app.get("/api/v1/forecast?h=7&date=2025-11-26").json()
    rule = {"formation": "high", "uncertain": "insufficient", "no_formation": "lower"}
    assert all(r["category"] == rule[r["conformal_decision"]] for r in f["rows"])
    assert f["summary"]["categories"]["moderate"] == 0  # no threshold agreed yet


def test_research_stage_reason_cites_baseline(app):
    ms = app.get("/api/v1/bootstrap").json()["horizons"]["7"]["model_status"]
    assert ms["status"] == "research_stage" and "persistence" in ms["reasons"][0].lower()
    assert ms["outperforms_baseline"] is (ms["model_pr_auc"] > ms["best_baseline_pr_auc"])


def test_replay_uses_only_out_of_sample_issues(app):
    r = app.get("/api/v1/replay?h=7").json()
    issues = [p["date"] for p in r["per_issue"]]
    assert min(issues) > "2025-08-31"  # after the calibration window
    o = r["overall"]["7"]
    assert o["hits"] + o["missed_while_insufficient"] + o["missed_while_lower"] == o["observed_formation"]


def test_unknown_api_path_is_json_404(app):
    r = app.get("/api/v1/nope")
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"


def test_validation_errors_are_uniform(app):
    r = app.get("/api/v1/forecast?h=9")
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid"


def test_writes_need_csrf_header(roles):
    r = roles["supervisor"].post("/api/v1/missions/plan", json={"h": 7, "save": True})
    assert r.status_code == 403 and r.json()["error"]["code"] == "csrf"


def test_security_headers(app):
    h = app.get("/").headers
    assert h["x-content-type-options"] == "nosniff" and "frame-ancestors" in h["content-security-policy"]


def test_plan_respects_endurance_and_locks_uncertain_targets(app):
    res = app.post("/api/v1/missions/plan", json={"h": 7, "date": "2025-11-26", "policy": {"max_targets": 40}}, headers=W).json()
    assert res["totals"]["infeasible_sorties"] == 0
    for s in res["sorties"]:
        for w in s["waypoints"]:
            if w["decision"] == "uncertain":
                assert w["action"] == "VERIFY_ONLY"


def test_flight_files_only_after_supervisor_approval(roles):
    an, sup = roles["analyst"], roles["supervisor"]
    mid = an.post("/api/v1/missions/plan", json={"h": 7, "date": "2025-11-26", "save": True}, headers=W).json()["id"]
    assert an.get(f"/api/v1/missions/{mid}/export?fmt=plan").status_code == 409
    assert an.post(f"/api/v1/missions/{mid}/status", json={"status": "APPROVED"}, headers=W).status_code == 403
    assert sup.post(f"/api/v1/missions/{mid}/status", json={"status": "APPROVED"}, headers=W).status_code == 200
    assert an.get(f"/api/v1/missions/{mid}/export?fmt=wpl").text.startswith("QGC WPL 110")
    plan = an.get(f"/api/v1/missions/{mid}/export?fmt=plan").json()
    assert plan["fileType"] == "Plan" and plan["mission"]["items"][-1]["command"] == 20
    # skipping a state is refused
    assert sup.post(f"/api/v1/missions/{mid}/status", json={"status": "COMPLETED"}, headers=W).status_code == 409


def test_synthetic_logs_are_stamped(roles):
    an = roles["analyst"]
    mid = an.get("/api/v1/missions").json()[0]["id"]
    an.post(f"/api/v1/missions/{mid}/synthetic-log", headers=W)
    assert "SYNTHETIC" in an.get("/api/v1/flightlogs/analysis").json()["provenance"]


def test_demo_accounts_cannot_create_field_flight_logs(roles):
    csv = "mission_id,sortie_id,ts_utc,lat,lon,ground_speed_ms,altitude_m,t_cap_ms,t_pre_ms,t_inf_ms,t_dec_ms,t_act_ms,deadline_ms,confidence,decision,sprayed_ml\nM1,S01,2026-01-01T00:00:00Z,-1.9,30.2,3,6,30,8,40,4,60,1000,0.9,SPRAY,800\n"
    r = roles["analyst"].post("/api/v1/flightlogs", files={"file": ("f.csv", csv)}, data={"provenance": "FIELD"}, headers=W)
    assert r.status_code == 200
    assert roles["analyst"].get("/api/v1/flightlogs").json()[0]["provenance"] == "SYNTHETIC"


def test_placeholder_station_keys_never_authenticate(app):
    r = app.post("/api/v1/telemetry/ESP32-GKM-01", json={"seq": 1}, headers={"X-Station-Key": "replace-me-01"})
    assert r.status_code == 401


def test_scenario_and_bulletin(app):
    s = app.get("/api/v1/scenario?site=G0221&date=2025-11-26&h=7&rain=2").json()
    assert s["available"] and len(s["curve"]) == 31
    b = app.get("/api/v1/bulletin?h=7").text
    assert "Habitat Formation Bulletin" in b and "HISTORICAL FORECAST" in b and "research stage" in b


def test_upload_size_limit(roles):
    big = "a," * (11 * 1024 * 1024)
    r = roles["analyst"].post("/api/v1/score?h=7", files={"file": ("x.csv", big)}, headers=W)
    assert r.status_code == 413


def test_latest_issue_explains_and_serialises(app):
    """Regression: missing inputs travel as null, never as NaN, on the latest issue."""
    f = app.get("/api/v1/forecast?h=7").json()
    site, date = f["rows"][0]["site_id"], f["summary"]["issue_date"]
    s = app.get(f"/api/v1/scenario?site={site}&date={date}&h=7&rain=1&temp=0")
    assert s.status_code == 200 and s.json()["available"]
    e = app.get(f"/api/v1/explain?site={site}&date={date}&h=7")
    assert e.status_code == 200 and e.json()["available"] and e.json()["drivers"]

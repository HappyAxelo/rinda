"""2026-11 rebuild: surveillance map, dossier, datasets, training, connectors,
intervention rules, drone missions, reporting. Exercised through the API."""
import io
import json
import sqlite3

import numpy as np
import pandas as pd
import pytest
from conftest import W, client_as
from test_workflow import iso, obs

ALLOWED_FRESH = {"Current", "Delayed", "Stale", "Unavailable", "Processing", "Historical"}


@pytest.fixture(scope="module")
def pm(app):
    return client_as("programme_manager")


def site_pos(c, sid):
    s = c.get(f"/api/v1/site/{sid}").json()
    return s["lat"], s["lon"]


def verified_obs(roles, sid, tag, **kw):
    fo, sup = roles["field_officer"], roles["supervisor"]
    lat, lon = site_pos(sup, sid)
    r = fo.post("/api/v1/observations", json=obs(sid, lat=lat, lon=lon, tag=tag, **kw), headers=W)
    assert r.status_code == 201, r.text
    oid = r.json()["id"]
    assert sup.post(f"/api/v1/observations/{oid}/review", json={"decision": "verified"}, headers=W).status_code == 200
    return oid


# ------------------------------------------------------------- migrations
def test_migration_6_is_additive_and_keeps_existing_rows(tmp_path):
    import db
    c = sqlite3.connect(tmp_path / "old.db")
    c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE schema_version (version INTEGER PRIMARY KEY, name TEXT, applied_utc TEXT)")
    for v, name, sql in db.MIGRATIONS[:5]:
        c.executescript(sql)
        c.execute("INSERT INTO schema_version VALUES (?,?,?)", (v, name, "t"))
    c.execute("INSERT INTO observations (client_uuid, site_id, observed_at, water_present, larvae_present, status) VALUES ('keep-me','G0001','2026-01-01','yes','no','verified')")
    c.execute("INSERT INTO missions (id, status, params, result) VALUES ('M-OLD','PLANNED','{}','{}')")
    c.commit()
    db._migrate(c)
    assert c.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == 6
    assert c.execute("SELECT site_id FROM observations WHERE client_uuid='keep-me'").fetchone()[0] == "G0001"
    assert c.execute("SELECT kind FROM missions WHERE id='M-OLD'").fetchone()[0] == "inspection"
    tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"data_sources", "ingestion_runs", "environmental_observations", "entomological_samples", "raster_products",
            "administrative_areas", "dataset_versions", "training_runs", "model_registry"} <= tables


def test_api_compatibility_with_previous_release(app):
    for u in ["/api/v1/bootstrap", "/api/v1/forecast?h=7", "/api/v1/forecast/meta", "/api/v1/verification?h=7", "/api/v1/site/G0221", "/api/v1/replay?h=7",
              "/api/v1/meteo/summary", "/api/v1/assurance", "/api/v1/evidence", "/api/v1/governance", "/api/v1/missions", "/api/v1/flightlogs",
              "/api/v1/telemetry", "/api/v1/alerts?h=7", "/api/v1/field/summary", "/api/forecast?h=14"]:
        assert app.get(u).status_code == 200, u


# ------------------------------------------------------- roles and privacy
def test_new_permissions_enforced(roles, pm):
    v, fo, an, adm = roles["viewer"], roles["field_officer"], roles["analyst"], roles["admin"]
    for u in ["/api/v1/datasets", "/api/v1/models", "/api/v1/training/runs", "/api/v1/connectors"]:
        assert v.get(u).status_code == 403 and fo.get(u).status_code == 403
        assert an.get(u).status_code == 200
    assert an.post("/api/v1/models/v3-h7/decision", json={"status": "archived", "note": "not my call"}, headers=W).status_code == 403
    assert v.get("/api/v1/reports/indicators").status_code == 403
    assert pm.get("/api/v1/reports/indicators").status_code == 200
    assert adm.get("/api/v1/models").status_code == 200


def test_programme_manager_and_public_get_aggregates_not_field_records(app, pm):
    for c in (pm, client_as(None)):
        assert c.get("/api/v1/map/entomology").json()["restricted"] is True
        ops = c.get("/api/v1/map/operations").json()
        assert ops["restricted"] is True and ops["points"] == []
        d = c.get("/api/v1/dossier/G0442").json()
        assert d["restricted"] is True and "observations" not in d and d["audit_history"] is None


# ------------------------------------------------------ overview and geo
def test_missing_data_is_never_zero(app):
    c = app.get("/api/v1/overview?district=Bugesera").json()
    assert c["coverage"]["onboarded"] is False
    assert all(v is None for v in c["counts"].values())
    o = app.get("/api/v1/overview").json()
    assert o["counts"]["registered_locations"] >= 600 and o["no_data_label"] == "No data available"
    assert o["coverage"]["operational_sectors"] == 1
    rep = client_as("analyst").get("/api/v1/reports/indicators").json()
    cost = next(i for g in rep["groups"] for i in g["indicators"] if i["id"] == "cost_treat")
    assert cost["value"] is None and cost["status"] == "no_data"


def test_geography_comes_from_boundaries(app):
    areas = app.get("/api/v1/geo/areas").json()
    assert [a["name"] for a in areas if a["onboarded"] and a["level"] == "sector"] == ["Gikomero"]
    assert not any(a["onboarded"] for a in areas if a["level"] in ("country", "province", "district"))
    assert [a for a in areas if a["level"] == "cell"] == []  # no redistributable cell boundaries yet
    assert app.get("/api/v1/geo/boundaries").json()["cells"] == []
    inside = app.get("/api/v1/dossier/G0442").json()["administrative"]
    assert inside["in_sector"] is True and inside["sector"] == "Gikomero" and inside["cell"] is None
    outside = app.get("/api/v1/dossier/G0221").json()["administrative"]
    assert outside["in_sector"] is False and outside["sector"] is None
    hits = app.get("/api/v1/search?q=G044").json()
    assert any(h["id"] == "G0442" and h["type"] == "site" for h in hits)
    assert not any(h["type"] == "habitat" for h in client_as(None).get("/api/v1/search?q=H0").json())


# ------------------------------------------------------------------ layers
def test_forecast_layer_labels_freshness_and_replay(app):
    f = app.get("/api/v1/map/forecast?h=7").json()
    assert f["state"] == ("Forecast stale" if f["age_days"] > 7 else "Research-only forecast")
    assert f["label"] == "Research-only forecast" and f["model"]["status"] == "Research only"
    assert app.get("/api/v1/map/forecast?h=7&date=2025-11-26").json()["state"] == "Forecast stale"
    assert f["audit"]["label_reconstruction_complete"] is False
    assert {r["label"] for r in f["rows"]} <= {"Potential surface-water formation signal", "Evidence insufficient", "Lower forecast priority", "Moderate forecast priority"}
    assert "breeding" not in " ".join(r["label"] for r in f["rows"]).lower()
    past = app.get("/api/v1/map/forecast?h=7&date=2026-03-10").json()
    assert past["issue_date"] <= "2026-03-10" and past["inputs"]["radar_last_pass"] <= past["issue_date"]
    none = app.get("/api/v1/map/forecast?h=7&date=2024-01-01").json()
    assert none["available"] is False and none["label"] == "Forecast unavailable" and none["rows"] == []
    assert app.get("/api/v1/map/forecast?h=9").status_code == 422


def test_earth_layer_is_observed_not_predicted(app):
    e = app.get("/api/v1/map/earth?date=2025-12-01&compare=2025-06-01").json()
    assert e["kind"] == "Satellite-observed" and e["date"] == "2025-12-01" and len(e["rows"]) >= 590
    assert all(r["pass_date"] <= "2025-12-01" for r in e["rows"])
    assert "not confirmed mosquito breeding" in e["caution"]
    assert any("changed" in r for r in e["rows"])
    p = e["products"][0]
    for k in ("product_id", "platform", "acquired_utc", "resolution_m", "processing_method", "storage_ref", "validation_status", "provenance"):
        assert p[k] is not None, k
    assert all(x["acquired_date"] <= "2025-12-01" for x in e["products"])
    assert app.get("/api/v1/earth/products?size=5").json()["total"] > 2000


def test_iot_shows_only_received_readings(app, monkeypatch):
    nodes = app.get("/api/v1/map/iot").json()["nodes"]
    assert nodes and all(n["status"] == "Not yet commissioned" and n["latest"] == [] for n in nodes)
    monkeypatch.setenv("RINDA_STATION_KEYS", json.dumps({"ESP32-TEST-01": "a-real-key-0123456789"}))
    body = {"seq": 1, "ts_utc": iso(2), "water_level_cm": 7.5, "water_temp_c": 22.1, "battery_v": 3.9}
    k = {"X-Station-Key": "a-real-key-0123456789"}
    first = app.post("/api/v1/telemetry/ESP32-TEST-01", json=body, headers=k).json()
    again = app.post("/api/v1/telemetry/ESP32-TEST-01", json=body, headers=k).json()
    assert first["readings"]["accepted"] == 3 and again["readings"]["accepted"] == 0  # duplicates are not stored twice
    n = app.get("/api/v1/iot/nodes/ESP32-TEST-01").json()
    assert n["status"] == "Online" and {r["variable"] for r in n["latest"]} == {"water_level_cm", "water_temp_c", "battery_v"}
    assert "Humidity" in n["missing_measurements"] and n["position"] is None
    assert app.get("/api/v1/iot/nodes/NOPE").status_code == 404


def test_meteo_layer_is_historical_and_admits_gaps(app):
    m = app.get("/api/v1/map/meteo?date=2026-09-30").json()
    assert m["state"] == "Historical" and m["observed_date"] == "2026-01-15" and m["lag_days"] > 200
    assert m["wind"] is None and m["forecasts"] == []
    assert app.get("/api/v1/map/meteo?date=2020-01-01").json()["available"] is False


def test_freshness_never_claims_real_time(app):
    f = app.get("/api/v1/freshness").json()["sources"]
    by = {x["key"]: x for x in f}
    assert set(by) == {"earth_observation", "iot", "meteorology", "entomology", "drone"}
    assert {x["label"] for x in f} <= ALLOWED_FRESH
    assert by["earth_observation"]["label"] == "Historical" and by["meteorology"]["label"] == "Historical"
    for k in ("last_ingestion", "last_observation", "expected_update_days", "availability", "processing_status", "validation_status"):
        assert k in by["iot"]
    assert by["earth_observation"]["connector_state"] == "not_configured"


# --------------------------------------------------------------- connectors
def test_ingestion_validates_normalises_and_deduplicates(roles):
    an = roles["analyst"]
    recs = [
        {"station_id": "MET-1", "observed_utc": "2026-01-10T06:00:00Z", "variable": "rainfall_mm", "value": 4.2, "unit": "mm", "lat": -1.88, "lon": 30.21},
        {"station_id": "MET-1", "observed_utc": "2026-01-10T06:00:00Z", "variable": "rainfall_mm", "value": 4.2, "unit": "mm"},        # duplicate
        {"station_id": "MET-1", "observed_utc": "2999-01-01T00:00:00Z", "variable": "rainfall_mm", "value": 1, "unit": "mm"},          # future
        {"station_id": "MET-1", "observed_utc": "2026-01-11T06:00:00Z", "variable": "rainfall_mm", "value": 1, "unit": "inches"},     # wrong unit
        {"station_id": "MET-1", "observed_utc": "2026-01-11T06:00:00Z", "variable": "air_temp_c", "value": 20, "unit": "degC", "lat": 48.8, "lon": 2.3},  # not Rwanda
    ]
    r = an.post("/api/v1/connectors/meteorology/records", json={"records": recs, "provenance": "REAL"}, headers=W).json()
    assert (r["accepted"], r["duplicate"], r["rejected"]) == (1, 1, 3)
    assert r["provenance"] == "SYNTHETIC"  # demonstration accounts cannot create real records
    run = an.get("/api/v1/connectors").json()["runs"][0]
    assert run["source_key"] == "meteorology" and run["n_rejected"] == 3
    un = an.post("/api/v1/connectors/earth_observation/run", headers=W).json()
    assert un["status"] == "unavailable"  # no credentials: nothing is fetched or simulated
    assert an.post("/api/v1/connectors/drone/records", json={"records": []}, headers=W).status_code == 422


# ----------------------------------------------------------------- datasets
def ento_csv(n=30, bad=False):
    rows = ["site_id,sample_date,sampling_method,dips,larvae_present,anopheles_larvae_count,species"]
    for i in range(n):
        d = "2999-01-01" if bad else f"2025-{1 + i % 12:02d}-{1 + i % 27:02d}"
        rows.append(f"G{i:04d},{d},standard_dipper_350ml,10,{'yes' if i % 3 == 0 else 'no'},{i % 5},an_gambiae_sl")
    return "\n".join(rows) + "\n" + rows[1] + "\n"  # one duplicate row


def test_dataset_upload_validation_confirmation_and_versioning(roles):
    an = roles["analyst"]
    up = an.post("/api/v1/datasets/upload", files={"file": ("ento.csv", ento_csv())}, data={"kind": "entomology", "label": "fixture", "provenance": "REAL"}, headers=W)
    assert up.status_code == 201, up.text
    d = up.json()
    assert d["status"] == "validated" and d["provenance"] == "SYNTHETIC"
    ids = {c["id"]: c["status"] for c in d["report"]["checks"]}
    assert {"file", "columns", "dates", "temporal", "geographic", "duplicates", "missing", "units"} <= set(ids) and ids["duplicates"] == "warn"
    assert an.post("/api/v1/datasets/upload", files={"file": ("ento.csv", ento_csv())}, data={"kind": "entomology"}, headers=W).status_code == 409  # same bytes
    conf = an.post(f"/api/v1/datasets/{d['id']}/confirm", headers=W).json()
    assert conf["status"] == "confirmed" and conf["dropped"] == 1 and conf["samples_loaded"] == 30
    assert an.post(f"/api/v1/datasets/{d['id']}/confirm", headers=W).status_code == 409
    v2 = an.post("/api/v1/datasets/upload", files={"file": ("ento2.csv", ento_csv(12))}, data={"kind": "entomology"}, headers=W).json()
    assert v2["version"] == d["version"] + 1
    bad = an.post("/api/v1/datasets/upload", files={"file": ("bad.csv", ento_csv(5, bad=True))}, data={"kind": "entomology"}, headers=W).json()
    assert bad["status"] == "failed_validation"
    assert an.post(f"/api/v1/datasets/{bad['id']}/confirm", headers=W).status_code == 409
    miss = an.post("/api/v1/datasets/upload", files={"file": ("m.csv", "a,b\n1,2\n")}, data={"kind": "rainfall"}, headers=W).json()
    assert miss["status"] == "failed_validation"
    assert an.post("/api/v1/datasets/upload", files={"file": ("x.exe", b"MZ")}, data={"kind": "rainfall"}, headers=W).status_code == 422
    assert roles["viewer"].post("/api/v1/datasets/upload", files={"file": ("e.csv", "a\n")}, data={"kind": "rainfall"}, headers=W).status_code == 403


# ----------------------------------------------------------------- training
def fixture_table(n_sites=60, n_days=120, seed=0):
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2025-01-01", periods=n_days, freq="D")
    df = pd.DataFrame({"site_id": np.repeat([f"S{i:03d}" for i in range(n_sites)], n_days), "date": np.tile(dates, n_sites)})
    df["x1"] = rng.normal(size=len(df))
    df["x2"] = rng.normal(size=len(df))
    df["WATER_FREQ_30D"] = rng.uniform(size=len(df))
    df["y"] = (df.x1 + 0.5 * rng.normal(size=len(df)) > 1.2).astype(int)
    return df


def test_temporal_split_has_an_embargo_and_no_overlap():
    import training as T
    df = fixture_table()
    for h in (7, 14):
        sp = T.temporal_splits(df["date"], h)
        d = pd.to_datetime(df["date"])
        assert not (sp["train"] & sp["cal"]).any() and not (sp["cal"] & sp["test"]).any() and not (sp["train"] & sp["test"]).any()
        assert sp["embargo_days"] == h + 2
        # the outcome of the last training row is known before calibration begins, and likewise for the test window
        assert d[sp["train"]].max() + pd.Timedelta(days=h) < d[sp["cal"]].min()
        assert d[sp["cal"]].max() + pd.Timedelta(days=h) < d[sp["test"]].min()
    with pytest.raises(ValueError):
        T.temporal_splits(df["date"], 7, train_end="2025-04-01", cal_end="2025-03-01")


def test_training_is_reproducible_and_reports_baselines():
    import training as T
    df = fixture_table()
    a = T.train(df, "A", 7, {"n_estimators": 30}, seed=7)
    b = T.train(df, "A", 7, {"n_estimators": 30}, seed=7)
    assert a["predictions"].equals(b["predictions"])
    m = a["metrics"]
    for k in ("pr_auc", "precision", "recall", "f1", "brier", "ece", "confusion"):
        assert k in m["model"]
    assert "accuracy" not in m["model"]
    assert {"B0_training_base_rate", "B1_recent_water_frequency_30d"} <= set(m["baselines"])
    assert m["model"]["pr_auc"] > m["baselines"]["B0_training_base_rate"]["pr_auc"]
    assert m["geographic_holdout"]["folds"] == 3 and m["reliability"]
    assert set(a["features"]) == {"x1", "x2", "WATER_FREQ_30D"}  # label and identifiers are never features
    assert T.choose_features(df.assign(future_rain=1.0, y_next=1.0), None) == a["features"]


def test_training_api_guards_and_model_gate(roles, monkeypatch):
    import surveillance as V
    import training as T
    from db import now, tx
    an, adm = roles["analyst"], roles["admin"]
    started = []
    monkeypatch.setattr(T, "start_job", lambda rid: started.append(rid))
    ds = an.get("/api/v1/datasets?kind=training_table").json()["rows"]
    t7 = next(d for d in ds if d["horizon"] == 7 and d["provenance"] == "STUDY")
    assert an.post("/api/v1/training/runs", json={"target": "D", "horizon": 7, "dataset_id": t7["id"]}, headers=W).status_code == 409  # targets are never mixed
    assert an.post("/api/v1/training/runs", json={"target": "A", "horizon": 14, "dataset_id": t7["id"]}, headers=W).status_code == 409
    assert an.post("/api/v1/training/runs", json={"target": "A", "horizon": 10, "dataset_id": t7["id"]}, headers=W).status_code == 422
    ento = next(d for d in an.get("/api/v1/datasets?kind=entomology").json()["rows"] if d["status"] == "confirmed")
    assert an.post("/api/v1/training/runs", json={"target": "A", "horizon": 7, "dataset_id": ento["id"]}, headers=W).status_code == 409
    ok = an.post("/api/v1/training/runs", json={"target": "A", "horizon": 7, "dataset_id": t7["id"], "seed": 3}, headers=W)
    assert ok.status_code == 201 and started == [ok.json()["id"]]
    assert an.post("/api/v1/training/runs", json={"target": "A", "horizon": 7, "dataset_id": t7["id"]}, headers=W).status_code == 409  # one at a time
    with tx() as c:
        c.execute("UPDATE training_runs SET status='failed' WHERE id=?", (ok.json()["id"],))
        for mid, status in (("A-h7-test-candidate", "candidate"), ("A-h7-test-synthetic", "synthetic_fixture")):
            c.execute("INSERT OR REPLACE INTO model_registry (id, target, horizon, version, status, metrics, created_utc) VALUES (?,?,?,?,?,?,?)", (mid, "A", 7, "t", status, "{}", now()))
    reg = an.get("/api/v1/models").json()
    assert reg["displayed"] == "v3"
    by = {m["id"]: m for m in reg["models"]}
    assert by["v3-h7"]["status"] == "research_only" and by["v3-h7"]["audit_warning"] == 1 and by["v1-h7"]["status"] == "archived"
    r = adm.post("/api/v1/models/A-h7-test-candidate/decision", json={"status": "approved_candidate", "note": "looks good on PR-AUC"}, headers=W)
    assert r.status_code == 409 and "audit" in r.json()["error"]["message"]
    assert adm.post("/api/v1/models/A-h7-test-synthetic/decision", json={"status": "approved_candidate", "note": "trying anyway"}, headers=W).status_code == 409
    assert adm.post("/api/v1/models/A-h7-test-candidate/decision", json={"status": "rejected", "note": "audit unresolved"}, headers=W).json()["displayed_model_changed"] is False
    assert V.AUDIT["label_reconstruction_complete"] is False
    targets = {t["target"]: t for t in an.get("/api/v1/training/targets").json()}
    assert targets["A"]["ready"] and not targets["D"]["ready"] and targets["D"]["needs"]


def test_audit_warning_travels_with_the_product(app):
    b = app.get("/api/v1/bootstrap").json()
    assert b["audit"]["audit_date"] == "2026-11-05" and len(b["audit"]["findings"]) == 3
    assert "297 of the 598" in " ".join(b["audit"]["findings"])
    assert app.get("/api/v1/model-audit").json()["applies_to"] == ["v3"]
    assert "5 November 2026" in app.get("/api/v1/dossier/G0442").json()["model_audit"]["title"]


# ------------------------------------------------- field and interventions
def test_observation_fields_review_reasons_and_photos(roles):
    fo, sup = roles["field_officer"], roles["supervisor"]
    lat, lon = site_pos(sup, "G0442")
    bad = fo.post("/api/v1/observations", json=obs("G0442", lat=lat, lon=lon, tag="am", anopheles_larvae_count=99), headers=W)
    assert bad.status_code == 422
    assert fo.post("/api/v1/observations", json=obs("G0442", lat=lat, lon=lon, tag="sm", sampling_method="bucket"), headers=W).status_code == 422
    o = fo.post("/api/v1/observations", json=obs("G0442", lat=lat, lon=lon, tag="ok1", sampling_method="standard_dipper_350ml", anopheles_larvae_count=6), headers=W).json()
    assert sup.post(f"/api/v1/observations/{o['id']}/review", json={"decision": "returned"}, headers=W).status_code == 422  # reason required
    assert sup.post(f"/api/v1/observations/{o['id']}/review", json={"decision": "returned", "comment": "dips look wrong"}, headers=W).status_code == 200
    rec = next(x for x in fo.get("/api/v1/observations?status=returned").json() if x["id"] == o["id"])
    assert rec["review_comment"] == "dips look wrong" and rec["anopheles_per_dip"] == 0.6
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 64
    p = fo.post(f"/api/v1/observations/{o['id']}/photos", files={"file": ("a.png", png, "image/png")}, headers=W)
    assert p.status_code == 201
    assert fo.post(f"/api/v1/observations/{o['id']}/photos", files={"file": ("a.png", b"not an image", "image/png")}, headers=W).status_code == 422
    assert fo.post(f"/api/v1/observations/{o['id']}/photos", files={"file": ("a.txt", b"x", "text/plain")}, headers=W).status_code == 422
    assert fo.get(f"/api/v1/photos/{p.json()['id']}").content == png
    assert roles["viewer"].get(f"/api/v1/photos/{p.json()['id']}").status_code == 403


def test_intervention_needs_every_step(roles):
    sup, fo = roles["supervisor"], roles["field_officer"]
    auth = lambda **kw: sup.post("/api/v1/interventions", json={"method": "bti_ground", "protocol_ref": "SOP-9", **kw}, headers=W)  # noqa: E731
    water_only = verified_obs(roles, "G0100", "wo", larvae_present="not_sampled", dips=None, larvae_count=None, anopheles_present="not_identified", species=[], stages=[])
    assert auth(observation_id=water_only, habitat_suitability="suitable_for_ground").status_code == 409  # water alone is not entomological evidence
    oid = verified_obs(roles, "G0101", "pos")
    assert auth(observation_id=oid).status_code == 422                                            # suitability assessment required
    assert auth(observation_id=oid, habitat_suitability="not_suitable").status_code == 409
    assert auth(observation_id=oid, habitat_suitability="suitable_for_ground", method="bti_drone").status_code == 409
    assert sup.post("/api/v1/interventions", json={"observation_id": oid, "method": "bti_ground", "habitat_suitability": "suitable_for_ground"}, headers=W).status_code == 422  # protocol reference
    assert fo.post("/api/v1/interventions", json={"observation_id": oid, "method": "bti_ground", "protocol_ref": "SOP-9", "habitat_suitability": "suitable_for_ground"}, headers=W).status_code == 403
    ok = auth(observation_id=oid, habitat_suitability="suitable_for_ground")
    assert ok.status_code == 201 and ok.json()["protocol_status"] == "awaiting_approval"
    assert auth(observation_id=oid, habitat_suitability="suitable_for_ground").status_code == 409  # one open decision per finding
    d = sup.get("/api/v1/dossier/G0101").json()
    assert d["status"] == "authorised" and d["evidence_tier"] == "treatment_authorised" and d["protocol"]["label"] == "Awaiting approval"
    kinds = [e["kind"] for e in d["timeline"]]
    assert "entomologically_confirmed" in kinds and "treatment_authorised" in kinds and "field_verified" in kinds
    # a decision not to treat is recorded with its reason and counted nowhere as an authorisation
    o2 = verified_obs(roles, "G0102", "rej")
    assert sup.post("/api/v1/interventions/reject", json={"observation_id": o2, "reason": "no"}, headers=W).status_code == 422
    assert sup.post("/api/v1/interventions/reject", json={"observation_id": o2, "reason": "habitat drying out, revisit next week"}, headers=W).status_code == 201
    d2 = sup.get("/api/v1/dossier/G0102").json()
    assert d2["status"] == "rejected" and any(e["kind"] == "treatment_rejected" for e in d2["timeline"])
    trail = " ".join(a["action"] for a in sup.get("/api/v1/admin/audit?limit=200").json())
    assert "intervention.reject" in trail and "intervention.authorize" in trail and "observation.review" in trail


def test_statuses_follow_the_workflow(roles):
    sup, fo = roles["supervisor"], roles["field_officer"]
    labels = sup.get("/api/v1/bootstrap").json()["status_labels"]
    assert list(labels.values()) == ["Not assessed", "Inspection required", "Evidence insufficient", "Eligible for review", "Authorised", "Rejected", "Completed", "Follow-up due", "Closed"]
    assert sup.get("/api/v1/dossier/G0310").json()["status"] == "not_assessed"
    fo_id = fo.get("/api/v1/auth/me").json()["id"]
    sup.post("/api/v1/tasks", json={"site_id": "G0310", "due_date": "2026-12-31", "assignee_id": fo_id}, headers=W)
    assert sup.get("/api/v1/dossier/G0310").json()["status"] == "inspection_required"
    oid = verified_obs(roles, "G0310", "st1")
    assert sup.get("/api/v1/dossier/G0310").json()["status"] == "eligible_for_review"
    iid = sup.post("/api/v1/interventions", json={"observation_id": oid, "method": "bti_ground", "protocol_ref": "SOP-9", "habitat_suitability": "suitable_for_ground"}, headers=W).json()["id"]
    fo.post(f"/api/v1/interventions/{iid}/performed", json={"performed_at": iso(3), "quantity_used": "0.2 kg"}, headers=W)
    assert sup.get("/api/v1/dossier/G0310").json()["status"] == "follow_up_due"
    assert any(a["key"] == "followup" for a in sup.get("/api/v1/overview").json()["actions"])
    ns = verified_obs(roles, "G0310", "fu-ns", observed_at=iso(0), larvae_present="not_sampled", dips=None, larvae_count=None, anopheles_present="not_identified", species=[], stages=[])
    assert sup.post(f"/api/v1/interventions/{iid}/followup", json={"observation_id": ns, "outcome": "larvae_absent"}, headers=W).status_code == 422
    fu = verified_obs(roles, "G0310", "fu", observed_at=iso(0), larvae_present="no", larvae_count=0, anopheles_present="no", species=[], stages=[])
    assert sup.post(f"/api/v1/interventions/{iid}/followup", json={"observation_id": fu, "outcome": "larvae_absent"}, headers=W).status_code == 200
    assert sup.get("/api/v1/dossier/G0310").json()["status"] == "closed"


# -------------------------------------------------------------------- drone
def test_forecast_flights_are_inspection_only_and_treatment_needs_authorisation(roles):
    an, sup = roles["analyst"], roles["supervisor"]
    plan = an.post("/api/v1/missions/plan", json={"h": 7, "date": "2025-11-26"}, headers=W).json()
    assert plan["kind"] == "inspection" and plan["totals"]["treat"] == 0 and plan["totals"]["spray_l"] == 0
    assert all(w["action"] == "VERIFY_ONLY" for s in plan["sorties"] for w in s["waypoints"])
    assert an.post("/api/v1/missions/plan", json={"kind": "treatment", "site_ids": ["G0442"]}, headers=W).status_code == 409  # a forecast site cannot be selected
    oid = verified_obs(roles, "G0200", "dr")
    iid = sup.post("/api/v1/interventions", json={"observation_id": oid, "method": "bti_drone", "protocol_ref": "SOP-D1", "habitat_suitability": "suitable_for_drone"}, headers=W).json()["id"]
    tm = an.post("/api/v1/missions/plan", json={"kind": "treatment", "site_ids": ["G0200"], "save": True}, headers=W)
    assert tm.status_code == 200, tm.text
    m = tm.json()
    mid = m["id"]
    assert m["kind"] == "treatment" and [w["site_id"] for s in m["sorties"] for w in s["waypoints"]] == ["G0200"] and m["estimates_note"]
    assert next(x for x in an.get("/api/v1/missions").json() if x["id"] == mid)["kind"] == "treatment"
    assert an.post(f"/api/v1/missions/{mid}/result", json={"flown_on": "2026-11-09", "sorties_flown": 1, "targets_reached": 1}, headers=W).status_code == 409  # not flown yet
    assert sup.post(f"/api/v1/missions/{mid}/status", json={"status": "APPROVED"}, headers=W).status_code == 200
    wpl = an.get(f"/api/v1/missions/{mid}/export?fmt=wpl").text
    assert wpl.startswith("QGC WPL 110")
    assert an.get(f"/api/v1/missions/{mid}/export?fmt=csv").text.startswith("sortie,")
    assert an.get(f"/api/v1/missions/{mid}/export?fmt=geojson").json()["type"] == "FeatureCollection"
    an.post(f"/api/v1/missions/{mid}/status", json={"status": "IN_FLIGHT"}, headers=W)
    assert an.post(f"/api/v1/missions/{mid}/result", json={"flown_on": "2026-11-09", "sorties_flown": 1, "targets_reached": 1, "treated_sites": ["G0999"]}, headers=W).status_code == 422
    ok = an.post(f"/api/v1/missions/{mid}/result", json={"flown_on": "2026-11-09", "sorties_flown": 1, "targets_reached": 1, "treated_sites": ["G0200"], "product_used": "Bti WDG 0.1 kg"}, headers=W)
    assert ok.status_code == 200
    assert next(x for x in an.get("/api/v1/missions").json() if x["id"] == mid)["flight_result"]["demo"] is True
    # an inspection flight can never record treatment; rejection needs a reason
    im = an.post("/api/v1/missions/plan", json={"h": 7, "date": "2025-11-26", "save": True}, headers=W).json()["id"]
    assert sup.post(f"/api/v1/missions/{im}/status", json={"status": "CANCELLED", "reject": True}, headers=W).status_code == 422
    assert an.post(f"/api/v1/missions/{im}/status", json={"status": "CANCELLED", "reject": True, "note": "x"}, headers=W).status_code == 409
    im2 = an.post("/api/v1/missions/plan", json={"h": 7, "date": "2025-10-15", "save": True}, headers=W).json()["id"]
    sup.post(f"/api/v1/missions/{im2}/status", json={"status": "APPROVED"}, headers=W)
    an.post(f"/api/v1/missions/{im2}/status", json={"status": "IN_FLIGHT"}, headers=W)
    first = next(w["site_id"] for s in an.get(f"/api/v1/missions/{im2}").json()["result"]["sorties"] for w in s["waypoints"])
    assert an.post(f"/api/v1/missions/{im2}/result", json={"flown_on": "2026-11-09", "sorties_flown": 1, "targets_reached": 3, "treated_sites": [first]}, headers=W).status_code == 409
    assert sup.post(f"/api/v1/missions/{im}/status", json={"status": "CANCELLED", "reject": True, "note": "wind too strong this week"}, headers=W).status_code == 200
    assert "mission.result" in " ".join(a["action"] for a in sup.get("/api/v1/admin/audit?limit=100").json())
    assert iid


# ---------------------------------------------------------------- reporting
def test_reports_four_groups_and_exports(roles, pm):
    sup = roles["supervisor"]
    r = sup.get("/api/v1/reports/indicators").json()
    assert [g["key"] for g in r["groups"]] == ["operational", "environmental", "entomological", "programme"]
    assert "not a measured reduction in malaria" in r["claim"]
    ind = {i["id"]: i for g in r["groups"] for i in g["indicators"]}
    assert ind["auth"]["value"] >= 1 and ind["lp"]["denominator"] >= ind["lp"]["numerator"]
    assert ind["hours"]["value"] is None  # no FIELD flight logs: synthetic logs never feed programme indicators
    csv = sup.get("/api/v1/reports/indicators.csv")
    assert csv.status_code == 200 and "No data available" in csv.text and csv.text.splitlines()[0].startswith("group,indicator,value")
    pdf = pm.get("/api/v1/reports/indicators.pdf")
    assert pdf.status_code == 200 and pdf.content[:5] == b"%PDF-" and pdf.content.rstrip().endswith(b"%%EOF")
    assert roles["analyst"].get("/api/v1/reports/indicators.csv").status_code == 403
    empty = sup.get("/api/v1/reports/indicators?start=2031-01-01").json()
    e = {i["id"]: i for g in empty["groups"] for i in g["indicators"]}
    assert e["hab_insp"]["value"] is None and e["lp"]["value"] is None and e["sw_change"]["value"] is None


# ------------------------------------------------------------ console shell
def test_console_files_and_offline_shell(app):
    sw = app.get("/sw.js").text
    for v in ("overview", "map", "dossier", "drone", "reports", "models", "issue", "field"):
        assert f"'{v}'" in sw
        js = app.get(f"/js/views/{v}.js")
        assert js.status_code == 200 and "export async function render" in js.text
    html = app.get("/").text
    assert 'data-theme="light"' in html and "Public+Sans" in html and "AI" not in html.replace("RINDA", "")
    assert "tile.openstreetmap.org" in app.get("/").headers["content-security-policy"]
    off = app.get("/js/offline.js").text
    assert "return ok" in off  # a refused save is reported, never swallowed
    assert app.get("/api/v1/overview", headers={"accept-encoding": "gzip"}).headers.get("content-encoding") == "gzip"


# ------------------------------------------------- research and efficiency
def test_research_alignment_cites_only_listed_references(app):
    a = app.get("/api/v1/research/alignment").json()
    assert len(a["gaps"]) == 10 and len(a["objectives"]) == 3 and a["challenges"]
    for g in a["gaps"]:
        for k in ("accomplished", "limitations", "gap", "rinda", "objective", "evaluation", "status"):
            assert g[k], (g["id"], k)
        assert all(r in a["references"] for r in g["refs"])
    assert all(r["doi"].startswith("10.") for r in a["references"].values())
    assert {o["id"] for o in a["objectives"]} == {"Objective 1", "Objective 2", "Objective 3"}


def test_earth_inventory_says_what_is_held(app):
    inv = app.get("/api/v1/earth/inventory").json()
    by = {c["key"]: c for c in inv["collections"]}
    assert {"sentinel-1-rtc", "sentinel-1-grd", "sentinel-2-l2a", "landsat-c2-l2", "copernicus-dem-glo30", "jrc-gsw-v1.3"} <= set(by)
    assert by["landsat-c2-l2"]["scenes"] > 500 and by["landsat-c2-l2"]["resolution_m"] == 30 and "metadata only" in by["landsat-c2-l2"]["held"].lower()
    assert by["sentinel-2-l2a"]["median_cloud_pct"] is not None and by["sentinel-1-rtc"]["median_cloud_pct"] is None
    assert min(by["sentinel-1-rtc"]["by_year"]) == "2020" and by["sentinel-1-grd"]["first"] >= "2020-01-01"
    assert by["copernicus-dem-glo30"]["first"] is None and by["copernicus-dem-glo30"]["scenes"] == 1
    assert sum(by["sentinel-1-rtc"]["by_year"].values()) == by["sentinel-1-rtc"]["scenes"]
    e = app.get("/api/v1/map/earth?date=2025-12-01").json()
    assert all("rtc" in p["storage_ref"] or p["platform"].startswith("SENTINEL-2") for p in e["products"])


def test_efficiency_is_measured_and_edge_is_not_claimed(roles):
    r = roles["analyst"].get("/api/v1/models/efficiency").json()
    assert [m["horizon"] for m in r["served"]] == [7, 14]
    for m in r["served"]:
        assert m["issue_latency_ms"] > 0 and m["rows_timed"] > 400 and m["model_bytes"] > 1_000_000 and "error" not in m
    assert r["edge"]["status"] == "Not measured" and "Jetson" in r["edge"]["target"] and len(r["edge"]["required"]) >= 4
    assert "not the drone" in r["scope"]
    assert roles["viewer"].get("/api/v1/models/efficiency").status_code == 403
    import training as T
    m = T.train(fixture_table(), "A", 7, {"n_estimators": 30}, seed=1)["metrics"]["efficiency"]
    assert m["ms_per_1000_rows"] > 0 and "Not an edge-device measurement" in m["note"]


def test_drone_operator_flies_but_does_not_decide(app):
    op = client_as("drone_operator")
    me = op.get("/api/v1/auth/me").json()
    assert me["role_label"] == "Drone operator" and "plan:mission" in me["permissions"]
    assert op.get("/api/v1/interventions").status_code == 200
    mid = op.post("/api/v1/missions/plan", json={"h": 7, "date": "2025-09-17", "save": True}, headers=W).json()["id"]
    assert op.post(f"/api/v1/missions/{mid}/status", json={"status": "APPROVED"}, headers=W).status_code == 403
    assert op.post("/api/v1/interventions", json={"observation_id": 1, "method": "bti_drone", "protocol_ref": "SOP-1", "habitat_suitability": "suitable_for_drone"}, headers=W).status_code == 403
    assert op.post("/api/v1/observations/1/review", json={"decision": "verified"}, headers=W).status_code == 403
    assert op.get("/api/v1/datasets").status_code == 403 and op.get("/api/v1/admin/users").status_code == 403


def test_model_maturity_is_never_overstated(roles):
    reg = roles["analyst"].get("/api/v1/models").json()
    assert {m["maturity"] for m in reg["models"]} <= {"Experimental", "Archived", "Rejected", "Test fixture, not a model", "Suitable for controlled operational evaluation"}
    assert next(m for m in reg["models"] if m["id"] == "v3-h7")["maturity"] == "Experimental"
    assert [x["level"] for x in reg["maturity_levels"]] == ["Experimental", "Suitable for controlled operational evaluation", "Validated"]



def test_outlook_never_recommends_spraying_from_a_forecast(app):
    o = app.get("/api/v1/outlook").json()
    assert o["today"] and [h["horizon"] for h in o["horizons"]] == [7, 14]
    for h in o["horizons"]:
        assert h["issue_date"] <= o["today"] and h["target_date"] > h["issue_date"]
        assert h["potential_signal"] + h["evidence_insufficient"] + h["lower"] == h["locations"]
        assert "breeding" not in h["text"].lower()
    by = {g["action"]: g for g in o["guidance"]}
    assert set(by) == {"Inspect", "Decide", "Treat", "Follow up"}
    treat = by["Treat"]["text"]
    assert "not grounds for spraying" in treat or "authorised treatment" in treat
    assert "not mosquito breeding" in o["caution"]
    site = app.get("/api/v1/forecast?h=7").json()["rows"][0]["site_id"]
    e = app.get(f"/api/v1/explain?site={site}&date={o['horizons'][0]['issue_date']}&h=7").json()
    assert e["available"] and (e["label_observed"] is None or e["label_observed"] in (0, 1))


__all__ = ["io"]

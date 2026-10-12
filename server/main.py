"""RINDA Vector Habitat Intelligence: API and console server.

    uvicorn main:app --port 8740            (from rinda-platform/server)
"""
from __future__ import annotations

import hmac
import io
import json
import math
import os
from contextlib import asynccontextmanager
from dataclasses import asdict, fields
from pathlib import Path

import numpy as np
import pandas as pd
from fastapi import Body, FastAPI, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles

import ops
import planner as P
from bulletin import render_bulletin
from data import CLAIM, STATION, haversine, records, store
from engine import CFG, ENGINE, ENGINE_VERSION, ROOT, RUN_SUMMARY, VAR

WEB = ROOT / "web"
STATION_KEYS_FILE = VAR / "station_keys.json"


@asynccontextmanager
async def lifespan(_app):
    store()
    ENGINE.load()
    ops.audit("system", "engine.ready", f"engine {ENGINE_VERSION}")
    yield


app = FastAPI(title="RINDA Vector Habitat Intelligence API", version=ENGINE_VERSION,
              lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json")


@app.middleware("http")
async def revalidate_static(request, call_next):
    resp = await call_next(request)
    if request.url.path.startswith(("/js/", "/css/")):
        resp.headers["Cache-Control"] = "no-cache"
    return resp


def _f(x):
    return None if x is None or (isinstance(x, float) and not math.isfinite(x)) else x


def _area() -> dict:
    s = store().sites
    hull = P.hull_area_ha(s.latitude.to_numpy(), s.longitude.to_numpy())
    wet = s[s.water_freq > 0]
    zone = len(wet) * math.pi * 50 ** 2 / 10_000
    return {"sector_hull_ha": hull, "wet_prone_zone_ha": zone, "n_wet_prone_sites": int(len(wet))}


def _sources() -> list[dict]:
    meta = {
        "meteo": ("Meteorology", "Automatic weather station, daily", "Gikomero AWS"),
        "sentinel1": ("Sentinel-1 SAR", "C-band VV/VH backscatter, descending pass", "Copernicus via Earth Engine"),
        "sentinel2": ("Sentinel-2 optical", "NDVI, NDWI, MNDWI, EVI, NDMI", "Copernicus via Earth Engine"),
        "terrain": ("Terrain", "Elevation, slope, TWI, flow accumulation", "SRTM / Copernicus DEM"),
        "entomology": ("Larval surveys", "Dipping records with Anopheles confirmation", "RBC entomology teams"),
        "nwp_forecast": ("NWP rainfall forecast", "Lead 1-14 days", "Meteo Rwanda"),
    }
    out = []
    for k, (name, desc, origin) in meta.items():
        present = bool(CFG["sources"][k]["present"])
        unlocks = {"sentinel2": "Target B: potentially suitable habitat",
                   "terrain": "Terrain features; ablation arms G and H",
                   "entomology": "Target C: confirmed Anopheles habitat",
                   "nwp_forecast": "Operational forecast mode (replaces hindcast)"}.get(k)
        out.append({"key": k, "name": name, "description": desc, "origin": origin,
                    "present": present, "unlocks": unlocks})
    return out


# ------------------------------------------------------------------- bootstrap
@app.get("/api/bootstrap")
def bootstrap():
    st = store()
    return {
        "product": {"name": "RINDA", "edition": "Vector Habitat Intelligence",
                    "version": ENGINE_VERSION},
        "tenant": {"name": "Gikomero Sector pilot", "district": "Gasabo", "province": "Kigali City"},
        "mode": RUN_SUMMARY["provenance"]["mode"], "claim": CLAIM,
        "horizons": {h: {"dates": st.dates(h), "threshold": st.thr[h]} for h in (7, 14)},
        "sites": records(st.sites), "station": STATION, "area": _area(),
        "sources": _sources(), "model": ENGINE.card(),
        "planner_defaults": P.defaults(), "econ_defaults": ops.DEFAULT_ECON,
        "risk_breaks": CFG["mapping"]["risk_classes"],
        "s1_last": st.s1_dates[-1], "meteo_last": str(st.meteo.date.max().date()),
    }


@app.get("/api/health")
def health():
    return {"status": ENGINE.status, "engine": ENGINE_VERSION, "log": ENGINE.log[-10:]}


# -------------------------------------------------------------------- forecast
@app.get("/api/forecast")
def forecast(h: int = 7, date: str | None = None):
    st = store()
    d, f = st.forecast(h, date)
    cols = ["site_id", "latitude", "longitude", "probability", "risk_class", "uncertainty_std",
            "uncertainty_band", "conformal_decision", "priority_score", "priority_rank",
            "WATER_PERSISTENCE_PASSES", "y", "alert", "stratum", "water_freq"]
    f2 = f[cols].copy()
    f2["priority_rank"] = np.arange(1, len(f2) + 1)  # rank within this issue, not the archive
    f2["target_date"] = (pd.Timestamp(d) + pd.Timedelta(days=h)).strftime("%Y-%m-%d")
    summ = {
        "issue_date": d, "target_date": f2["target_date"].iloc[0] if len(f2) else None,
        "n_sites": int(len(f2)), "alerts": int(f2.alert.sum()),
        "risk": f2.risk_class.value_counts().to_dict(),
        "decisions": f2.conformal_decision.value_counts().to_dict(),
        "mean_probability": float(f2.probability.mean()),
        "threshold": st.thr[h],
    }
    if f2.y.notna().any():
        y = f2.y.fillna(0).astype(int)
        summ["outcome"] = {"observed": int(y.sum()), "hits": int((f2.alert & (y == 1)).sum()),
                           "false_alarms": int((f2.alert & (y == 0)).sum()),
                           "misses": int((~f2.alert & (y == 1)).sum())}
    return {"summary": summ, "rows": records(f2), "claim": CLAIM}


@app.get("/api/verification")
def verification(h: int = 7):
    return records(store().verification(h))


@app.get("/api/site/{site_id}")
def site(site_id: str):
    s = store().site(site_id)
    if not s:
        raise HTTPException(404, "unknown site")
    return s


@app.get("/api/explain")
def explain(site: str, date: str, h: int = 7):
    return ENGINE.explain(h, site, date)


@app.get("/api/scenario")
def scenario(site: str, date: str, h: int = 7, rain: float = 1.0, temp: float = 0.0):
    return ENGINE.scenario(h, site, date, max(0.0, min(rain, 3.0)), max(-4.0, min(temp, 4.0)))


@app.post("/api/score")
async def score(h: int = Query(7), file: UploadFile = File(...)):
    raw = await file.read()
    X = pd.read_csv(io.BytesIO(raw))
    if len(X) > 200_000:
        raise HTTPException(413, "batch limited to 200 000 rows")
    out = ENGINE.score_batch(h, X)
    ops.audit("analyst", "model.batch_score", f"h={h} rows={len(X)} file={file.filename}")
    buf = out.to_csv(index=False)
    return Response(buf, media_type="text/csv", headers={
        "Content-Disposition": f'attachment; filename="scored_h{h}_{file.filename}"',
        "X-Missing-Features": str(len(out.attrs.get("missing_features", [])))})


@app.get("/api/score/template")
def score_template(h: int = 7):
    m = ENGINE.models[h]
    tbl = ENGINE.tables[h]
    ex = tbl.tail(3)[["site_id", "date"] + m.fcols]
    return PlainTextResponse(ex.to_csv(index=False), media_type="text/csv", headers={
        "Content-Disposition": f'attachment; filename="rinda_feature_template_h{h}.csv"'})


# ---------------------------------------------------------------------- meteo
@app.get("/api/meteo")
def meteo(start: str | None = None, end: str | None = None):
    return store().meteo_series(start, end)


@app.get("/api/meteo/summary")
def meteo_summary():
    return store().meteo_summary()


# ------------------------------------------------------------------ assurance
@app.get("/api/assurance")
def assurance():
    a = dict(store().assurance())
    a["engine"] = ENGINE.card()
    return a


@app.get("/api/evidence")
def evidence():
    ev = store().evidence()
    tc = ev.get("target_categories", {})
    steps = [
        ("A", "Environmental observation", "Meteorology and Sentinel-1 ingested daily; field IoT ingest endpoint live", "operational", ["Field Network", "Data & Governance"]),
        ("B", "Habitat mapping", "SAR water classification for 600 monitored sites", "operational", ["Habitat Forecast"]),
        ("C", "Habitat suitability modelling", "Target B rules ready; waits on Sentinel-2 and terrain exports", "blocked", ["Data & Governance"]),
        ("D", "Larval habitat prediction", "Target C ready; waits on RBC larval survey records", "blocked", ["Data & Governance"]),
        ("E", "Productive habitat prediction", "Needs larval density data from RBC dipping protocol", "blocked", []),
        ("F", "Adult vector prediction", "Outside thesis scope (MAPLE partner)", "out_of_scope", []),
        ("G", "Transmission-related prediction", "Outside thesis scope (MAPLE partner)", "out_of_scope", []),
        ("H", "Prospective forecasting", "7- and 14-day formation forecast with temporal holdout; hindcast until NWP feed connected", "operational", ["Habitat Forecast", "Model Assurance"]),
        ("I", "Target verification", "Onboard verification and confidence gate; flight-log analytics ready", "awaiting_field", ["Flight Operations"]),
        ("J", "Targeted intervention", "Forecast-to-sortie planner with flight-controller export", "operational", ["Mission Planning"]),
        ("K", "Intervention outcome", "Cost and coverage comparator; entomological outcome slots wait on trial", "awaiting_field", ["Impact & Economics"]),
    ]
    chain = []
    for code, name, deliv, status, mods in steps:
        key = next((k for k in tc if k.startswith(code + " ")), None)
        chain.append({"code": code, "name": name, "studies": tc.get(key) if key else None,
                      "platform": deliv, "status": status, "modules": mods})
    objectives = [
        {"id": "O1", "paper": "Paper 1", "title": "Forecast habitat formation 7-14 days ahead from EO, meteorological and IoT data",
         "status": "operational", "evidence": "Holdout PR-AUC 0.581 at 7 d vs base rate 0.037; 0.310 at 14 d vs 0.023",
         "modules": ["Habitat Forecast", "Scenario Lab", "Model Assurance"],
         "open": "Generalisation across agro-ecological zones needs a second station and site set"},
        {"id": "O2", "paper": "Papers 2 and 3", "title": "Convert risk maps into flight plans, confirm targets onboard, measure latency, energy and compute",
         "status": "awaiting_field", "evidence": "Planner, timing model (L <= d/v), energy model and log analytics built; measured values arrive with flight logs",
         "modules": ["Mission Planning", "Flight Operations"],
         "open": "Instrumented flights with Ampere Vision Rwanda airframe"},
        {"id": "O3", "paper": "Side paper with RBC / MAPLE", "title": "Effectiveness and cost-effectiveness against manual LSM; national integration framework",
         "status": "awaiting_field", "evidence": "Comparator and scale-up planner built on editable programme inputs",
         "modules": ["Impact & Economics", "Bulletins"],
         "open": "Matched-plot entomological outcomes and programme cost records"},
    ]
    rqs = [
        ("RQ1", "Onboard habitat recognition accuracy", "Flight Operations", "awaiting_field"),
        ("RQ2", "Accuracy vs latency/memory/energy under compression", "Flight Operations", "awaiting_field"),
        ("RQ3", "Loop meets L <= d/v in flight; deadline-miss ratio", "Flight Operations", "awaiting_field"),
        ("RQ4", "Spray-targeting accuracy of confidence-gated actuation", "Flight Operations", "awaiting_field"),
        ("RQ5", "Generalisation across seasons and sites", "Model Assurance", "partial"),
        ("RQ6", "Energy per treated hectare vs blanket application", "Mission Planning", "partial"),
        ("RQ-C", "Flight time saved by forecast routing vs lawnmower", "Mission Planning", "partial"),
        ("RQ-D", "Field IoT link reliability and forecast sensitivity", "Field Network", "awaiting_field"),
    ]
    return {"review": {"n": ev.get("n"), "target_categories": tc, "strength": ev.get("strength", {}),
                       "intervention_linkage_field_trap": ev.get("intervention_linkage_field_trap"),
                       "H_with_temporal_separation": ev.get("H_with_temporal_separation")},
            "chain": chain, "objectives": objectives,
            "rqs": [{"id": a, "question": b, "module": c, "status": d} for a, b, c, d in rqs]}


@app.get("/api/governance")
def governance():
    a = store().audit()
    return {"sources": _sources(), "provenance": RUN_SUMMARY["provenance"],
            "water_thresholds": RUN_SUMMARY["water_thresholds"], "audit": a,
            "trail": ops.audit_trail(), "engine": ENGINE.card(),
            "leakage": {h: r["manifest"]["table"].get("leakage_checks") for h, r in
                        ((r["horizon"], r) for r in RUN_SUMMARY["results"])}}


# ------------------------------------------------------------------- planning
def _aircraft(d: dict | None) -> P.Aircraft:
    names = {f.name for f in fields(P.Aircraft)}
    return P.Aircraft(**{k: v for k, v in (d or {}).items() if k in names})


def _treatment(d: dict | None) -> P.Treatment:
    names = {f.name for f in fields(P.Treatment)}
    return P.Treatment(**{k: v for k, v in (d or {}).items() if k in names})


def _select_targets(f: pd.DataFrame, thr: float, pol: dict) -> pd.DataFrame:
    treat = f[(f.conformal_decision == "formation") | (f.probability >= thr)].assign(action="VERIFY_AND_TREAT")
    parts = [treat]
    if pol.get("include_uncertain", True):
        unc = f[(f.conformal_decision == "uncertain") & ~f.site_id.isin(treat.site_id)]
        unc = unc[unc.probability >= pol.get("min_uncertain_probability", 0.05)]
        parts.append(unc.assign(action="VERIFY_ONLY"))
    sel = pd.concat(parts)
    sel["_rank"] = (sel.action == "VERIFY_AND_TREAT").astype(int)
    sel = sel.sort_values(["_rank", "priority_score"], ascending=False).drop(columns="_rank")
    if pol.get("exclude"):
        sel = sel[~sel.site_id.isin(pol["exclude"])]
    return sel.head(int(pol.get("max_targets", 40)))


@app.post("/api/missions/plan")
def plan_mission(body: dict = Body(...)):
    st = store()
    h = int(body.get("h", 7))
    d, f = st.forecast(h, body.get("date"))
    a, t = _aircraft(body.get("aircraft")), _treatment(body.get("treatment"))
    base = tuple(body.get("base") or (STATION["lat"], STATION["lon"]))
    tg = _select_targets(f, st.thr[h], body.get("policy", {}))
    lc = body.get("launch") or {}
    res = P.plan(tg, a, t, base, launch=lc.get("mode", "mobile"),
                 zone_radius_m=float(lc.get("zone_radius_m", 900)),
                 road_factor=float(lc.get("road_factor", 1.4)), vehicle_kmh=float(lc.get("vehicle_kmh", 25)))
    area = _area()
    res["baseline"] = {"wet_prone_zone": P.lawnmower(area["wet_prone_zone_ha"], a, t),
                       "sector": P.lawnmower(area["sector_hull_ha"], a, t)}
    res["timing"] = P.timing_budget(a)
    res["energy"] = P.energy_per_ha(a)
    res["issue_date"], res["horizon"] = d, h
    res["window"] = {"issue": d, "formation_expected_by": (pd.Timestamp(d) + pd.Timedelta(days=h)).strftime("%Y-%m-%d")}
    if body.get("save"):
        mid = f"M-{d.replace('-', '')}-H{h:02d}-{ops.now()[11:19].replace(':', '')}"
        res["id"] = mid
        ops.save_mission(mid, d, h, body.get("author", "operator"),
                         {"aircraft": asdict(a), "treatment": asdict(t), "policy": body.get("policy", {}), "base": base,
                          "launch": lc},
                         res)
        ops.audit(body.get("author", "operator"), "mission.create",
                  f"{mid}: {res['totals'].get('sorties', 0)} sorties, {res['totals'].get('targets', 0)} targets")
    return res


@app.get("/api/missions")
def missions():
    return ops.list_missions()


@app.get("/api/missions/{mid}")
def mission(mid: str):
    m = ops.get_mission(mid)
    if not m:
        raise HTTPException(404, "unknown mission")
    return m


@app.post("/api/missions/{mid}/status")
def mission_status(mid: str, body: dict = Body(...)):
    s = body.get("status", "")
    if s not in {"PLANNED", "APPROVED", "IN_FLIGHT", "COMPLETED", "CANCELLED"}:
        raise HTTPException(400, "invalid status")
    ops.set_status(mid, s)
    ops.audit(body.get("actor", "operator"), "mission.status", f"{mid} -> {s}")
    return {"ok": True}


@app.get("/api/missions/{mid}/export")
def mission_export(mid: str, fmt: str = "plan", sortie: str | None = None):
    m = ops.get_mission(mid)
    if not m:
        raise HTTPException(404, "unknown mission")
    res, a = m["result"], _aircraft(m["params"]["aircraft"])
    ops.audit("operator", "mission.export", f"{mid} {fmt} {sortie or 'all'}")
    if fmt == "geojson":
        return Response(json.dumps(P.to_geojson(res), indent=1), media_type="application/geo+json",
                        headers={"Content-Disposition": f'attachment; filename="{mid}.geojson"'})
    if fmt == "csv":
        rows = [{"sortie": s["id"], **w} for s in res["sorties"] for w in s["waypoints"]]
        return Response(pd.DataFrame(rows).to_csv(index=False), media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="{mid}_targets.csv"'})
    s = next((x for x in res["sorties"] if x["id"] == sortie), res["sorties"][0] if res["sorties"] else None)
    if s is None:
        raise HTTPException(400, "mission has no sorties")
    payload = {"base": res["base"], "sortie": s}
    if fmt == "wpl":
        return Response(P.to_wpl(payload, a), media_type="text/plain",
                        headers={"Content-Disposition": f'attachment; filename="{mid}_{s["id"]}.waypoints"'})
    return Response(json.dumps(P.to_qgc_plan(payload, a), indent=2), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{mid}_{s["id"]}.plan"'})


@app.post("/api/timing")
def timing(body: dict = Body(default={})):
    a = _aircraft(body)
    curve = []
    for v in np.round(np.arange(1.0, 10.01, 0.5), 2):
        b = P.timing_budget(P.Aircraft(**(asdict(a) | {"work_speed_ms": float(v)})))
        curve.append({"speed": float(v), "deadline_ms": b["deadline_ms"], "latency_ms": b["latency_ms"]})
    return {"budget": P.timing_budget(a), "energy": P.energy_per_ha(a), "curve": curve}


# ----------------------------------------------------------------- flight logs
@app.get("/api/flightlogs/template")
def flightlog_template():
    return PlainTextResponse(ops.flight_log_template(), media_type="text/csv", headers={
        "Content-Disposition": 'attachment; filename="rinda_flight_log_template.csv"'})


@app.get("/api/flightlogs/schema")
def flightlog_schema():
    return [{"column": c, "description": d, "example": e} for c, d, e in ops.FLIGHT_LOG_COLUMNS]


@app.post("/api/flightlogs")
async def flightlog_upload(file: UploadFile = File(...), provenance: str = Form("FIELD")):
    raw = await file.read()
    if provenance not in {"FIELD", "BENCH", "SYNTHETIC"}:
        raise HTTPException(400, "provenance must be FIELD, BENCH or SYNTHETIC")
    r = ops.ingest_flight_log(raw, file.filename or "upload.csv", provenance)
    if not r.get("ok"):
        raise HTTPException(400, r.get("error"))
    ops.audit("operator", "flightlog.ingest", f"{file.filename} {provenance} rows={r['rows']}")
    return r


@app.post("/api/missions/{mid}/synthetic-log")
def flightlog_synthetic(mid: str):
    m = ops.get_mission(mid)
    if not m:
        raise HTTPException(404, "unknown mission")
    raw = ops.synthetic_flight_log(m, m["params"]["aircraft"])
    r = ops.ingest_flight_log(raw, f"{mid}_SYNTHETIC.csv", "SYNTHETIC")
    ops.audit("operator", "flightlog.synthetic", f"{mid} rows={r.get('rows')}")
    return r


@app.get("/api/flightlogs")
def flightlog_list():
    return ops.flight_logs()


@app.delete("/api/flightlogs/{log_id}")
def flightlog_delete(log_id: int):
    ops.delete_flight_log(log_id)
    ops.audit("operator", "flightlog.delete", str(log_id))
    return {"ok": True}


@app.get("/api/flightlogs/analysis")
def flightlog_analysis(id: int | None = None):
    return ops.analyse_flight_logs(id)


# ------------------------------------------------------------------ telemetry
def _station_keys() -> dict:
    """Per-node keys from RINDA_STATION_KEYS (JSON) or var/station_keys.json.
    Placeholder keys are registered for display but can never authenticate."""
    env = os.environ.get("RINDA_STATION_KEYS")
    if env:
        return json.loads(env)
    if not STATION_KEYS_FILE.exists():
        STATION_KEYS_FILE.write_text(json.dumps({"ESP32-GKM-01": "replace-me-01",
                                                 "ESP32-GKM-02": "replace-me-02",
                                                 "ESP32-GKM-03": "replace-me-03"}, indent=2))
    return json.loads(STATION_KEYS_FILE.read_text())


@app.post("/api/telemetry/{station_id}")
def telemetry_ingest(station_id: str, body: dict = Body(...), x_station_key: str = Header(None)):
    keys = _station_keys()
    key = keys.get(station_id)
    if not key or key.startswith("replace-me") or not x_station_key or not hmac.compare_digest(key, x_station_key):
        raise HTTPException(401, "unknown station or bad key")
    return ops.ingest_telemetry(station_id, body)


@app.get("/api/telemetry")
def telemetry():
    keys = _station_keys()
    seen = {r["station_id"]: r for r in ops.telemetry_summary()}
    return [{"station_id": k, "registered": True, **seen.get(k, {"packets": 0, "pdr": None,
                                                                  "last_received_utc": None})}
            for k in keys]


# ------------------------------------------------------------------ economics
@app.post("/api/economics")
def economics(body: dict = Body(default={})):
    st = store()
    h = int(body.get("h", 7))
    a, t = _aircraft(body.get("aircraft")), _treatment(body.get("treatment"))
    # average weekly workload over the archive, from the real forecast product
    ver = st.verification(h)
    alerts_per_issue = float(ver.alerts.mean())
    unc_per_issue = float(ver.uncertain.mean())
    issues_per_week = len(ver) / max(1, (pd.Timestamp(ver.date.max()) - pd.Timestamp(ver.date.min())).days / 7)
    targets_week = (alerts_per_issue + min(unc_per_issue, 30)) * issues_per_week
    treated_ha_week = alerts_per_issue * issues_per_week * t.patch_area_m2 / 10_000
    sample = plan_mission({"h": h, "aircraft": body.get("aircraft"), "treatment": body.get("treatment"),
                           "policy": {"max_targets": 40}})
    per_target_h = (sample["totals"]["field_day_min"] / 60 / max(1, sample["totals"]["targets"])) if sample["totals"] else 0.05
    guided_h_week = targets_week * per_target_h
    area = _area()["wet_prone_zone_ha"]
    blanket = P.lawnmower(area, a, t)
    res = ops.economics(body.get("inputs", {}), area, targets_week, treated_ha_week, guided_h_week,
                        blanket["flight_min"] / 60, 10_000 / (a.swath_m * a.work_speed_ms) / 3600)
    res["workload"] = {"issues_per_week": issues_per_week, "alerts_per_issue": alerts_per_issue,
                       "uncertain_per_issue": unc_per_issue, "targets_per_week": targets_week,
                       "guided_flight_h_per_week": guided_h_week,
                       "blanket_flight_h_per_week": blanket["flight_min"] / 60}
    return res


# ------------------------------------------------------------------- bulletin
@app.get("/api/bulletin", response_class=HTMLResponse)
def bulletin(h: int = 7, date: str | None = None, lang: str = "en"):
    st = store()
    d, f = st.forecast(h, date)
    fc = forecast(h, d)
    plan = plan_mission({"h": h, "date": d, "policy": {"max_targets": 40}})
    ops.audit("analyst", "bulletin.render", f"h={h} {d}")
    return render_bulletin(fc, plan, st, h, ENGINE.card(), _area(), lang)


# --------------------------------------------------------------------- static
@app.get("/", include_in_schema=False)
def index():
    return FileResponse(WEB / "index.html", headers={"Cache-Control": "no-store"})


app.mount("/", StaticFiles(directory=WEB), name="web")

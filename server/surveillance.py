"""National overview, surveillance map layers, habitat dossier, model workspace.

Read-only views over existing records and study products, plus model registry
decisions. Three rules run through this module:

  * a count with no underlying records is null ("No data available"), never 0;
  * record-level field locations and officer names need read:field; every other
    role gets aggregates;
  * what was predicted, observed by satellite or sensor, verified in the field,
    confirmed by dipping, authorised and completed stay separate.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import date as Date, timedelta
from pathlib import Path

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

import connectors as C
import field as F
import geo as G
import ops
import programme
import rules as R
import security as S
import training as T
from data import STATION, records, store
from db import now, tx
from engine import ENGINE, ROOT, V3_CARD
from security import User, current_user, require

router = APIRouter(tags=["surveillance"])
AUDIT = json.loads((ROOT / "config" / "model_audit.json").read_text(encoding="utf-8"))
SIGNAL = {"high": "Potential surface-water formation signal", "moderate": "Moderate forecast priority",
          "insufficient": "Evidence insufficient", "lower": "Lower forecast priority"}
STATUS_LABEL = {"not_assessed": "Not assessed", "inspection_required": "Inspection required", "evidence_insufficient": "Evidence insufficient",
                "eligible_for_review": "Eligible for review", "authorised": "Authorised", "rejected": "Rejected", "completed": "Completed",
                "follow_up_due": "Follow-up due", "closed": "Closed"}
NODATA = "No data available"


# ------------------------------------------------------------------ field index
def _in_range(ts: str | None, start: str | None, end: str | None) -> bool:
    if not start and not end:
        return True
    if not ts:
        return False
    d = ts[:10]
    return (not start or d >= start) and (not end or d <= end)


def field_index(start: str | None = None, end: str | None = None) -> dict:
    where = programme._scope(None)[0]
    with tx() as c:
        tasks = [dict(r) for r in c.execute(f"SELECT * FROM tasks WHERE {where}")]
        obs = [dict(r) for r in c.execute(f"SELECT * FROM observations WHERE {where} ORDER BY observed_at")]
        ivs = [dict(r) for r in c.execute(f"SELECT * FROM interventions WHERE {where}")]
        habs = [dict(r) for r in c.execute(f"SELECT * FROM habitats WHERE {where}")]
    tasks = [t for t in tasks if _in_range(t["created_utc"], start, end)]
    obs = [o for o in obs if _in_range(o["observed_at"], start, end)]
    ivs = [i for i in ivs if _in_range(i["authorized_utc"], start, end)]
    by: dict = defaultdict(lambda: {"tasks": [], "obs": [], "ivs": []})
    for t in tasks:
        by[t["site_id"]]["tasks"].append(t)
    for o in obs:
        by[o["site_id"]]["obs"].append(o)
    for i in ivs:
        by[i["site_id"]]["ivs"].append(i)
    return {"tasks": tasks, "obs": obs, "ivs": ivs, "habs": habs, "by": by}


def confirmed(o: dict) -> bool:
    return o["status"] == "verified" and o["larvae_present"] == "yes" and o["anopheles_present"] == "yes"


def site_status(rec: dict | None) -> str:
    if not rec:
        return "not_assessed"
    ivs = sorted(rec["ivs"], key=lambda i: i["id"])
    if ivs:
        last = ivs[-1]["status"]
        if last == "closed":
            return "closed"
        if last == "rejected":
            return "rejected"
        if last == "authorized":
            return "authorised"
        if last == "performed":
            open_fu = any(t["status"] in ("assigned", "in_progress") and "follow-up" in (t["reason"] or "").lower() for t in rec["tasks"])
            return "completed" if open_fu else "follow_up_due"
    verified = [o for o in rec["obs"] if o["status"] == "verified"]
    if verified:
        return "eligible_for_review" if F._eligible(verified[-1]) else "evidence_insufficient"
    if any(t["status"] in ("assigned", "in_progress") for t in rec["tasks"]):
        return "inspection_required"
    if rec["obs"]:
        return "evidence_insufficient"
    return "not_assessed"


def tier(rec: dict | None) -> str:
    """Strongest evidence held, in the order the programme uses it."""
    if not rec:
        return "predicted_or_satellite_only"
    if any(i["status"] in ("performed", "closed") for i in rec["ivs"]):
        return "treatment_completed"
    if any(i["status"] == "authorized" for i in rec["ivs"]):
        return "treatment_authorised"
    v = [o for o in rec["obs"] if o["status"] == "verified"]
    if any(confirmed(o) for o in v):
        return "entomologically_confirmed"
    if any(o["water_present"] == "yes" for o in v):
        return "field_verified"
    return "predicted_or_satellite_only"


def _n(value: int, has_records: bool) -> int | None:
    return value if has_records else None


def site_area(sid: str, idx: dict | None = None) -> dict:
    if sid.startswith("H"):
        with tx() as c:
            h = c.execute("SELECT lat, lon FROM habitats WHERE code=?", (sid,)).fetchone()
        return G.area_of(h["lat"], h["lon"]) if h else {"in_sector": None, "cell": None}
    return G.site_areas().get(sid, {"in_sector": None, "cell": None})


# ----------------------------------------------------------------- geography
@router.get("/geo/areas")
def geo_areas(u: User = Depends(require("read:forecast"))):
    return G.hierarchy()


@router.get("/geo/boundaries")
def geo_boundaries(u: User = Depends(require("read:forecast"))):
    b = G.boundaries()
    return {"type": "FeatureCollection", "sector": b["sector"], "cells": b["cells"], "marshland": b["marshland"], "marshland_note": b["marshland_note"]}


@router.get("/search")
def search(q: str = Query(min_length=1, max_length=40), u: User = Depends(require("read:forecast"))):
    ql = q.strip().lower()
    out = [{"type": "area", "id": n["code"], "label": f"{n['name']} ({n['level']})", "onboarded": n["onboarded"]} for n in G.hierarchy() if ql in n["name"].lower()]
    st = store().sites
    for r in st[st.site_id.str.lower().str.contains(ql, regex=False)].head(15).itertuples():
        out.append({"type": "site", "id": r.site_id, "label": f"Monitoring location {r.site_id}", "lat": r.latitude, "lon": r.longitude})
    if u.can("read:field"):
        with tx() as c:
            for h in c.execute("SELECT code, lat, lon FROM habitats WHERE LOWER(code) LIKE ? LIMIT 15", (f"%{ql}%",)):
                out.append({"type": "habitat", "id": h["code"], "label": f"Field-registered habitat {h['code']}", "lat": h["lat"], "lon": h["lon"]})
    return out


# ------------------------------------------------------------------ freshness
@router.get("/freshness")
def freshness(u: User = Depends(require("read:forecast"))):
    return {"sources": C.freshness(), "labels": ["Current", "Delayed", "Stale", "Unavailable", "Processing", "Historical"],
            "note": "Each source has its own status. No part of RINDA is real-time unless the source says Current."}


@router.get("/model-audit")
def model_audit(u: User = Depends(require("read:forecast"))):
    return AUDIT


# ------------------------------------------------------------------- overview
@router.get("/overview")
def overview(start: str | None = None, end: str | None = None, province: str | None = None, district: str | None = None, sector: str | None = None,
             cell: str | None = None, source: str | None = None, evidence: str | None = None, u: User = Depends(current_user)):
    if not u.can("read:forecast"):
        raise HTTPException(401, "Sign in to continue.")
    onboarded = (not province or province == "Kigali City") and (not district or district == "Gasabo") and (not sector or sector == "Gikomero")
    area_name = cell or sector or district or province or "Rwanda"
    idx = field_index(start, end)
    st = store()
    def keep(sid):
        if not onboarded:
            return False
        if cell and site_area(sid)["cell"] != cell:
            return False
        return True
    statuses = {sid: site_status(rec) for sid, rec in idx["by"].items() if keep(sid)}
    if evidence:
        statuses = {k: v for k, v in statuses.items() if v == evidence}
    keys = set(statuses)
    tasks = [t for t in idx["tasks"] if t["site_id"] in keys]
    obs = [o for o in idx["obs"] if o["site_id"] in keys]
    ivs = [i for i in idx["ivs"] if i["site_id"] in keys]
    verified = [o for o in obs if o["status"] == "verified"]
    sa = G.site_areas()
    if not onboarded:
        n_sites = None
    else:
        n_sites = sum(1 for s, v in sa.items() if v["cell"] == cell) if cell else len(sa)
        n_sites += sum(1 for h in idx["habs"] if keep(h["code"]))
    s1_rows = len(st.s1) if onboarded and (not source or source == "earth_observation") else None
    with tx() as c:
        env = c.execute("SELECT COUNT(*) FROM environmental_observations").fetchone()[0]
    counts = {
        "registered_locations": n_sites,
        "environmental_observations": None if s1_rows is None and not env else (s1_rows or 0) + env + (len(st.meteo) if onboarded and (not source or source == "meteorology") else 0),
        "inspections_pending": _n(sum(t["status"] in ("assigned", "in_progress") for t in tasks), bool(tasks)),
        "verified_water": _n(len({o["site_id"] for o in verified if o["water_present"] == "yes"}), bool(verified)),
        "confirmed_positive": _n(len({o["site_id"] for o in verified if confirmed(o)}), bool(verified)),
        "interventions_authorised": _n(sum(i["status"] in ("authorized", "performed", "closed") for i in ivs), bool(ivs)),
        "treatments_completed": _n(sum(i["status"] in ("performed", "closed") for i in ivs), bool(ivs)),
        "follow_up_due": _n(sum(v == "follow_up_due" for v in statuses.values()), bool(ivs)),
    }
    fr = C.freshness()
    if source:
        fr = [x for x in fr if x["key"] == source]
    return {"coverage": {"area": area_name, "onboarded": onboarded, "operational_sectors": 1, "sector": "Gikomero", "district": "Gasabo", "province": "Kigali City",
                         "sites_in_sector": sum(v["in_sector"] for v in sa.values()), "sites_outside_sector": sum(not v["in_sector"] for v in sa.values()),
                         "note": "Only the Gikomero pilot is onboarded. RINDA holds no coverage elsewhere in Rwanda."},
            "counts": counts, "no_data_label": NODATA, "freshness": fr, "actions": _actions(idx, u, keys if (cell or evidence) else None),
            "status_counts": dict(Counter(statuses.values())), "status_labels": STATUS_LABEL, "audit": AUDIT, "scope": programme._scope(None)[1],
            "filters": {"start": start, "end": end, "province": province, "district": district, "sector": sector, "cell": cell, "source": source, "evidence": evidence}}


def _actions(idx: dict, u: User, keys: set | None) -> list[dict]:
    today = R.today().isoformat()
    has = lambda sid: keys is None or sid in keys  # noqa: E731
    out = []
    def add(key, label, n, route, tone="info", perm=None):
        if n and (perm is None or u.can(perm)):
            out.append({"key": key, "label": label, "n": n, "route": route, "tone": tone})
    overdue = sum(t["status"] in ("assigned", "in_progress") and t["due_date"] < today and has(t["site_id"]) for t in idx["tasks"])
    pending = sum(o["status"] in ("submitted", "needs_review") and has(o["site_id"]) for o in idx["obs"])
    returned = sum(o["status"] == "returned" and has(o["site_id"]) for o in idx["obs"])
    statuses = {s: site_status(r) for s, r in idx["by"].items() if has(s)}
    add("overdue", "Inspections past their due date", overdue, "#/field?tab=tasks", "warn", "read:field")
    add("reviews", "Observations waiting for supervisor review", pending, "#/field?tab=review", "info", "review:observation")
    add("returned", "Observations returned for correction", returned, "#/field?tab=record", "warn", "write:observation")
    add("eligible", "Habitats ready for an intervention decision", sum(v == "eligible_for_review" for v in statuses.values()), "#/field?tab=review", "warn", "authorize:intervention")
    add("followup", "Treated habitats with no follow-up scheduled", sum(v == "follow_up_due" for v in statuses.values()), "#/field?tab=interventions", "warn", "read:field")
    with tx() as c:
        where = programme._scope(None)[0]
        m = c.execute(f"SELECT COUNT(*) FROM missions WHERE status='PLANNED' AND {where}").fetchone()[0]
        d = c.execute("SELECT COUNT(*) FROM dataset_versions WHERE status='validated'").fetchone()[0]
    add("missions", "Missions waiting for approval", m, "#/drone", "info", "approve:mission")
    add("datasets", "Datasets waiting for confirmation", d, "#/models?tab=datasets", "info", "manage:data")
    return out


# ------------------------------------------------------------------ map layers
def _forecast_rows(h: int, date: str | None) -> tuple[str, pd.DataFrame]:
    d, f = store().forecast(h, date)
    return d, f


@router.get("/map/forecast")
def map_forecast(h: int = 7, date: str | None = None, cell: str | None = None, model: str | None = None, u: User = Depends(require("read:forecast"))):
    if h not in (7, 14):
        raise HTTPException(422, "h must be 7 or 14.")
    st = store()
    sa = G.site_areas()
    if model:
        return _model_forecast(model, date, cell, u)
    dates = st.dates(h)
    if date and date < dates[0]:
        return {"available": False, "label": "Forecast unavailable", "message": f"The archive starts on {dates[0]}. No forecast was issued for this date.", "rows": [], "audit": AUDIT}
    d, f = _forecast_rows(h, date)
    meta = R.forecast_meta(h, d)
    rows = []
    for r in f.itertuples():
        a = sa.get(r.site_id, {})
        if cell and a.get("cell") != cell:
            continue
        cat = R.category(r.conformal_decision, r.probability)
        rows.append({"site_id": r.site_id, "lat": float(r.latitude), "lon": float(r.longitude), "category": cat, "label": SIGNAL[cat],
                     "probability": round(float(r.probability), 4), "uncertainty": str(r.uncertainty_band), "in_sector": a.get("in_sector"), "cell": a.get("cell"),
                     "observed": None if pd.isna(r.y) else int(r.y)})
    return {"available": True, "issue_date": d, "target_date": meta["target_date"], "horizon": h, "state": "Forecast stale" if meta["status"] == "stale" else "Research-only forecast",
            "label": "Research-only forecast", "model": {"version": ENGINE.version, "fingerprint": meta["model"]["fingerprint"], "status": "Research only"}, "age_days": meta["age_days"],
            "inputs": meta["inputs"], "limitations": [*meta["model_status"]["reasons"], AUDIT["summary"]], "rows": rows, "n": len(rows), "audit": AUDIT, "issues": st.dates(h)}


def _model_forecast(model: str, date: str | None, cell: str | None, u: User) -> dict:
    if not u.can("read:models"):
        raise HTTPException(403, "Candidate models are visible to analysts only.")
    with tx() as c:
        m = c.execute("SELECT r.*, t.predictions_path FROM model_registry r LEFT JOIN training_runs t ON t.id=r.training_run_id WHERE r.id=?", (model,)).fetchone()
    if not m or not m["predictions_path"]:
        raise HTTPException(404, "That model has no stored out-of-sample predictions.")
    p = pd.read_parquet(m["predictions_path"])
    issues = sorted(p.date.unique())
    d = [x for x in issues if not date or x <= date]
    if not d:
        return {"available": False, "label": "Forecast unavailable", "message": f"This run's test window starts on {issues[0]}.", "rows": [], "audit": AUDIT, "issues": issues}
    d = d[-1]
    s = store().sites.set_index("site_id")
    sa = G.site_areas()
    thr = json.loads(m["metrics"])["model"].get("threshold", 0.5)
    rows = []
    for r in p[p.date == d].itertuples():
        a = sa.get(r.site_id, {})
        if r.site_id not in s.index or (cell and a.get("cell") != cell):
            continue
        cat = "high" if r.probability >= thr else "lower"
        rows.append({"site_id": r.site_id, "lat": float(s.loc[r.site_id].latitude), "lon": float(s.loc[r.site_id].longitude), "category": cat, "label": SIGNAL[cat],
                     "probability": float(r.probability), "uncertainty": "not estimated", "in_sector": a.get("in_sector"), "cell": a.get("cell"), "observed": int(r.y)})
    return {"available": True, "issue_date": d, "horizon": m["horizon"], "state": "Research-only forecast", "label": "Research-only forecast",
            "target_date": (Date.fromisoformat(d) + timedelta(days=int(m["horizon"]))).isoformat(), "model": {"version": m["id"], "status": m["status"]}, "rows": rows, "n": len(rows), "issues": issues,
            "audit": AUDIT, "limitations": ["Out-of-sample predictions from a candidate run. Not served and not approved."]}


@router.get("/map/earth")
def map_earth(date: str | None = None, compare: str | None = None, cell: str | None = None, u: User = Depends(require("read:forecast"))):
    st = store()
    thr = st.water_thresholds
    d = date or st.s1_dates[-1]
    def snap(day: str) -> pd.DataFrame:
        at = st.s1[st.s1.date <= pd.Timestamp(day)]
        last = at.sort_values("date").groupby("site_id", observed=True).tail(2).copy()
        last["wet"] = (last.vv_db <= thr["vv_db_max"]) & (last.vh_db <= thr["vh_db_max"])
        return last
    cur = snap(d)
    latest = cur.groupby("site_id", observed=True).tail(1).set_index("site_id")
    prior = cur.groupby("site_id", observed=True).head(1).set_index("site_id")
    base = snap(compare).groupby("site_id", observed=True).tail(1).set_index("site_id") if compare else None
    s2 = _s2_at(d)
    sa = G.site_areas()
    rows = []
    for r in st.sites.itertuples():
        if r.site_id not in latest.index or (cell and sa[r.site_id]["cell"] != cell):
            continue
        a, b = latest.loc[r.site_id], prior.loc[r.site_id]
        row = {"site_id": r.site_id, "lat": r.latitude, "lon": r.longitude, "pass_date": a.date.strftime("%Y-%m-%d"), "previous_pass": b.date.strftime("%Y-%m-%d"), "vv_db": round(float(a.vv_db), 2),
               "vh_db": round(float(a.vh_db), 2), "wet": bool(a.wet), "previous_wet": bool(b.wet), "new_signal": bool(a.wet and not b.wet), "water_frequency": round(float(r.water_freq), 3), "in_sector": sa[r.site_id]["in_sector"]}
        if base is not None and r.site_id in base.index:
            row["compare_wet"] = bool(base.loc[r.site_id].wet)
            row["changed"] = bool(a.wet) != row["compare_wet"]
        if s2 is not None and r.site_id in s2.index:
            s = s2.loc[r.site_id]
            row.update({k: _num(s.get(v)) for k, v in (("ndwi", "S2_NDWI_50M"), ("mndwi", "S2_MNDWI_50M"), ("s2_age_days", "S2_AGE_DAYS_50M"))})
        rows.append(row)
    return {"date": d, "compare": compare, "thresholds": thr, "rows": rows, "dates": st.s1_dates, "kind": "Satellite-observed",
            "s2_available": s2 is not None, "caution": "A radar or optical water signal is not confirmed mosquito breeding. Thresholds are calibrated against Sentinel-2, not field observations.",
            "sentinel1c_note": AUDIT["findings"][1], "products": _products(d)}


def _num(v):
    return None if v is None or pd.isna(v) else round(float(v), 3)


def _s2_at(day: str):
    t = ENGINE.tables.get(7)
    if t is None or "S2_MNDWI_50M" not in t.columns:
        return None
    cut = pd.Timestamp(day)
    avail = t.date[t.date <= cut]
    if avail.empty:
        return None
    d = avail.max()
    return t[t.date == d].drop_duplicates("site_id").set_index("site_id")


def _products(day: str, limit: int = 12) -> list[dict]:
    with tx() as c:
        s1 = c.execute("SELECT * FROM raster_products WHERE platform LIKE 'SENTINEL-1%' AND acquired_date<=? ORDER BY acquired_date DESC LIMIT 3", (day,)).fetchall()
        s2 = c.execute("SELECT * FROM raster_products WHERE platform LIKE 'SENTINEL-2%' AND acquired_date<=? ORDER BY acquired_date DESC LIMIT 3", (day,)).fetchall()
    return [dict(r) for r in [*s1, *s2]][:limit]


@router.get("/earth/products")
def earth_products(date: str | None = None, platform: str | None = None, page: int = Query(1, ge=1), size: int = Query(50, ge=1, le=200), u: User = Depends(require("read:forecast"))):
    q, a = "FROM raster_products WHERE 1=1", []
    if date:
        q += " AND acquired_date<=?"
        a.append(date)
    if platform:
        q += " AND platform LIKE ?"
        a.append(f"{platform}%")
    with tx() as c:
        total = c.execute("SELECT COUNT(*) " + q, a).fetchone()[0]
        rows = [dict(r) for r in c.execute("SELECT * " + q + " ORDER BY acquired_date DESC LIMIT ? OFFSET ?", (*a, size, (page - 1) * size))]
    return {"total": total, "page": page, "rows": rows}


@router.get("/map/iot")
def map_iot(u: User = Depends(require("read:forecast"))):
    import main
    keys = main._station_keys()
    seen = {r["station_id"]: r for r in ops.telemetry_summary()}
    pos = C.IOT["positions"]
    nodes = []
    for k, v in keys.items():
        s = seen.get(k, {})
        p = pos.get(k) or {}
        last = _latest_env(k)
        status = C.node_status(s.get("last_received_utc"), str(v).startswith("replace-me"))
        if any(x["quality_flag"] == "out_of_range" for x in last):
            status = "Faulty"
        nodes.append({"node_id": k, "status": status, "lat": p.get("lat"), "lon": p.get("lon"), "last_received_utc": s.get("last_received_utc"), "packets": s.get("packets", 0), "pdr": s.get("pdr"), "latest": last})
    return {"nodes": nodes, "design": C.IOT["design"], "note": "Only readings actually received are shown. No node has reported yet." if not seen else "Readings as received."}


def _latest_env(node: str) -> list[dict]:
    with tx() as c:
        return [dict(r) for r in c.execute(
            "SELECT e.variable, e.value, e.unit, e.observed_utc, e.quality_flag FROM environmental_observations e JOIN "
            "(SELECT variable v, MAX(observed_utc) t FROM environmental_observations WHERE station_id=? GROUP BY variable) m "
            "ON e.variable=m.v AND e.observed_utc=m.t WHERE e.station_id=?", (node, node))]


@router.get("/iot/nodes/{node_id}")
def iot_node(node_id: str, days: int = Query(30, ge=1, le=365), u: User = Depends(require("read:forecast"))):
    import main
    keys = main._station_keys()
    if node_id not in keys:
        raise HTTPException(404, "Unknown node.")
    seen = {r["station_id"]: r for r in ops.telemetry_summary()}.get(node_id, {})
    since = (R.today() - timedelta(days=days)).isoformat()
    with tx() as c:
        rows = [dict(r) for r in c.execute("SELECT variable, value, unit, observed_utc, quality_flag FROM environmental_observations WHERE station_id=? AND observed_utc>=? ORDER BY observed_utc", (node_id, since))]
    series = defaultdict(list)
    for r in rows:
        series[r["variable"]].append([r["observed_utc"], r["value"], r["quality_flag"]])
    labels = C.IOT["variables"]
    measured = set(series)
    return {"node_id": node_id, "position": C.IOT["positions"].get(node_id), "status": C.node_status(seen.get("last_received_utc"), str(keys[node_id]).startswith("replace-me")),
            "inventory": C.IOT["design"], "latest": _latest_env(node_id), "last_received_utc": seen.get("last_received_utc"), "packets": seen.get("packets", 0), "pdr": seen.get("pdr"),
            "series": series, "units": {k: v["unit"] for k, v in labels.items()}, "labels": {k: v["label"] for k, v in labels.items()},
            "missing_measurements": [v["label"] for k, v in labels.items() if k not in measured], "bad_readings": sum(1 for r in rows if r["quality_flag"])}


@router.get("/map/meteo")
def map_meteo(date: str | None = None, u: User = Depends(require("read:forecast"))):
    st = store()
    m = st.meteo.sort_values("date")
    last = m.date.max()
    d = pd.Timestamp(date) if date else last
    at = m[m.date <= d]
    if at.empty:
        return {"available": False, "state": "Unavailable", "station": STATION, "message": "The station record starts later than this date."}
    r = at.iloc[-1]
    win = lambda n: float(at[at.date > d - pd.Timedelta(days=n)].rainfall_mm.sum())  # noqa: E731
    recent = at[at.date > d - pd.Timedelta(days=60)]
    return {"available": True, "state": "Historical", "station": STATION, "observed_date": r.date.strftime("%Y-%m-%d"), "lag_days": int((d - r.date).days), "record_ends": last.strftime("%Y-%m-%d"),
            "rainfall_mm": _num(r.rainfall_mm), "rain_7d_mm": round(win(7), 1), "rain_14d_mm": round(win(14), 1), "rain_30d_mm": round(win(30), 1), "temp_mean_c": None if pd.isna(r.temp_mean_c) else round(float(r.temp_mean_c), 1),
            "humidity_pct": round(float(r.humidity_mean_pct), 0) if bool(r.humidity_complete) and not pd.isna(r.humidity_mean_pct) else None, "humidity_note": None if bool(r.humidity_complete) else "Humidity readings were incomplete on this day.",
            "wind": None, "wind_note": "The Gikomero record holds no wind measurements.", "forecasts": [], "forecasts_note": "No numerical weather forecast feed is connected.",
            "series": records(recent[["rainfall_mm", "temp_mean_c"]].assign(date=recent.date.dt.strftime("%Y-%m-%d")))}


@router.get("/map/entomology")
def map_entomology(start: str | None = None, end: str | None = None, u: User = Depends(require("read:forecast"))):
    idx = field_index(start, end)
    with tx() as c:
        where = programme._scope(None)[0]
        samples = [dict(r) for r in c.execute(f"SELECT * FROM entomological_samples WHERE source='dataset' AND {where}")]
    samples = [s for s in samples if _in_range(s["sample_date"], start, end)]
    sites = store().sites.set_index("site_id")
    habs = {h["code"]: h for h in idx["habs"]}
    def pos(sid):
        if sid in sites.index:
            return float(sites.loc[sid].latitude), float(sites.loc[sid].longitude)
        if sid in habs:
            return habs[sid]["lat"], habs[sid]["lon"]
        return None, None
    points, cells = [], defaultdict(lambda: {"inspected": 0, "larvae_positive": 0, "larvae_negative": 0, "confirmed": 0})
    for sid, rec in idx["by"].items():
        if not rec["obs"]:
            continue
        last = rec["obs"][-1]
        kind = "breeding_confirmed" if confirmed(last) else "larvae_observed" if last["larvae_present"] == "yes" else "larvae_negative" if last["larvae_present"] == "no" else "water_observed"
        ar = site_area(sid)
        cl = ar["cell"] or ("Gikomero Sector (cell boundaries not loaded)" if ar["in_sector"] else "Outside the sector")
        cells[cl]["inspected"] += 1
        cells[cl]["larvae_positive"] += last["larvae_present"] == "yes"
        cells[cl]["larvae_negative"] += last["larvae_present"] == "no"
        cells[cl]["confirmed"] += confirmed(last)
        lat, lon = pos(sid)
        if lat is None:
            continue
        lpd = round(last["larvae_count"] / last["dips"], 2) if last["dips"] and last["larvae_count"] is not None and last["larvae_present"] == "yes" else None
        points.append({"site_id": sid, "lat": lat, "lon": lon, "kind": kind, "last_inspection": last["observed_at"][:10], "review_status": last["status"], "species": [x for x in (last["species"] or "").split(",") if x],
                       "stages": [x for x in (last["stages"] or "").split(",") if x], "pupae": last["pupae_present"], "larvae_per_dip": lpd, "inspections": len(rec["obs"]), "demo": bool(last["demo"])})
    for s in samples:
        if s["lat"] is None and s["site_id"]:
            s["lat"], s["lon"] = pos(s["site_id"])
        if s["lat"] is not None:
            points.append({"site_id": s["site_id"] or f"S{s['id']}", "lat": s["lat"], "lon": s["lon"], "kind": "survey_positive" if s["larvae_present"] == "yes" else "survey_negative", "last_inspection": s["sample_date"], "review_status": s["provenance"],
                           "species": [x for x in (s["species"] or "").split(",") if x], "stages": [], "pupae": None, "larvae_per_dip": None, "inspections": 1, "resistance": s["resistance_finding"], "demo": bool(s["demo"])})
    has = bool(points or cells)
    if not u.can("read:field"):
        return {"restricted": True, "by_cell": cells if has else {}, "points": [], "message": "Exact field locations need a field, analyst or supervisor account. Counts by cell are shown instead."}
    return {"restricted": False, "points": points, "by_cell": cells, "message": None if has else "No entomological records yet. RBC survey data has been requested and is not yet received."}


@router.get("/map/operations")
def map_operations(u: User = Depends(require("read:forecast"))):
    idx = field_index()
    counts = Counter(site_status(r) for r in idx["by"].values())
    if not u.can("read:field"):
        return {"restricted": True, "points": [], "status_counts": counts, "status_labels": STATUS_LABEL, "message": "Operational locations need a field, analyst or supervisor account."}
    sites = store().sites.set_index("site_id")
    habs = {h["code"]: h for h in idx["habs"]}
    names = S.user_names()
    points = []
    for sid, rec in idx["by"].items():
        lat, lon = (float(sites.loc[sid].latitude), float(sites.loc[sid].longitude)) if sid in sites.index else (habs[sid]["lat"], habs[sid]["lon"]) if sid in habs else (None, None)
        if lat is None:
            continue
        st = site_status(rec)
        open_t = [t for t in rec["tasks"] if t["status"] in ("assigned", "in_progress")]
        points.append({"site_id": sid, "lat": lat, "lon": lon, "status": st, "label": STATUS_LABEL[st], "open_tasks": len(open_t), "officer": names.get(open_t[-1]["assignee_id"]) if open_t else None, "due": open_t[-1]["due_date"] if open_t else None})
    with tx() as c:
        missions = c.execute("SELECT id, status, kind, result FROM missions WHERE status IN ('APPROVED','IN_FLIGHT') AND " + programme._scope(None)[0]).fetchall()
    targets = []
    for m in missions:
        for s in json.loads(m["result"]).get("sorties", []):
            for w in s["waypoints"]:
                targets.append({"mission": m["id"], "kind": m["kind"], "status": m["status"], "site_id": w["site_id"], "lat": w["lat"], "lon": w["lon"], "action": w["action"]})
    return {"restricted": False, "points": points, "mission_targets": targets, "status_counts": counts, "status_labels": STATUS_LABEL}


# --------------------------------------------------------------------- dossier
@router.get("/dossier/{sid}")
def dossier(sid: str, u: User = Depends(require("read:forecast"))):
    st = store()
    is_habitat = sid.startswith("H")
    base, habitat = None, None
    if is_habitat:
        with tx() as c:
            r = c.execute("SELECT * FROM habitats WHERE code=?", (sid,)).fetchone()
        if not r:
            raise HTTPException(404, "Unknown habitat.")
        if not u.can("read:field"):
            raise HTTPException(403, "Field-registered habitats are visible to field, analyst and supervisor accounts.")
        habitat = dict(r)
        lat, lon = habitat["lat"], habitat["lon"]
    else:
        base = st.site(sid)
        if not base:
            raise HTTPException(404, "Unknown monitoring location.")
        lat, lon = base["lat"], base["lon"]
        for h, rows in base["forecasts"].items():
            for x in rows:
                x["category"] = R.category(x["conformal_decision"], x["probability"])
                x["label"] = SIGNAL[x["category"]]
    idx = field_index()
    rec = idx["by"].get(sid)
    full = u.can("read:field")
    status = site_status(rec)
    names = S.user_names() if full else {}
    with tx() as c:
        samples = [dict(r) for r in c.execute("SELECT * FROM entomological_samples WHERE site_id=? ORDER BY sample_date", (sid,))]
        photos = [dict(r) for r in c.execute("SELECT p.id, p.observation_id, p.uploaded_utc, p.content_type FROM observation_photos p JOIN observations o ON o.id=p.observation_id WHERE o.site_id=?", (sid,))] if full else []
        missions = [dict(r) for r in c.execute("SELECT id, status, kind, created_utc, result FROM missions")]
        trail = [dict(r) for r in c.execute("SELECT ts_utc, actor, action, detail FROM audit WHERE detail LIKE ? ORDER BY id DESC LIMIT 100", (f"%{sid}%",))] if u.can("read:audit") else None
    flown = [{k: m[k] for k in ("id", "status", "kind", "created_utc")} for m in missions if any(w["site_id"] == sid for s in json.loads(m["result"]).get("sorties", []) for w in s["waypoints"])]
    t = _timeline(sid, base, rec, samples, full)
    latest_open = next((x for x in reversed(rec["tasks"]) if x["status"] in ("assigned", "in_progress")), None) if rec else None
    area = site_area(sid)
    meteo = map_meteo(None, u)
    meteo.pop("series", None)
    out = {"id": sid, "kind": "habitat" if is_habitat else "monitoring_location", "lat": lat, "lon": lon, "habitat": habitat,
           "administrative": {"country": "Rwanda", "province": "Kigali City", "district": "Gasabo", "sector": "Gikomero" if area["in_sector"] else None, "cell": area["cell"], "in_sector": area["in_sector"],
                              "note": None if area["in_sector"] else "This location is outside Gikomero Sector."},
           "status": status, "status_label": STATUS_LABEL[status], "evidence_tier": tier(rec), "responsible_officer": names.get(latest_open["assignee_id"]) if latest_open else None,
           "satellite": None if not base else {"radar": base["radar"], "wet_fraction": base["wet_fraction"], "n_acquisitions": base["n_acquisitions"], "water_thresholds": base["water_thresholds"], "historical_water_frequency": base["historical_water_frequency"]},
           "forecasts": None if not base else base["forecasts"], "iot": {"message": "No field node is linked to this location."}, "meteorology": meteo, "timeline": t, "missions": flown if full else None, "audit_history": trail,
           "model_audit": AUDIT, "protocol": F.protocol_public()}
    if full:
        o = rec["obs"] if rec else []
        for x in o:
            x["observer"] = names.get(x["observer_id"])
            x["reviewer"] = names.get(x["reviewer_id"])
        out["observations"] = o
        out["samples"] = samples
        out["tasks"] = [dict(x, assignee=names.get(x["assignee_id"])) for x in (rec["tasks"] if rec else [])]
        out["interventions"] = rec["ivs"] if rec else []
        out["photos"] = photos
        out["restricted"] = False
    else:
        out["restricted"] = True
        out["field_counts"] = {"observations": len(rec["obs"]) if rec else 0, "interventions": len(rec["ivs"]) if rec else 0}
    return out


def _timeline(sid: str, base: dict | None, rec: dict | None, samples: list[dict], full: bool) -> list[dict]:
    ev = []
    if base:
        prev = None
        for r in base["radar"]:
            if r["wet"] and prev is False:
                ev.append({"date": r["date"], "kind": "satellite_observed", "label": "Sentinel-1 water signal began", "detail": f"VV {r['vv_db']:.1f} dB, VH {r['vh_db']:.1f} dB"})
            prev = r["wet"]
        for h, rows in base["forecasts"].items():
            for x in rows:
                if x["category"] == "high":
                    ev.append({"date": x["date"], "kind": "predicted", "label": f"{x['label']} ({h}-day)", "detail": f"Research-only. Score {x['probability']:.2f}."})
    if rec:
        for o in rec["obs"]:
            if o["status"] != "verified":
                if full:
                    ev.append({"date": o["observed_at"][:10], "kind": "field_recorded", "label": f"Field record O{o['id']}: {o['status'].replace('_', ' ')}", "detail": "Not yet verified."})
                continue
            if o["water_present"] == "yes":
                ev.append({"date": o["observed_at"][:10], "kind": "field_verified", "label": "Water verified in the field", "detail": f"Observation O{o['id']}" if full else ""})
            if confirmed(o):
                ev.append({"date": o["observed_at"][:10], "kind": "entomologically_confirmed", "label": "Anopheles larvae confirmed", "detail": f"Observation O{o['id']}" if full else ""})
        for i in rec["ivs"]:
            if i["status"] == "rejected":
                ev.append({"date": (i["authorized_utc"] or "")[:10], "kind": "treatment_rejected", "label": "Treatment not authorised", "detail": i["decision_reason"] if full else ""})
                continue
            ev.append({"date": (i["authorized_utc"] or "")[:10], "kind": "treatment_authorised", "label": f"Treatment authorised: {i['method'].replace('_', ' ')}", "detail": f"Protocol {i['protocol_ref']}" if full else ""})
            if i["performed_at"]:
                ev.append({"date": i["performed_at"][:10], "kind": "treatment_completed", "label": "Treatment completed", "detail": ""})
            if i["closed_utc"]:
                ev.append({"date": i["closed_utc"][:10], "kind": "follow_up", "label": f"Follow-up: {(i['outcome'] or '').replace('_', ' ')}", "detail": ""})
    for s in samples:
        ev.append({"date": s["sample_date"], "kind": "survey_record", "label": f"Survey sample: larvae {s['larvae_present']}", "detail": s["provenance"]})
    return sorted(ev, key=lambda e: e["date"], reverse=True)


# -------------------------------------------------------------- model workspace
def seed_registry() -> None:
    entries = []
    if V3_CARD:
        for h in (7, 14):
            ev = V3_CARD["evaluation"][str(h)]["full"]
            entries.append((f"v3-h{h}", "A", h, "v3", "research_only", json.dumps({"model": {"pr_auc": ev["pr_auc"], "precision": ev["precision_threshold"]["precision"], "recall": ev["precision_threshold"]["recall"]},
                            "baselines": {"B1_recent_water_frequency_30d": {"pr_auc": ev["B1_pr_auc"]}}, "window": [ev["date_from"], ev["date_to"]]}), 1,
                            "Currently displayed. Research only under the 5 November 2026 audit."))
    entries += [("v1-h7", "A", 7, "v1", "archived", json.dumps({"model": {"pr_auc": 0.5807}}), 0, "Archived Paper 1 run. Beaten by persistence (0.628)."),
                ("v1-h14", "A", 14, "v1", "archived", json.dumps({"model": {"pr_auc": 0.310}}), 0, "Archived Paper 1 run. Beaten by persistence (0.629).")]
    with tx() as c:
        for e in entries:
            c.execute("INSERT OR IGNORE INTO model_registry (id, target, horizon, version, status, metrics, audit_warning, notes, created_utc) VALUES (?,?,?,?,?,?,?,?,?)", (*e, now()))


class RunIn(BaseModel):
    target: str = Field(pattern=r"^[A-E]$")
    horizon: int = 7
    dataset_id: int
    seed: int = 42
    train_end: str | None = None
    cal_end: str | None = None
    n_estimators: int = Field(default=300, ge=20, le=2000)
    max_depth: int = Field(default=5, ge=1, le=12)
    learning_rate: float = Field(default=0.05, gt=0, le=1)


@router.post("/training/runs", status_code=201)
def start_run(b: RunIn, u: User = Depends(require("train:model"))):
    import os
    if os.environ.get("RINDA_TRAINING", "1") == "0":
        raise HTTPException(409, "Training is turned off on this deployment because of its memory limit. Run RINDA on a larger server to train models.")
    if b.horizon not in (7, 14):
        raise HTTPException(422, "Horizon must be 7 or 14. Each horizon is evaluated separately.")
    with tx() as c:
        ds = c.execute("SELECT * FROM dataset_versions WHERE id=?", (b.dataset_id,)).fetchone()
        running = c.execute("SELECT COUNT(*) FROM training_runs WHERE status IN ('queued','running')").fetchone()[0]
    if not ds:
        raise HTTPException(404, "Unknown dataset.")
    if ds["status"] != "confirmed":
        raise HTTPException(409, "Confirm the dataset's quality report before training on it.")
    if ds["kind"] != "training_table":
        raise HTTPException(409, "Training needs a prepared table with site_id, date and y.")
    if ds["target"] != b.target:
        raise HTTPException(409, f"This table holds labels for target {ds['target']}. Targets are never mixed.")
    if ds["horizon"] and ds["horizon"] != b.horizon:
        raise HTTPException(409, f"This table was built for a {ds['horizon']}-day horizon.")
    if running:
        raise HTTPException(409, "Another training run is in progress.")
    cfg = b.model_dump(exclude={"target", "horizon", "dataset_id", "seed"})
    with tx() as c:
        cur = c.execute("INSERT INTO training_runs (target, horizon, dataset_version_id, config, status, seed, created_by, created_utc) VALUES (?,?,?,?,?,?,?,?)",
                        (b.target, b.horizon, b.dataset_id, json.dumps(cfg), "queued", b.seed, u.email, now()))
        rid = cur.lastrowid
    ops.audit(u.email, "training.start", f"run {rid} target={b.target} h={b.horizon} dataset=D{b.dataset_id} seed={b.seed}")
    T.start_job(rid)
    return {"id": rid, "status": "queued"}


@router.get("/training/targets")
def training_targets(u: User = Depends(require("read:models"))):
    with tx() as c:
        have = {r[0] for r in c.execute("SELECT DISTINCT target FROM dataset_versions WHERE kind='training_table' AND status='confirmed'")}
    return [{"target": k, "label": v["label"], "ready": k in have, "needs": v["needs"] if k not in have else None} for k, v in T.TARGET_RULES.items()]


@router.get("/training/runs")
def list_runs(u: User = Depends(require("read:models"))):
    with tx() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM training_runs ORDER BY id DESC LIMIT 100")]
    for r in rows:
        r["metrics"] = json.loads(r["metrics"]) if r["metrics"] else None
        r["config"] = json.loads(r["config"]) if r["config"] else None
        r.pop("predictions_path", None)
    return rows


@router.get("/training/runs/{rid}")
def get_run(rid: int, u: User = Depends(require("read:models"))):
    return next((r for r in list_runs(u) if r["id"] == rid), None) or _nf()


def _nf():
    raise HTTPException(404, "Unknown run.")


@router.get("/models")
def list_models(u: User = Depends(require("read:models"))):
    with tx() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM model_registry ORDER BY created_utc DESC, id")]
    for r in rows:
        r["metrics"] = json.loads(r["metrics"]) if r["metrics"] else None
        r.pop("path", None)
    return {"models": rows, "displayed": "v3", "audit": AUDIT}


class Decision(BaseModel):
    status: str = Field(pattern=r"^(approved_candidate|rejected|archived)$")
    note: str = Field(min_length=5, max_length=1000)


@router.post("/models/{mid}/decision")
def decide(mid: str, b: Decision, u: User = Depends(require("decide:model"))):
    with tx() as c:
        m = c.execute("SELECT * FROM model_registry WHERE id=?", (mid,)).fetchone()
    if not m:
        raise HTTPException(404, "Unknown model.")
    if m["status"] == "synthetic_fixture" and b.status == "approved_candidate":
        raise HTTPException(409, "A model trained on synthetic data can never be approved.")
    if b.status == "approved_candidate" and m["target"] == "A" and not AUDIT["label_reconstruction_complete"]:
        raise HTTPException(409, "Blocked by the 5 November 2026 audit: " + AUDIT["required_before_upgrade"])
    with tx() as c:
        c.execute("UPDATE model_registry SET status=?, notes=?, decided_by=?, decided_utc=? WHERE id=?", (b.status, b.note, u.email, now(), mid))
    ops.audit(u.email, "model.decision", f"{mid} -> {b.status}: {b.note}")
    return {"ok": True, "displayed_model_changed": False}


@router.get("/connectors")
def list_connectors(u: User = Depends(require("read:models"))):
    with tx() as c:
        runs = [dict(r) for r in c.execute("SELECT * FROM ingestion_runs ORDER BY id DESC LIMIT 50")]
    return {"connectors": [x.describe() for x in C.CONNECTORS.values()], "runs": runs, "freshness": C.freshness()}


@router.post("/connectors/{key}/run")
def run_connector(key: str, u: User = Depends(require("manage:data"))):
    if key not in C.CONNECTORS:
        raise HTTPException(404, "Unknown connector.")
    r = C.run_connector(key, u.email)
    ops.audit(u.email, "connector.run", f"{key} {r['status']}")
    return r


class IngestIn(BaseModel):
    records: list[dict] = Field(max_length=5000)
    provenance: str = Field(default="REAL", pattern=r"^(REAL|SYNTHETIC)$")


@router.post("/connectors/{key}/records")
def push_records(key: str, b: IngestIn, u: User = Depends(require("manage:data"))):
    if key not in ("meteorology", "iot", "earth_observation"):
        raise HTTPException(422, "This source does not accept environmental records.")
    prov = "SYNTHETIC" if u.demo else b.provenance
    r = C.ingest(key, b.records, u.email, prov)
    ops.audit(u.email, "connector.records", f"{key} {prov} {r['accepted']} accepted")
    return r | {"provenance": prov}


__all__ = ["Path"]

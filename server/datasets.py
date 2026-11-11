"""Historical dataset registry: upload, validate, confirm, version.

Original bytes are kept untouched in var/datasets/raw. A dataset can be used for
training only after a person has read its quality report and confirmed it.
Confirming writes a new versioned processed file; nothing is overwritten.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile

import ops
from data import store
from db import now, tx
from engine import HF, VAR
from security import User, require

router = APIRouter(tags=["datasets"])
RAW = VAR / "datasets" / "raw"
PROCESSED = VAR / "datasets" / "processed"
MAX_MB = int(os.environ.get("RINDA_MAX_DATASET_MB", "20"))
RW = (-2.9, -1.0, 28.8, 30.95)
GIK = (-2.05, -1.75, 30.05, 30.40)  # pilot working box
TARGETS = {"A": "New surface-water formation", "B": "Persistence of observed water", "C": "Field-verified water presence",
           "D": "Entomologically confirmed mosquito breeding", "E": "Larval abundance or density"}

_LOC = {"any_of": [["site_id"], ["habitat_id"], ["latitude", "longitude"], ["lat", "lon"]]}
KINDS: dict[str, dict] = {
    "sentinel1": {"label": "Sentinel-1", "date": "date", "required": ["date", "vv_db", "vh_db"], **_LOC, "keys": ["site_id", "date"],
                  "ranges": {"vv_db": (-50, 20, "dB"), "vh_db": (-50, 20, "dB")}},
    "sentinel2": {"label": "Sentinel-2", "date": "date", "required": ["date"], **_LOC, "keys": ["site_id", "date"],
                  "ranges": {"ndwi": (-1, 1, "index"), "mndwi": (-1, 1, "index"), "ndvi": (-1, 1, "index"), "cloud_pct": (0, 100, "%")}},
    "rainfall": {"label": "Rainfall", "date": "date", "required": ["date", "rainfall_mm"], "keys": ["station_id", "date"],
                 "ranges": {"rainfall_mm": (0, 500, "mm")}},
    "temperature": {"label": "Temperature", "date": "date", "required": ["date", "temp_c"], "keys": ["station_id", "date"],
                    "ranges": {"temp_c": (-10, 50, "degC")}},
    "humidity": {"label": "Humidity", "date": "date", "required": ["date", "humidity_pct"], "keys": ["station_id", "date"],
                 "ranges": {"humidity_pct": (0, 100, "%")}},
    "meteorology": {"label": "Other meteorological observations", "date": "date", "required": ["date"], "keys": ["station_id", "date"],
                    "ranges": {"rainfall_mm": (0, 500, "mm"), "temp_c": (-10, 50, "degC"), "humidity_pct": (0, 100, "%"),
                               "wind_speed_ms": (0, 80, "m/s"), "wind_dir_deg": (0, 360, "deg")}},
    "iot": {"label": "IoT environmental records", "date": "ts_utc", "required": ["node_id", "ts_utc", "variable", "value", "unit"],
            "keys": ["node_id", "ts_utc", "variable"], "ranges": {}},
    "entomology": {"label": "Entomological observations", "date": "sample_date",
                   "required": ["sample_date", "sampling_method", "dips", "larvae_present"], **_LOC, "keys": ["site_id", "lat", "lon", "sample_date", "sampling_method"],
                   "ranges": {"dips": (0, 500, "count"), "anopheles_larvae_count": (0, 100000, "count"), "pupae_count": (0, 100000, "count")}},
    "habitat": {"label": "Habitat characteristics", "date": "survey_date", "required": ["habitat_id", "habitat_type"], **_LOC, "keys": ["habitat_id"],
                "ranges": {"water_depth_cm": (0, 500, "cm"), "vegetation_cover_pct": (0, 100, "%")}},
    "water_observation": {"label": "Historical water observations", "date": "date", "required": ["date", "water_present"], **_LOC, "keys": ["site_id", "date"], "ranges": {}},
    "training_table": {"label": "Prepared training table", "date": "date", "required": ["site_id", "date", "y"], "keys": ["site_id", "date"], "ranges": {"y": (0, 1000000, "label")}},
}


def _check(cid: str, label: str, status: str, detail: str) -> dict:
    return {"id": cid, "label": label, "status": status, "detail": detail}


def read_frame(raw: bytes, filename: str) -> pd.DataFrame:
    name = filename.lower()
    if name.endswith(".csv"):
        return pd.read_csv(io.BytesIO(raw))
    if name.endswith(".parquet"):
        return pd.read_parquet(io.BytesIO(raw))
    if name.endswith((".geojson", ".json")):
        g = json.loads(raw.decode("utf-8"))
        rows = []
        for f in g["features"]:
            if f["geometry"]["type"] != "Point":
                raise ValueError("Only Point features can be read as a table.")
            lon, lat = f["geometry"]["coordinates"][:2]
            rows.append({**f["properties"], "lat": lat, "lon": lon})
        return pd.DataFrame(rows)
    raise ValueError("Use CSV, Parquet or GeoJSON.")


def validate(df: pd.DataFrame, kind: str, target: str | None = None, horizon: int | None = None) -> dict:
    """The eleven-step quality report. A failed step blocks confirmation."""
    spec = KINDS[kind]
    checks = [_check("file", "File can be read", "pass", f"{len(df):,} rows, {df.shape[1]} columns")]
    if df.empty:
        checks.append(_check("rows", "File has rows", "fail", "The file has no rows."))
        return _report(df, checks, None, None)
    cols = list(df.columns)
    miss = [c for c in spec["required"] if c not in cols]
    any_of = spec.get("any_of")
    loc_ok = not any_of or any(all(c in cols for c in g) for g in any_of)
    detail = f"Found: {', '.join(cols[:30])}{' ...' if len(cols) > 30 else ''}."
    if miss:
        detail += f" Missing: {', '.join(miss)}."
    if not loc_ok:
        detail += " Needs a site id, habitat id or coordinates."
    checks.append(_check("columns", "Required columns", "fail" if miss or not loc_ok else "pass", detail))

    dcol = spec["date"] if spec["date"] in cols else None
    dmin = dmax = None
    if dcol:
        d = pd.to_datetime(df[dcol], errors="coerce", utc=True)
        bad = int(d.isna().sum())
        future = int((d > pd.Timestamp(datetime.now(timezone.utc))).sum())
        status = "fail" if future or bad == len(df) else "warn" if bad else "pass"
        checks.append(_check("dates", "Dates are valid", status, f"{bad:,} unreadable, {future:,} in the future."))
        if d.notna().any():
            dmin, dmax = d.min().strftime("%Y-%m-%d"), d.max().strftime("%Y-%m-%d")
            checks.append(_check("temporal", "Temporal coverage", "pass", f"{dmin} to {dmax}."))
    elif kind != "habitat":
        checks.append(_check("dates", "Dates are valid", "fail", f"No {spec['date']} column."))

    latc = next((c for c in ("lat", "latitude") if c in cols), None)
    lonc = next((c for c in ("lon", "longitude") if c in cols), None)
    if latc and lonc:
        la, lo = pd.to_numeric(df[latc], errors="coerce"), pd.to_numeric(df[lonc], errors="coerce")
        outside = int((~la.between(RW[0], RW[1]) | ~lo.between(RW[2], RW[3])).sum())
        checks.append(_check("gps", "GPS coordinates are inside Rwanda", "fail" if outside == len(df) else "warn" if outside else "pass",
                             f"{outside:,} rows outside Rwanda or unreadable."))
        pilot = float((la.between(GIK[0], GIK[1]) & lo.between(GIK[2], GIK[3])).mean())
        checks.append(_check("geographic", "Geographic coverage", "pass" if pilot > 0 else "warn", f"{pilot:.0%} of rows are in the Gikomero working area."))
    elif "site_id" in cols:
        known = set(store().sites.site_id)
        share = float(df["site_id"].astype(str).isin(known).mean())
        checks.append(_check("geographic", "Geographic coverage", "pass" if share > 0.5 else "warn", f"{share:.0%} of rows match a registered monitoring point."))
    else:
        checks.append(_check("geographic", "Geographic coverage", "warn", "No coordinates or site id to check against the pilot area."))

    keys = [c for c in spec["keys"] if c in cols]
    dups = int(df.duplicated(subset=keys or None).sum())
    checks.append(_check("duplicates", "Duplicate records", "warn" if dups else "pass", f"{dups:,} duplicates on {', '.join(keys) or 'all columns'}. Duplicates are dropped at confirmation."))

    na = df.isna().mean().sort_values(ascending=False)
    worst = {k: round(float(v), 3) for k, v in na.head(8).items() if v > 0}
    req_na = [c for c in spec["required"] if c in cols and df[c].isna().any()]
    checks.append(_check("missing", "Missing values", "warn" if worst else "pass", f"Highest missing shares: {worst}." if worst else "No missing values."))
    if req_na:
        checks.append(_check("missing_required", "Required columns are complete", "warn", f"Gaps in {', '.join(req_na)}. Those rows are excluded."))

    unit_issues = []
    for c, (lo, hi, unit) in spec["ranges"].items():
        if c in cols:
            v = pd.to_numeric(df[c], errors="coerce")
            bad = int((v.notna() & ~v.between(lo, hi)).sum())
            if bad:
                unit_issues.append(f"{c}: {bad:,} outside {lo} to {hi} {unit}")
    checks.append(_check("units", "Measurement units and ranges", "fail" if unit_issues else "pass", "; ".join(unit_issues) or "Values fall in the expected ranges."))

    if kind == "training_table":
        if target not in TARGETS:
            checks.append(_check("target", "Scientific target stated", "fail", f"Choose one of {', '.join(TARGETS)}."))
        else:
            checks.append(_check("target", "Scientific target stated", "pass", f"{target}: {TARGETS[target]}."))
        if horizon not in (7, 14) and target in ("A", "B"):
            checks.append(_check("horizon", "Forecast horizon", "fail", "State 7 or 14 days."))
        if "y" in cols:
            y = pd.to_numeric(df["y"], errors="coerce")
            leak = [c for c in cols if c not in ("y", "site_id", "date") and pd.api.types.is_numeric_dtype(df[c])
                    and df[c].notna().sum() > 10 and y.notna().sum() > 10 and abs(float(df[c].corr(y))) > 0.999]
            checks.append(_check("leakage", "No column copies the target", "fail" if leak else "pass", f"Columns matching the target: {leak}." if leak else "No column reproduces the target."))
    return _report(df, checks, dmin, dmax)


def _report(df, checks, dmin, dmax) -> dict:
    return {"rows": int(len(df)), "columns": [{"name": c, "type": str(df[c].dtype), "missing": round(float(df[c].isna().mean()), 3)} for c in df.columns[:200]],
            "date_min": dmin, "date_max": dmax, "checks": checks, "passed": not any(c["status"] == "fail" for c in checks),
            "warnings": sum(c["status"] == "warn" for c in checks)}


def _row(r) -> dict:
    d = dict(r)
    d["report"] = json.loads(d["report"]) if d.get("report") else None
    d["columns"] = json.loads(d["columns"]) if d.get("columns") else []
    d.pop("raw_path", None)
    d.pop("processed_path", None)
    return d


def seed_study_tables() -> None:
    """Register the frozen study tables so they can be retrained on. Read-only references."""
    for h in (7, 14):
        rel = HF / "data" / "processed" / f"table_A_new_water_h{h}.parquet"
        man = rel.with_name(rel.stem + "_manifest.json")
        if not rel.exists() or not man.exists():
            continue
        m = json.loads(man.read_text())
        with tx() as c:
            if c.execute("SELECT 1 FROM dataset_versions WHERE kind='training_table' AND sha256=?", (m["fingerprint"],)).fetchone():
                continue
            ver = c.execute("SELECT COALESCE(MAX(version),0)+1 FROM dataset_versions WHERE kind='training_table'").fetchone()[0]
            c.execute("INSERT INTO dataset_versions (kind, version, label, filename, sha256, raw_path, processed_path, n_rows, columns, report, status, "
                      "provenance, target, horizon, created_by, created_utc, confirmed_by, confirmed_utc) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      ("training_table", ver, f"Study table A, {h}-day (archived Paper 1 labels)", rel.name, m["fingerprint"], str(rel), str(rel), m["n_rows"],
                       json.dumps(m["feature_columns"]),
                       json.dumps({"rows": m["n_rows"], "columns": [], "date_min": m["date_min"], "date_max": m["date_max"], "passed": True, "warnings": 1,
                                   "checks": [_check("provenance", "Study table", "warn", "Frozen study table. Its labels carry the problems found in the 5 November 2026 audit.")]}),
                       "confirmed", "STUDY", "A", h, "study", now(), "study", now()))


@router.get("/datasets/kinds")
def kinds(u: User = Depends(require("read:models"))):
    return {"kinds": [{"key": k, "label": v["label"], "required": v["required"], "any_of": v.get("any_of"), "date": v["date"]} for k, v in KINDS.items()],
            "targets": TARGETS, "max_upload_mb": MAX_MB}


@router.get("/datasets")
def list_datasets(kind: str | None = None, page: int = Query(1, ge=1), size: int = Query(50, ge=1, le=200), u: User = Depends(require("read:models"))):
    q, a = "SELECT * FROM dataset_versions", []
    if kind:
        q += " WHERE kind=?"
        a.append(kind)
    with tx() as c:
        total = c.execute(q.replace("*", "COUNT(*)", 1), a).fetchone()[0]
        rows = [_row(r) for r in c.execute(q + " ORDER BY id DESC LIMIT ? OFFSET ?", (*a, size, (page - 1) * size))]
    for r in rows:
        r["columns"] = r["columns"][:0] if r["kind"] == "training_table" else r["columns"]
    return {"total": total, "page": page, "rows": rows}


@router.post("/datasets/upload", status_code=201)
async def upload(kind: str = Form(...), label: str = Form(""), provenance: str = Form("REAL"), target: str | None = Form(None),
                 horizon: int | None = Form(None), file: UploadFile = File(...), u: User = Depends(require("manage:data"))):
    if kind not in KINDS:
        raise HTTPException(422, f"kind must be one of {', '.join(KINDS)}.")
    if provenance not in ("REAL", "SYNTHETIC"):
        raise HTTPException(422, "provenance must be REAL or SYNTHETIC.")
    if u.demo:
        provenance = "SYNTHETIC"  # demonstration accounts cannot create real records
    raw = await file.read(MAX_MB * 1024 * 1024 + 1)
    if len(raw) > MAX_MB * 1024 * 1024:
        raise HTTPException(413, f"Uploads are limited to {MAX_MB} MB.")
    name = Path(file.filename or "upload.csv").name[:120]
    sha = hashlib.sha256(raw).hexdigest()
    with tx() as c:
        dup = c.execute("SELECT id, version FROM dataset_versions WHERE kind=? AND sha256=?", (kind, sha)).fetchone()
    if dup:
        raise HTTPException(409, f"This exact file is already registered as version {dup['version']} (record {dup['id']}).")
    try:
        df = read_frame(raw, name)
    except Exception as e:
        raise HTTPException(422, f"File could not be read: {type(e).__name__}. Use CSV, Parquet or GeoJSON points.")
    report = validate(df, kind, target, horizon)
    RAW.mkdir(parents=True, exist_ok=True)
    rawp = RAW / f"{sha[:16]}_{name}"
    rawp.write_bytes(raw)
    with tx() as c:
        ver = c.execute("SELECT COALESCE(MAX(version),0)+1 FROM dataset_versions WHERE kind=?", (kind,)).fetchone()[0]
        cur = c.execute("INSERT INTO dataset_versions (kind, version, label, filename, sha256, raw_path, n_rows, columns, report, status, provenance, target, horizon, "
                        "created_by, created_utc, demo) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (kind, ver, label[:200], name, sha, str(rawp), len(df), json.dumps([str(x) for x in df.columns]), json.dumps(report, default=str),
                         "validated" if report["passed"] else "failed_validation", provenance, target, horizon, u.email, now(), int(u.demo)))
        did = cur.lastrowid
    ops.audit(u.email, "dataset.upload", f"D{did} {kind} v{ver} {provenance} rows={len(df)} passed={report['passed']}")
    return {"id": did, "version": ver, "status": "validated" if report["passed"] else "failed_validation", "provenance": provenance, "report": report}


def _load_entomology(df: pd.DataFrame, did: int, provenance: str, demo: int) -> int:
    latc = next((c for c in ("lat", "latitude") if c in df), None)
    lonc = next((c for c in ("lon", "longitude") if c in df), None)
    g = lambda r, k: None if k not in r or pd.isna(r[k]) else r[k]  # noqa: E731
    n = 0
    with tx() as c:
        for r in df.to_dict("records"):
            lat, lon, sid = g(r, latc) if latc else None, g(r, lonc) if lonc else None, g(r, "site_id") or g(r, "habitat_id")
            key = f"{sid}|{lat}|{lon}|{r['sample_date']}|{g(r, 'sampling_method')}|{g(r, 'collector')}"
            cur = c.execute("INSERT OR IGNORE INTO entomological_samples (source, dataset_version_id, site_id, lat, lon, sample_date, collector, sampling_method, dips, water_present, "
                            "larvae_present, anopheles_larvae_count, stage_early, stage_late, pupae_count, species, sample_type, resistance_finding, provenance, demo, dedupe_key) "
                            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            ("dataset", did, sid, lat, lon, str(r["sample_date"])[:10], g(r, "collector"), g(r, "sampling_method"), g(r, "dips"), g(r, "water_present"),
                             g(r, "larvae_present"), g(r, "anopheles_larvae_count"), g(r, "stage_early"), g(r, "stage_late"), g(r, "pupae_count"), g(r, "species"),
                             g(r, "sample_type") or "larval", g(r, "resistance_finding"), provenance, demo, key))
            n += cur.rowcount
        c.execute("UPDATE data_sources SET last_ingest_utc=? WHERE key='entomology'", (now(),))
    return n


@router.post("/datasets/{did}/confirm")
def confirm(did: int, u: User = Depends(require("manage:data"))):
    with tx() as c:
        r = c.execute("SELECT * FROM dataset_versions WHERE id=?", (did,)).fetchone()
    if not r:
        raise HTTPException(404, "Unknown dataset.")
    if r["status"] == "failed_validation":
        raise HTTPException(409, "This dataset failed validation and cannot be used. Correct the file and upload it again.")
    if r["status"] != "validated":
        raise HTTPException(409, f"Dataset is already {r['status']}.")
    spec = KINDS[r["kind"]]
    df = read_frame(Path(r["raw_path"]).read_bytes(), r["filename"])
    keys = [c for c in spec["keys"] if c in df.columns]
    before = len(df)
    df = df.drop_duplicates(subset=keys or None)
    req = [c for c in spec["required"] if c in df.columns]
    df = df.dropna(subset=req)
    if spec["date"] in df.columns:
        df[spec["date"]] = pd.to_datetime(df[spec["date"]], errors="coerce", utc=True).dt.tz_localize(None)
        df = df.dropna(subset=[spec["date"]])
    PROCESSED.mkdir(parents=True, exist_ok=True)
    proc = PROCESSED / f"{r['kind']}_v{r['version']:04d}.parquet"
    if r["kind"] == "entomology":
        df[spec["date"]] = df[spec["date"]].dt.strftime("%Y-%m-%d")
    df.to_parquet(proc, index=False)
    loaded = _load_entomology(df, did, r["provenance"], r["demo"]) if r["kind"] == "entomology" else None
    with tx() as c:
        c.execute("UPDATE dataset_versions SET status='confirmed', processed_path=?, n_rows=?, confirmed_by=?, confirmed_utc=? WHERE id=?",
                  (str(proc), len(df), u.email, now(), did))
    ops.audit(u.email, "dataset.confirm", f"D{did} kept {len(df)} of {before} rows")
    return {"id": did, "status": "confirmed", "rows": len(df), "dropped": before - len(df), "samples_loaded": loaded}


@router.get("/datasets/{did}")
def get_dataset(did: int, u: User = Depends(require("read:models"))):
    with tx() as c:
        r = c.execute("SELECT * FROM dataset_versions WHERE id=?", (did,)).fetchone()
    if not r:
        raise HTTPException(404, "Unknown dataset.")
    return _row(r)


__all__ = ["np"]

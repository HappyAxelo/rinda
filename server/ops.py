"""Operations record: persistence, flight-log analytics, programme economics.

Every number produced here from defaults is a planning estimate and is labelled
as such in the response. Measured values only ever come from uploaded logs.
"""
from __future__ import annotations

import io
import json
import sqlite3
import time
from pathlib import Path

import numpy as np
import pandas as pd

from engine import VAR

DB = VAR / "operations.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS missions (
  id TEXT PRIMARY KEY, created_utc TEXT, issue_date TEXT, horizon INTEGER,
  status TEXT, author TEXT, params TEXT, result TEXT);
CREATE TABLE IF NOT EXISTS flight_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, mission_id TEXT, uploaded_utc TEXT,
  filename TEXT, provenance TEXT, n_rows INTEGER, payload TEXT);
CREATE TABLE IF NOT EXISTS telemetry (
  id INTEGER PRIMARY KEY AUTOINCREMENT, station_id TEXT, ts_utc TEXT,
  received_utc TEXT, seq INTEGER, payload TEXT);
CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts_utc TEXT, actor TEXT, action TEXT, detail TEXT);
"""


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    return c


def audit(actor: str, action: str, detail: str = "") -> None:
    with conn() as c:
        c.execute("INSERT INTO audit (ts_utc, actor, action, detail) VALUES (?,?,?,?)",
                  (now(), actor, action, detail))


def audit_trail(limit: int = 200) -> list[dict]:
    with conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,))]


# ------------------------------------------------------------------ missions
def save_mission(mid: str, issue_date: str, h: int, author: str, params: dict, result: dict) -> None:
    with conn() as c:
        c.execute("INSERT OR REPLACE INTO missions VALUES (?,?,?,?,?,?,?,?)",
                  (mid, now(), issue_date, h, "PLANNED", author, json.dumps(params), json.dumps(result)))


def list_missions() -> list[dict]:
    with conn() as c:
        rows = c.execute("SELECT id, created_utc, issue_date, horizon, status, author, result "
                         "FROM missions ORDER BY created_utc DESC").fetchall()
    out = []
    for r in rows:
        res = json.loads(r["result"])
        out.append({k: r[k] for k in ("id", "created_utc", "issue_date", "horizon", "status", "author")}
                   | {"totals": res.get("totals", {})})
    return out


def get_mission(mid: str) -> dict | None:
    with conn() as c:
        r = c.execute("SELECT * FROM missions WHERE id=?", (mid,)).fetchone()
    if not r:
        return None
    return dict(r) | {"params": json.loads(r["params"]), "result": json.loads(r["result"])}


def set_status(mid: str, status: str) -> None:
    with conn() as c:
        c.execute("UPDATE missions SET status=? WHERE id=?", (status, mid))


# ---------------------------------------------------------------- flight logs
FLIGHT_LOG_COLUMNS = [
    ("mission_id", "Mission identifier from the plan file", "M-20260119-0001"),
    ("sortie_id", "Sortie within the mission", "S01"),
    ("target_id", "Forecast site identifier, blank for opportunistic detections", "G0221"),
    ("ts_utc", "Event time, ISO 8601", "2026-01-19T07:42:11.204Z"),
    ("lat", "Aircraft latitude at decision", "-1.847744"),
    ("lon", "Aircraft longitude at decision", "30.182585"),
    ("ground_speed_ms", "Ground speed at decision", "3.0"),
    ("altitude_m", "Height above ground", "6.0"),
    ("t_cap_ms", "Frame capture time", "33.1"),
    ("t_pre_ms", "Pre-processing time", "7.9"),
    ("t_inf_ms", "Inference time on the companion computer", "44.6"),
    ("t_dec_ms", "Decision time", "3.8"),
    ("t_act_ms", "Valve actuation time", "58.2"),
    ("deadline_ms", "d/v for this event", "1640"),
    ("confidence", "Verifier confidence for habitat present", "0.91"),
    ("decision", "SPRAY | ABSTAIN | NO_HABITAT", "SPRAY"),
    ("sprayed_ml", "Volume metered onto the patch", "820"),
    ("p_compute_w", "Companion computer power at decision", "24.6"),
    ("ground_truth", "Optional: 1 if entomology later confirmed habitat, 0 if not", "1"),
]


def flight_log_template() -> str:
    return ",".join(c for c, _, _ in FLIGHT_LOG_COLUMNS) + "\n" + \
        ",".join(e for _, _, e in FLIGHT_LOG_COLUMNS) + "\n"


def ingest_flight_log(raw: bytes, filename: str, provenance: str) -> dict:
    df = pd.read_csv(io.BytesIO(raw))
    need = [c for c, _, _ in FLIGHT_LOG_COLUMNS if c not in ("ground_truth", "target_id", "p_compute_w")]
    missing = [c for c in need if c not in df.columns]
    if missing:
        return {"ok": False, "error": f"missing columns: {', '.join(missing)}"}
    mid = str(df["mission_id"].iloc[0])
    with conn() as c:
        c.execute("INSERT INTO flight_logs (mission_id, uploaded_utc, filename, provenance, n_rows, payload) "
                  "VALUES (?,?,?,?,?,?)", (mid, now(), filename, provenance, len(df), df.to_json(orient="records")))
    return {"ok": True, "mission_id": mid, "rows": len(df)}


def flight_logs() -> list[dict]:
    with conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT id, mission_id, uploaded_utc, filename, provenance, n_rows FROM flight_logs ORDER BY id DESC")]


def delete_flight_log(log_id: int) -> None:
    with conn() as c:
        c.execute("DELETE FROM flight_logs WHERE id=?", (log_id,))


def analyse_flight_logs(log_id: int | None = None) -> dict:
    with conn() as c:
        q = "SELECT provenance, payload FROM flight_logs" + (" WHERE id=?" if log_id else "")
        rows = c.execute(q, (log_id,) if log_id else ()).fetchall()
    if not rows:
        return {"available": False}
    frames, prov = [], set()
    for r in rows:
        frames.append(pd.read_json(io.StringIO(r["payload"])))
        prov.add(r["provenance"])
    df = pd.concat(frames, ignore_index=True)
    stages = ["t_cap_ms", "t_pre_ms", "t_inf_ms", "t_dec_ms", "t_act_ms"]
    df["L_ms"] = df[stages].sum(axis=1)
    miss = df["L_ms"] > df["deadline_ms"]
    qs = [0.5, 0.9, 0.95, 0.99]
    lat = np.sort(df["L_ms"].to_numpy())
    cdf_x = np.quantile(lat, np.linspace(0, 1, 101)).tolist()
    out = {
        "available": True, "provenance": sorted(prov), "n_events": int(len(df)),
        "missions": sorted(df["mission_id"].astype(str).unique().tolist()),
        "latency": {f"p{int(q*100)}": float(np.quantile(lat, q)) for q in qs} | {"mean": float(lat.mean()),
                                                                              "max": float(lat.max())},
        "deadline_miss_ratio": float(miss.mean()),
        "stage_means": [{"stage": s.replace("t_", "").replace("_ms", ""), "ms": float(df[s].mean()),
                         "p99": float(df[s].quantile(0.99))} for s in stages],
        "cdf": [{"q": i / 100, "ms": v} for i, v in enumerate(cdf_x)],
        "decisions": df["decision"].value_counts().to_dict(),
        "sprayed_l": float(df.get("sprayed_ml", pd.Series(dtype=float)).sum() / 1000),
        "by_speed": [{"speed": float(k), "n": int(len(g)), "p50": float(g.L_ms.median()),
                      "p99": float(g.L_ms.quantile(0.99)),
                      "dmr": float((g.L_ms > g.deadline_ms).mean())}
                     for k, g in df.groupby(df["ground_speed_ms"].round(1))],
        "events": json.loads(df.head(400).to_json(orient="records")),
    }
    if "p_compute_w" in df:
        out["compute_w_mean"] = float(df["p_compute_w"].mean())
    if "ground_truth" in df and df["ground_truth"].notna().any():
        g = df.dropna(subset=["ground_truth"])
        pred = g["decision"].eq("SPRAY")
        y = g["ground_truth"].astype(int).eq(1)
        tp, fp = int((pred & y).sum()), int((pred & ~y).sum())
        fn, tn = int((~pred & y).sum()), int((~pred & ~y).sum())
        out["verification"] = {"tp": tp, "fp": fp, "fn": fn, "tn": tn,
                               "precision": tp / (tp + fp) if tp + fp else None,
                               "recall": tp / (tp + fn) if tp + fn else None,
                               "false_spray_rate": fp / (fp + tn) if fp + tn else None,
                               "missed_habitat_rate": fn / (fn + tp) if fn + tp else None}
    return out


def synthetic_flight_log(mission: dict, aircraft: dict, seed: int = 7) -> bytes:
    """Bench-test file shaped exactly like a real log, drawn from the design
    budget with heavy-tailed jitter. Stamped SYNTHETIC; it exercises the
    analytics and must never be cited as a measurement."""
    rng = np.random.default_rng(seed)
    rows = []
    a = aircraft
    for s in mission["result"]["sorties"]:
        for w in s["waypoints"]:
            for speed in (2.0, 3.0, 4.0, 5.0):
                fp = 2 * a["altitude_m"] * np.tan(np.radians(a["camera_vfov_deg"] / 2))
                d = fp / 2 + a["nozzle_offset_m"]
                cap = rng.normal(a["t_cap_ms"], 2)
                pre = rng.gamma(4, a["t_pre_ms"] / 4)
                inf = a["t_inf_ms"] * (1 + rng.lognormal(-2.2, 0.8))
                dec = rng.gamma(3, a["t_dec_ms"] / 3)
                act = rng.normal(a["t_act_ms"], 6)
                if rng.random() < 0.01:
                    inf += rng.uniform(200, 900)  # thermal throttle / GC pause
                conf = float(np.clip(rng.beta(6, 2) if w["action"] == "VERIFY_AND_TREAT" else rng.beta(2, 3), 0, 1))
                dec_s = "SPRAY" if conf >= 0.7 and w["action"] == "VERIFY_AND_TREAT" else (
                    "ABSTAIN" if conf >= 0.4 else "NO_HABITAT")
                rows.append({
                    "mission_id": mission["id"], "sortie_id": s["id"], "target_id": w["site_id"],
                    "ts_utc": now(), "lat": w["lat"], "lon": w["lon"], "ground_speed_ms": speed,
                    "altitude_m": a["altitude_m"], "t_cap_ms": round(cap, 2), "t_pre_ms": round(pre, 2),
                    "t_inf_ms": round(inf, 2), "t_dec_ms": round(dec, 2), "t_act_ms": round(act, 2),
                    "deadline_ms": round(1000 * d / speed, 1), "confidence": round(conf, 3),
                    "decision": dec_s, "sprayed_ml": 800 if dec_s == "SPRAY" else 0,
                    "p_compute_w": round(rng.normal(a["p_compute_w"], 1.5), 2),
                    "ground_truth": int(rng.random() < (0.85 if dec_s == "SPRAY" else 0.25)),
                })
    return pd.DataFrame(rows).to_csv(index=False).encode()


# ---------------------------------------------------------------- telemetry
def ingest_telemetry(station_id: str, payload: dict) -> dict:
    with conn() as c:
        c.execute("INSERT INTO telemetry (station_id, ts_utc, received_utc, seq, payload) VALUES (?,?,?,?,?)",
                  (station_id, payload.get("ts_utc", now()), now(), payload.get("seq"), json.dumps(payload)))
    return {"ok": True}


def telemetry_summary() -> list[dict]:
    with conn() as c:
        rows = c.execute("SELECT station_id, COUNT(*) n, MIN(seq) s0, MAX(seq) s1, MAX(received_utc) last "
                         "FROM telemetry GROUP BY station_id").fetchall()
    out = []
    for r in rows:
        expected = (r["s1"] - r["s0"] + 1) if r["s0"] is not None and r["s1"] is not None else None
        out.append({"station_id": r["station_id"], "packets": r["n"], "last_received_utc": r["last"],
                    "pdr": (r["n"] / expected) if expected else None})
    return out


# ---------------------------------------------------------------- economics
DEFAULT_ECON = {
    # every value is an editable programme input; none is a measured result
    "period_weeks": 12,
    "currency": "RWF",
    "ground_teams": 4,
    "ground_team_size": 4,
    "ground_daily_wage": 5000,
    "ground_ha_per_team_day": 8.0,
    "ground_detection_rate": 0.60,
    "ground_bti_kg_ha_of_water": 0.5,
    "bti_price_per_kg": 45000,
    "drone_cost_per_flight_hour": 60000,
    "pilot_crew_cost_per_day": 40000,
    "flight_hours_per_day": 4.0,
    "drones_available": 2,
    "two_pass_processing_days": 3.0,
    "processing_cost_per_day": 50000,
    "forecast_service_cost_per_week": 30000,
    "verification_detection_rate": 0.85,
    "water_fraction_of_area": 0.06,
    "habitat_lifetime_days": 10,
}


def economics(inp: dict, area_ha: float, targets_per_week: float, treated_ha_per_week: float,
              guided_flight_h_per_week: float, blanket_flight_h_per_week: float,
              spray_h_per_ha: float) -> dict:
    e = DEFAULT_ECON | (inp or {})
    w = e["period_weeks"]
    water_ha = area_ha * e["water_fraction_of_area"]
    bti = e["bti_price_per_kg"]

    # Manual ground LSM: teams walk the whole valley each week
    team_days = area_ha / e["ground_ha_per_team_day"]
    ground = {
        "label": "Manual ground LSM",
        "labour": team_days * e["ground_team_size"] * e["ground_daily_wage"] * w,
        "larvicide": water_ha * e["ground_detection_rate"] * e["ground_bti_kg_ha_of_water"] * bti * w,
        "aviation": 0.0, "processing": 0.0, "forecast": 0.0,
        "habitats_reached_share": e["ground_detection_rate"],
        "response_days": team_days / max(1, e["ground_teams"]),
        "bti_kg": water_ha * e["ground_detection_rate"] * e["ground_bti_kg_ha_of_water"] * w,
    }
    # Two-pass drone: map everything, process offline, spray the mapped water
    crew_days_tp = blanket_flight_h_per_week / e["flight_hours_per_day"]
    two_pass = {
        "label": "Two-pass drone (map, process, spray)",
        "labour": crew_days_tp * e["pilot_crew_cost_per_day"] * w,
        "larvicide": water_ha * e["ground_bti_kg_ha_of_water"] * bti * w,
        "aviation": (blanket_flight_h_per_week + water_ha * spray_h_per_ha) * e["drone_cost_per_flight_hour"] * w,
        "processing": e["two_pass_processing_days"] * e["processing_cost_per_day"] * w,
        "forecast": 0.0,
        "habitats_reached_share": 0.90,
        "response_days": e["two_pass_processing_days"] + crew_days_tp / max(1, e["drones_available"]),
        "bti_kg": water_ha * e["ground_bti_kg_ha_of_water"] * w,
    }
    # Forecast-guided single pass
    crew_days_g = guided_flight_h_per_week / e["flight_hours_per_day"]
    guided = {
        "label": "Forecast-guided single pass",
        "labour": max(crew_days_g, 0.25) * e["pilot_crew_cost_per_day"] * w,
        "larvicide": treated_ha_per_week * e["ground_bti_kg_ha_of_water"] * bti * w,
        "aviation": guided_flight_h_per_week * e["drone_cost_per_flight_hour"] * w,
        "processing": 0.0,
        "forecast": e["forecast_service_cost_per_week"] * w,
        "habitats_reached_share": e["verification_detection_rate"],
        "response_days": max(crew_days_g / max(1, e["drones_available"]), 0.25),
        "bti_kg": treated_ha_per_week * e["ground_bti_kg_ha_of_water"] * w,
    }
    out = []
    for s in (ground, two_pass, guided):
        s["total"] = s["labour"] + s["larvicide"] + s["aviation"] + s["processing"] + s["forecast"]
        s["cost_per_ha"] = s["total"] / (area_ha * w)
        s["within_window"] = s["response_days"] <= e["habitat_lifetime_days"]
        out.append(s)
    return {"inputs": e, "area_ha": area_ha, "water_ha": water_ha, "targets_per_week": targets_per_week,
            "strategies": out, "basis": "planning estimate from editable inputs; not a measured outcome"}

"""Data sources, connectors and the ingestion service.

Each connector is independent. A connector with no approved credentials or
agreement reports "not_configured" and fetches nothing: RINDA never simulates a
feed. Historical data and connector data are separate paths; historical files go
through datasets.py.

Records pushed through ingest() are validated, normalised, de-duplicated and
logged with provenance. Fetches run on a background thread so a slow source
cannot block the API.
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import date, datetime, timezone

import pandas as pd

from data import STATION, store
from db import now, tx
from engine import ROOT

IOT = json.loads((ROOT / "config" / "iot_nodes.json").read_text(encoding="utf-8"))
RW_BBOX = (-2.9, -1.0, 28.8, 30.95)
METEO_VARS = {"rainfall_mm": ("mm", 0, 500), "air_temp_c": ("degC", -10, 50), "humidity_pct": ("%", 0, 100),
              "wind_speed_ms": ("m/s", 0, 80), "wind_dir_deg": ("deg", 0, 360)}

SOURCES = [
    ("earth_observation", "Earth observation", "satellite", "Copernicus Sentinel-1 and Sentinel-2", 12,
     "Study archive of Sentinel-1 RTC and Sentinel-2 L2A. No live connection."),
    ("iot", "IoT environmental nodes", "sensor", "RINDA telemetry endpoint", 0.05,
     "Telemetry endpoint exists. Nodes count as commissioned only once a real key is set."),
    ("meteorology", "Meteorological data", "weather", "Gikomero automatic weather station; Meteo Rwanda (planned)", 1,
     "Daily station record in the study bundle. No Meteo Rwanda agreement yet."),
    ("entomology", "Entomological surveillance", "field", "RINDA field records; RBC surveys (requested)", 7,
     "Field observations recorded in RINDA, plus RBC survey files once supplied."),
    ("drone", "Drone mission records", "operations", "RINDA mission register and flight logs", 7,
     "Mission records held in the operations database."),
]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def seed_sources() -> None:
    with tx() as c:
        for key, name, domain, origin, days, notes in SOURCES:
            c.execute("INSERT OR IGNORE INTO data_sources (key, name, domain, mode, origin, expected_update_days, notes) "
                      "VALUES (?,?,?,?,?,?,?)", (key, name, domain, "historical", origin, days, notes))


# ---------------------------------------------------------------- normalise
def _ts(v) -> str | None:
    try:
        t = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    if t > utcnow():
        return None
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _gps_ok(lat, lon) -> bool:
    if lat is None or lon is None:
        return True  # position may be held in the node registry instead
    try:
        return RW_BBOX[0] <= float(lat) <= RW_BBOX[1] and RW_BBOX[2] <= float(lon) <= RW_BBOX[3]
    except (TypeError, ValueError):
        return False


def _insert_env(c, source: str, station: str, site, lat, lon, ts: str, var: str, value: float, unit: str,
                provenance: str, flag: str | None = None, dataset: int | None = None) -> bool:
    key = f"{source}|{station}|{ts}|{var}"
    cur = c.execute("INSERT OR IGNORE INTO environmental_observations (source_key, dataset_version_id, station_id, site_id, lat, lon, "
                    "observed_utc, received_utc, variable, value, unit, quality_flag, provenance, dedupe_key) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (source, dataset, station, site, lat, lon, ts, now(), var, value, unit, flag, provenance, key))
    return cur.rowcount == 1


def normalise_telemetry(station_id: str, body: dict) -> dict:
    """Readings from a commissioned node, kept exactly as received plus a range flag."""
    ts = _ts(body.get("ts_utc") or now())
    if not ts:
        return {"accepted": 0, "rejected": ["ts_utc"]}
    accepted, rejected = 0, []
    with tx() as c:
        for var, spec in IOT["variables"].items():
            if var not in body or body[var] is None:
                continue
            try:
                v = float(body[var])
            except (TypeError, ValueError):
                rejected.append(var)
                continue
            flag = None if spec["min"] <= v <= spec["max"] else "out_of_range"
            accepted += int(_insert_env(c, "iot", station_id, None, None, None, ts, var, v, spec["unit"], "SENSOR", flag))
        c.execute("UPDATE data_sources SET last_ingest_utc=?, last_observation=?, mode='connector' WHERE key='iot'", (now(), ts))
    return {"accepted": accepted, "rejected": rejected}


# ---------------------------------------------------------------- connectors
class Connector:
    key = ""
    name = ""
    env: tuple[str, ...] = ()
    needs = ""

    def configured(self) -> bool:
        return bool(self.env) and all(os.environ.get(e) for e in self.env)

    def state(self) -> str:
        return "configured" if self.configured() else "not_configured"

    def fetch(self, since: str | None) -> list[dict]:
        """Return raw records since a timestamp. Implemented when access is approved."""
        raise NotConfigured(self.needs)

    def describe(self) -> dict:
        return {"key": self.key, "name": self.name, "state": self.state(), "needs": self.needs, "env": list(self.env)}


class NotConfigured(Exception):
    pass


class Copernicus(Connector):
    key, name = "earth_observation", "Copernicus Sentinel-1 and Sentinel-2"
    env = ("RINDA_CDSE_CLIENT_ID", "RINDA_CDSE_CLIENT_SECRET")
    needs = "Approved Copernicus Data Space credentials, then a raster processing worker outside the web process."


class MeteoRwanda(Connector):
    key, name = "meteorology", "Meteo Rwanda"
    env = ("RINDA_METEO_URL", "RINDA_METEO_TOKEN")
    needs = "A data agreement and API details from Meteo Rwanda."


class IoTGateway(Connector):
    key, name = "iot", "LoRa gateway telemetry"
    needs = "Commissioned nodes with real keys in RINDA_STATION_KEYS. Nodes push to POST /api/v1/telemetry/{node_id}."

    def state(self) -> str:
        env = os.environ.get("RINDA_STATION_KEYS")
        if env and any(not str(k).startswith("replace-me") for k in json.loads(env).values()):
            return "configured"
        return "not_configured"


class Entomology(Connector):
    key, name = "entomology", "RBC entomological surveillance"
    env = ("RINDA_RBC_ENTO_URL", "RINDA_RBC_ENTO_TOKEN")
    needs = "RBC data-sharing agreement. Field officers can already record observations; RBC files can be uploaded as datasets."


class DroneRecords(Connector):
    key, name = "drone", "Drone mission records"
    needs = "Internal: missions and flight logs are written directly by RINDA."

    def state(self) -> str:
        return "internal"


CONNECTORS: dict[str, Connector] = {c.key: c for c in (Copernicus(), IoTGateway(), MeteoRwanda(), Entomology(), DroneRecords())}


def _log_run(source: str, status: str, message: str, actor: str, **n) -> int:
    with tx() as c:
        cur = c.execute("INSERT INTO ingestion_runs (source_key, started_utc, finished_utc, status, attempts, n_received, n_accepted, "
                        "n_duplicate, n_rejected, message, actor) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (source, n.get("started", now()), now(), status, n.get("attempts", 1), n.get("received", 0), n.get("accepted", 0),
                         n.get("duplicate", 0), n.get("rejected", 0), message, actor))
        return cur.lastrowid


def _validate_record(source: str, r: dict) -> tuple[str | None, str | None]:
    """Returns (error, flag)."""
    if not _ts(r.get("observed_utc")):
        return "bad or future timestamp", None
    if not _gps_ok(r.get("lat"), r.get("lon")):
        return "position outside Rwanda", None
    if not r.get("station_id") and not r.get("site_id"):
        return "no station or site", None
    if r.get("variable") is None or r.get("value") is None or not r.get("unit"):
        return "needs variable, value and unit", None
    if source == "meteorology" and r["variable"] in METEO_VARS:
        unit, lo, hi = METEO_VARS[r["variable"]]
        if r["unit"] != unit:
            return f"{r['variable']} must be in {unit}", None
        if not lo <= float(r["value"]) <= hi:
            return None, "out_of_range"
    return None, r.get("quality_flag")


def ingest(source: str, records: list[dict], actor: str, provenance: str) -> dict:
    """Validate, normalise, de-duplicate and log a batch of environmental records."""
    if source not in ("meteorology", "iot", "earth_observation"):
        raise ValueError("This source does not take environmental records.")
    accepted, duplicate, rejected, errors = 0, 0, 0, []
    last = None
    started = now()
    with tx() as c:
        for i, r in enumerate(records):
            err, flag = _validate_record(source, r)
            if err:
                rejected += 1
                errors.append({"row": i, "error": err})
                continue
            ts = _ts(r["observed_utc"])
            new = _insert_env(c, source, r.get("station_id") or r["site_id"], r.get("site_id"), r.get("lat"), r.get("lon"), ts,
                              r["variable"], float(r["value"]), r["unit"], provenance, flag)
            accepted += int(new)
            duplicate += int(not new)
            last = max(last or ts, ts)
        if accepted:
            c.execute("UPDATE data_sources SET last_ingest_utc=?, last_observation=?, mode='connector' WHERE key=?", (now(), last, source))
    status = "failed" if records and rejected == len(records) else "ok"
    rid = _log_run(source, status, f"{provenance}: {accepted} new, {duplicate} duplicate, {rejected} rejected", actor, started=started,
                   received=len(records), accepted=accepted, duplicate=duplicate, rejected=rejected)
    return {"run_id": rid, "accepted": accepted, "duplicate": duplicate, "rejected": rejected, "errors": errors[:50], "status": status}


_running: set[str] = set()
_lock = threading.Lock()


def run_connector(key: str, actor: str, retries: int = 2, wait_s: float = 2.0) -> dict:
    """Start a fetch on a background thread. An unconfigured connector is logged and does nothing."""
    con = CONNECTORS[key]
    if con.state() != "configured" or type(con).fetch is Connector.fetch:
        rid = _log_run(key, "unavailable", con.needs, actor)
        return {"run_id": rid, "status": "unavailable", "message": con.needs}
    with _lock:
        if key in _running:
            return {"status": "processing", "message": "A fetch from this source is already running."}
        _running.add(key)

    def job():
        started = now()
        try:
            for attempt in range(1, retries + 2):
                try:
                    with tx() as c:
                        since = c.execute("SELECT last_observation FROM data_sources WHERE key=?", (key,)).fetchone()[0]
                    ingest(key, con.fetch(since), actor, "CONNECTOR")
                    return
                except Exception as e:  # noqa: BLE001 - logged per attempt, then surfaced in the run log
                    if attempt > retries:
                        _log_run(key, "failed", type(e).__name__, actor, started=started, attempts=attempt)
                        return
                    time.sleep(wait_s * attempt)
        finally:
            with _lock:
                _running.discard(key)

    threading.Thread(target=job, daemon=True, name=f"ingest-{key}").start()
    return {"status": "processing", "message": "Fetch started."}


# ----------------------------------------------------------------- freshness
def _label(mode: str, last: str | None, expected: float | None, processing: bool = False) -> str:
    if processing:
        return "Processing"
    if not last:
        return "Unavailable"
    if mode == "historical":
        return "Historical"
    try:
        ts = datetime.fromisoformat(last.replace("Z", "+00:00"))
    except ValueError:
        return "Unavailable"
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    age = (utcnow() - ts).total_seconds() / 86400
    e = expected or 1
    return "Current" if age <= e * 1.5 else "Delayed" if age <= e * 4 else "Stale"


def freshness() -> list[dict]:
    st = store()
    with tx() as c:
        src = {r["key"]: dict(r) for r in c.execute("SELECT * FROM data_sources")}
        tel = c.execute("SELECT MAX(ts_utc) t, MAX(received_utc) r, COUNT(*) n FROM telemetry").fetchone()
        obs = c.execute("SELECT MAX(observed_at) t, MAX(created_utc) r, COUNT(*) n FROM observations").fetchone()
        smp = c.execute("SELECT MAX(sample_date) t, COUNT(*) n FROM entomological_samples").fetchone()
        ms = c.execute("SELECT MAX(created_utc) t, COUNT(*) n FROM missions").fetchone()
    computed = {
        "earth_observation": (st.s1_dates[-1], None, len(st.s1), "Sentinel-1 passes in the study bundle"),
        "iot": (tel["t"], tel["r"], tel["n"], "Packets received from field nodes"),
        "meteorology": (str(st.meteo.date.max().date()), None, len(st.meteo), "Daily station records"),
        "entomology": (max([x for x in (obs["t"], smp["t"]) if x], default=None), obs["r"], obs["n"] + smp["n"], "Observations and samples"),
        "drone": (ms["t"], ms["t"], ms["n"], "Missions in the register"),
    }
    out = []
    for key, name, *_ in SOURCES:
        s = src.get(key, {})
        last, ing, n, what = computed[key]
        con = CONNECTORS[key]
        live_mode = key in ("iot", "entomology", "drone")
        mode = "connector" if (s.get("mode") == "connector" or live_mode) and n else "historical"
        out.append({"key": key, "name": name, "mode": mode, "last_observation": last, "last_ingestion": s.get("last_ingest_utc") or ing,
                    "expected_update_days": s.get("expected_update_days"), "records": n, "records_are": what,
                    "availability": "available" if n else "none", "connector_state": con.state(), "connector_needs": con.needs,
                    "processing_status": "idle", "validation_status": s.get("validation_status") or ("not field-validated" if key == "earth_observation" else "see records"),
                    "origin": s.get("origin"), "notes": s.get("notes"),
                    "label": _label(mode, last, s.get("expected_update_days"))})
    return out


def node_status(last_rx: str | None, placeholder: bool) -> str:
    if not last_rx:
        return "Not yet commissioned" if placeholder else "Offline"
    t = datetime.fromisoformat(last_rx.replace("Z", "+00:00"))
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    mins = (utcnow() - t).total_seconds() / 60
    lim = IOT["status_minutes"]
    return "Online" if mins <= lim["online"] else "Delayed" if mins <= lim["delayed"] else "Offline"


__all__ = ["STATION", "date"]

"""Research alignment, Earth-observation inventory and efficiency measurements.

* The gap matrix and objective map come from config/research_alignment.json, which
  cites only studies in the proposal's reference list.
* The inventory is scene metadata from the satellite repository catalogues (2020
  onwards). RINDA holds site-level values for Sentinel-1 RTC and Sentinel-2; for
  everything else it holds metadata only, and says so.
* Efficiency figures are measured here, on this server. They are never presented
  as edge-device measurements.
"""
from __future__ import annotations

import csv
import json
import os
import platform
import statistics
import time
from collections import Counter, defaultdict

from fastapi import APIRouter, Depends

import ops
from db import tx
from engine import ENGINE, ROOT, V3
from security import User, require

router = APIRouter(tags=["research"])
CAT = ROOT / "study" / "catalogue"
ALIGN = ROOT / "config" / "research_alignment.json"

COLLECTIONS = {
    "sentinel-1-rtc": {"label": "Sentinel-1 RTC (VV/VH)", "kind": "Radar", "resolution_m": 10, "held": "Site-level values for 600 locations, used by the forecast",
                       "quality": "Orbit-harmonised for tracks 21 and 123. Sentinel-1C and 1D were corrected onto Sentinel-1A in the study on 13 November 2026; the served model still uses the uncorrected series.", "source": "Copernicus Sentinel data via Planetary Computer"},
    "sentinel-1-grd": {"label": "Sentinel-1 GRD (VV/VH)", "kind": "Radar", "resolution_m": 10, "held": "Scene metadata only. Originals were not downloaded.",
                       "quality": "Not processed in RINDA.", "source": "Copernicus Sentinel data via Planetary Computer"},
    "sentinel-2-l2a": {"label": "Sentinel-2 Level-2A", "kind": "Optical", "resolution_m": 10, "held": "Site-level indices (NDWI, MNDWI, NDVI) for 600 locations",
                       "quality": "Cloud cover recorded per scene. Indices are missing on cloudy dates.", "source": "Copernicus Sentinel data via Planetary Computer"},
    "landsat-c2-l2": {"label": "Landsat Collection 2 Level-2", "kind": "Optical", "resolution_m": 30, "held": "Scene metadata only. Not used by any model yet.",
                      "quality": "Cloud cover recorded per scene. One scene and one QA file failed to download.", "source": "USGS Landsat via Planetary Computer"},
    "copernicus-dem-glo30": {"label": "Copernicus DEM GLO-30", "kind": "Terrain", "resolution_m": 30, "held": "Terrain features (elevation, slope, height above drainage) in the forecast inputs",
                             "quality": "Static. Derived layers are in the satellite repository.", "source": "Copernicus DEM"},
    "nasadem": {"label": "NASADEM", "kind": "Terrain", "resolution_m": 30, "held": "Metadata only. Kept as a cross-check for the Copernicus DEM.", "quality": "Static.", "source": "NASA"},
    "jrc-gsw-v1.3": {"label": "JRC Global Surface Water v1.3", "kind": "Historical surface water", "resolution_m": 30, "held": "Occurrence and related layers as static forecast inputs",
                     "quality": "Landsat-based, 1984 to 2021. Misses small or vegetated water.", "source": "EC JRC / Google"},
    "esa-worldcover": {"label": "ESA WorldCover 2020 and 2021", "kind": "Land cover", "resolution_m": 10, "held": "Land-cover class as a static forecast input", "quality": "Static, two epochs.", "source": "ESA"},
}
STATIC = [("COP-DEM-GLO30-S02-E030", "copernicus-dem-glo30", "Copernicus DEM", "GLO-30 mosaic tile S02 E030"),
          ("NASADEM-HGT-s02e030", "nasadem", "NASADEM", "HGT tile s02e030"),
          ("JRC-GSW-v1.3-Gikomero", "jrc-gsw-v1.3", "Landsat 5/7/8 (derived)", "Occurrence, change, extent, recurrence, seasonality, transitions"),
          ("ESA-WorldCover-2020-Gikomero", "esa-worldcover", "Sentinel-1/2 (derived)", "WorldCover v100, 2020"),
          ("ESA-WorldCover-2021-Gikomero", "esa-worldcover", "Sentinel-1/2 (derived)", "WorldCover v200, 2021")]


def _f(v):
    try:
        return float(v) if v not in (None, "") else None
    except ValueError:
        return None


def _i(v):
    try:
        return int(float(v)) if v not in (None, "") else None
    except ValueError:
        return None


def seed_products() -> int:
    """Load scene metadata once. Reads the catalogues row by row to keep start-up memory flat."""
    with tx() as c:
        have = {r[0]: r[1] for r in c.execute("SELECT substr(storage_ref, 1, instr(storage_ref || '/', '/') - 1), COUNT(*) FROM raster_products GROUP BY 1")}
    sql = ("INSERT OR IGNORE INTO raster_products (product_id, source_key, platform, acquired_utc, acquired_date, processed_utc, resolution_m, cloud_pct, "
           "relative_orbit, orbit_state, processing_method, storage_ref, provenance, validation_status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)")

    def rows(name):
        p = CAT / name
        if not p.exists():
            return
        with p.open(newline="", encoding="utf-8") as fh:
            yield from csv.DictReader(fh)

    n = 0
    with tx() as c:
        if "planetarycomputer:sentinel-1-rtc" not in have:
            for r in rows("catalogue_sentinel-1-rtc.csv"):
                c.execute(sql, (r["item_id"], "earth_observation", r["platform"], r["datetime"], r["date"], r.get("s1:processing_datetime") or None, 10.0, None,
                                _i(r.get("sat:relative_orbit")), r.get("sat:orbit_state"), "Radiometric terrain correction (Planetary Computer sentinel-1-rtc)",
                                "planetarycomputer:sentinel-1-rtc", "catalogue", "not field-validated"))
                n += 1
        if "planetarycomputer:sentinel-1-grd" not in have:
            for r in rows("catalogue_sentinel-1-grd.csv"):
                c.execute(sql, (r["item_id"], "earth_observation", r["platform"], r["datetime"], r["date"], None, 10.0, None, _i(r.get("sat:relative_orbit")),
                                r.get("sat:orbit_state"), "Ground range detected, IW mode. Metadata only.", "planetarycomputer:sentinel-1-grd", "catalogue", "not processed"))
                n += 1
        if "planetarycomputer:sentinel-2-l2a" not in have:
            for r in rows("catalogue_sentinel-2-l2a.csv"):
                tail = r["item_id"].rsplit("_", 1)[-1]
                proc = f"{tail[:4]}-{tail[4:6]}-{tail[6:8]}T{tail[9:11]}:{tail[11:13]}:{tail[13:15]}Z" if len(tail) == 15 else None
                c.execute(sql, (r["item_id"], "earth_observation", r["platform"], r["datetime"], r["date"], proc, 10.0, _f(r.get("eo:cloud_cover")), _i(r.get("sat:relative_orbit")),
                                None, f"Level-2A surface reflectance, baseline {r.get('s2:processing_baseline')}", f"planetarycomputer:sentinel-2-l2a/{r.get('s2:mgrs_tile')}",
                                "catalogue", "not field-validated"))
                n += 1
        if "planetarycomputer:landsat-c2-l2" not in have:
            for r in rows("catalogue_landsat-c2-l2.csv"):
                c.execute(sql, (r["item_id"], "earth_observation", r["platform"], r["datetime"], r["date"], None, 30.0, _f(r.get("eo:cloud_cover")), None, None,
                                f"Collection 2 Level-2 ({r.get('landsat:correction')}), tier {r.get('landsat:collection_category')}",
                                f"planetarycomputer:landsat-c2-l2/{r.get('landsat:wrs_path')}{r.get('landsat:wrs_row')}", "catalogue", "not processed"))
                n += 1
        for pid, coll, plat, method in STATIC:
            c.execute(sql, (pid, "earth_observation", plat, None, None, None, float(COLLECTIONS[coll]["resolution_m"]), None, None, None, method,
                            f"repository:{coll}", "repository", "static reference layer"))
    return n


def _collection(ref: str) -> str:
    return ref.split(":", 1)[1].split("/")[0]


@router.get("/earth/inventory")
def inventory(u: User = Depends(require("read:forecast"))):
    """What Earth-observation data is catalogued, by collection and year, and what RINDA actually holds of it."""
    by: dict = defaultdict(lambda: {"n": 0, "years": Counter(), "first": None, "last": None, "clouds": [], "platforms": Counter()})
    with tx() as c:
        for r in c.execute("SELECT storage_ref, platform, acquired_date, cloud_pct FROM raster_products"):
            b = by[_collection(r["storage_ref"])]
            b["n"] += 1
            b["platforms"][r["platform"]] += 1
            d = r["acquired_date"]
            if d:
                b["years"][d[:4]] += 1
                b["first"] = min(b["first"] or d, d)
                b["last"] = max(b["last"] or d, d)
            if r["cloud_pct"] is not None:
                b["clouds"].append(r["cloud_pct"])
    out = []
    for key, meta in COLLECTIONS.items():
        b = by.get(key)
        if not b:
            out.append({"key": key, **meta, "scenes": None, "first": None, "last": None, "by_year": {}, "median_cloud_pct": None, "platforms": {}})
            continue
        out.append({"key": key, **meta, "scenes": b["n"], "first": b["first"], "last": b["last"], "by_year": dict(sorted(b["years"].items())),
                    "median_cloud_pct": round(statistics.median(b["clouds"]), 1) if b["clouds"] else None,
                    "clear_scenes": sum(x < 20 for x in b["clouds"]) if b["clouds"] else None, "platforms": dict(b["platforms"])})
    return {"collections": out, "period": "2020 onwards", "note": "Counts are catalogued scenes over the Gikomero working area. Rasters stay in the satellite repository; RINDA stores metadata and site-level values.",
            "weather": {"label": "Rainfall, temperature and humidity", "held": "Gikomero automatic weather station, daily, 22 March 2023 to 15 January 2026. Gridded CHIRPS and ERA5-Land are in the forecast inputs.",
                        "quality": "One station, no wind. Humidity is gated on completeness and never filled in."}}


@router.get("/research/alignment")
def alignment(u: User = Depends(require("read:forecast"))):
    return json.loads(ALIGN.read_text(encoding="utf-8"))


def _rss_mb() -> float | None:
    try:
        import psutil
        return round(psutil.Process().memory_info().rss / 1e6, 1)
    except Exception:
        try:
            with open("/proc/self/status") as fh:
                for line in fh:
                    if line.startswith("VmRSS"):
                        return round(int(line.split()[1]) / 1024, 1)
        except OSError:
            return None
    return None


def hardware() -> str:
    return f"{platform.system()} {platform.machine()}, {os.cpu_count()} logical cores"


@router.get("/models/efficiency")
def efficiency(u: User = Depends(require("read:models"))):
    """Measured cost of the forecast models on this server, and what is still unmeasured on the edge device."""
    served = []
    for h in (7, 14):
        m, tbl = ENGINE.models.get(h), ENGINE.tables.get(h)
        row = {"horizon": h, "model": f"{ENGINE.version}-h{h}", "n_features": len(m.fcols) if m else None}
        try:
            last = tbl.at_date(tbl.date.max()) if hasattr(tbl, "at_date") else tbl[tbl.date == tbl.date.max()]
            times = []
            for _ in range(5):
                t0 = time.perf_counter()
                m.prob(last)
                times.append(time.perf_counter() - t0)
            row |= {"rows_timed": int(len(last)), "issue_latency_ms": round(statistics.median(times) * 1000, 1),
                    "ms_per_1000_rows": round(statistics.median(times) * 1e6 / max(len(last), 1), 1)}
        except Exception as e:  # noqa: BLE001 - a failed timing is reported, not hidden
            row["error"] = type(e).__name__
        f = V3 / f"operational_h{h}.joblib"
        row["model_bytes"] = f.stat().st_size if f.exists() else None
        served.append(row)
    logs = ops.analyse_flight_logs()
    return {"served": served, "process_rss_mb": _rss_mb(), "hardware": hardware(),
            "scope": "Measured now on this server, one request at a time. This is the ground forecast service, not the drone.",
            "edge": {"status": "Not measured", "target": "NVIDIA Jetson Orin Nano 8 GB with an STM32F411 valve controller",
                     "applies_to": "The onboard water-segmentation model and the spray controller (Objective 3, RQ2 and RQ6). Neither exists yet.",
                     "required": ["99th-percentile full-pipeline latency", "FLOPs and inference-only latency, as the proxy measures H2 compares against", "Peak memory", "Power draw",
                                  "Deadline-miss ratio in flight"],
                     "where": "Flight Operations ingests the timing logs once instrumented flights exist.",
                     "flight_logs": {"available": bool(logs.get("available")), "provenance": logs.get("provenance", []),
                                     "note": "Only synthetic test logs are loaded. They say nothing about real latency." if logs.get("available") and set(logs.get("provenance", [])) <= {"SYNTHETIC"} else None}}}

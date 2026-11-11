"""Administrative geography from real boundary files, and point-in-polygon lookup.

Only the Gikomero pilot is onboarded. Higher levels exist so the hierarchy reads
correctly; they carry no geometry and claim no coverage.

Cell boundaries are not bundled: the only outlines to hand were GADM's, whose licence
forbids redistribution. Put NISR cell files in config/geo as cell_<name>.geojson (one
feature each, with a "cell" property) and cells appear everywhere without code changes.
"""
from __future__ import annotations

import json
from functools import lru_cache

from data import store
from db import tx
from engine import ROOT

GEO = ROOT / "config" / "geo"
SECTOR_CODE = "RW-KC-GAS-GIK"


def _load(name: str) -> dict:
    return json.loads((GEO / name).read_text(encoding="utf-8"))


def _in_ring(lat: float, lon: float, ring: list) -> bool:
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][:2]
        xj, yj = ring[j][:2]
        if ((yi > lat) != (yj > lat)) and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def _in_polygon(lat: float, lon: float, rings: list) -> bool:
    return _in_ring(lat, lon, rings[0]) and not any(_in_ring(lat, lon, r) for r in rings[1:])


def in_geometry(lat: float, lon: float, g: dict) -> bool:
    if g["type"] == "Polygon":
        return _in_polygon(lat, lon, g["coordinates"])
    if g["type"] == "MultiPolygon":
        return any(_in_polygon(lat, lon, p) for p in g["coordinates"])
    return False


@lru_cache(maxsize=1)
def boundaries() -> dict:
    sector = _load("sector_gikomero.geojson")
    cells = []
    for p in sorted(GEO.glob("cell_*.geojson")):
        f = _load(p.name)["features"][0]
        name = f["properties"]["cell"]
        cells.append({"type": "Feature", "geometry": f["geometry"],
                      "properties": {"code": f"{SECTOR_CODE}-{name.upper()[:3]}", "name": name, "level": "cell",
                                     "source": f["properties"].get("source"), "licence": f["properties"].get("licence"),
                                     "note": f["properties"].get("note")}})
    props = sector["features"][0]["properties"]
    sec = {"type": "Feature", "geometry": sector["features"][0]["geometry"],
           "properties": {"code": SECTOR_CODE, "name": "Gikomero", "level": "sector", "source": props.get("source"),
                          "licence": props.get("licence"), "area_km2": props.get("area_km2")}}
    marsh = _load("marshland_interpreted_preliminary.geojson")
    return {"sector": sec, "cells": cells, "marshland": marsh,
            "marshland_note": "Interpreted outline, preliminary. Not an official marshland boundary."}


@lru_cache(maxsize=1)
def site_areas() -> dict[str, dict]:
    """Which sector and cell each monitoring point falls in (computed, cached)."""
    b = boundaries()
    out = {}
    for r in store().sites.itertuples():
        inside = in_geometry(r.latitude, r.longitude, b["sector"]["geometry"])
        cell = next((c["properties"]["name"] for c in b["cells"]
                     if in_geometry(r.latitude, r.longitude, c["geometry"])), None)
        out[r.site_id] = {"in_sector": inside, "cell": cell if inside else None}
    return out


def area_of(lat: float | None, lon: float | None) -> dict:
    """Area of an arbitrary position, such as a field-registered habitat."""
    if lat is None or lon is None:
        return {"in_sector": None, "cell": None}
    b = boundaries()
    inside = in_geometry(lat, lon, b["sector"]["geometry"])
    cell = next((c["properties"]["name"] for c in b["cells"] if in_geometry(lat, lon, c["geometry"])), None)
    return {"in_sector": inside, "cell": cell if inside else None}


def hierarchy() -> list[dict]:
    b = boundaries()
    sa = site_areas()
    n_sector = sum(1 for v in sa.values() if v["in_sector"])
    nodes = [
        {"code": "RW", "level": "country", "name": "Rwanda", "parent": None, "onboarded": False},
        {"code": "RW-KC", "level": "province", "name": "Kigali City", "parent": "RW", "onboarded": False},
        {"code": "RW-KC-GAS", "level": "district", "name": "Gasabo", "parent": "RW-KC", "onboarded": False},
        {"code": SECTOR_CODE, "level": "sector", "name": "Gikomero", "parent": "RW-KC-GAS", "onboarded": True,
         "n_sites": n_sector},
    ]
    for c in b["cells"]:
        p = c["properties"]
        nodes.append({"code": p["code"], "level": "cell", "name": p["name"], "parent": SECTOR_CODE, "onboarded": True,
                      "n_sites": sum(1 for v in sa.values() if v["cell"] == p["name"])})
    return nodes


def seed_areas() -> None:
    with tx() as c:
        for n in hierarchy():
            c.execute("INSERT OR REPLACE INTO administrative_areas (code, level, name, parent, onboarded, geometry_ref, source) "
                      "VALUES (?,?,?,?,?,?,?)",
                      (n["code"], n["level"], n["name"], n["parent"], int(n["onboarded"]),
                       "config/geo" if n["onboarded"] else None, "config/geo boundary files" if n["onboarded"] else "name only"))

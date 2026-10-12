"""Mission planning: forecast targets -> sorties -> flight-controller files.

Implements the three design models of the Forecast-to-Flight architecture:

    (1)  L = t_cap + t_pre + t_inf + t_dec + t_act  <=  d / v
    (2)  T = D_route / v + N * t_verify
    (3)  E_ha = (P_flight + P_compute + P_spray) * 10 000 / (v * w)

Targets the forecast marks "uncertain" (conformal set {0,1}) are flown as
VERIFY-ONLY: the aircraft inspects them but the spray channel stays locked
unless the onboard verifier independently clears them. Nothing is sprayed blind.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from data import STATION, haversine


@dataclass
class Aircraft:
    name: str = "Multirotor sprayer, 10 L class"
    cruise_speed_ms: float = 8.0          # transit between targets
    work_speed_ms: float = 3.0            # over target, verify + spray
    altitude_m: float = 6.0
    swath_m: float = 4.0
    endurance_min: float = 16.0           # loaded, to 20 % reserve
    reserve_frac: float = 0.20
    tank_l: float = 10.0
    takeoff_land_s: float = 90.0
    verify_s: float = 6.0                 # hover-and-confirm per target
    p_flight_w: float = 2200.0
    p_compute_w: float = 25.0
    p_spray_w: float = 60.0
    battery_wh: float = 1300.0
    camera_vfov_deg: float = 48.0
    nozzle_offset_m: float = 0.8
    # onboard loop stage budget (ms) -- design values until flight logs exist
    t_cap_ms: float = 33.0
    t_pre_ms: float = 8.0
    t_inf_ms: float = 45.0
    t_dec_ms: float = 4.0
    t_act_ms: float = 60.0


@dataclass
class Treatment:
    patch_area_m2: float = 400.0          # treated area per confirmed target
    spray_volume_l_ha: float = 20.0       # carrier volume
    bti_kg_ha: float = 0.5                # product rate (label rate to confirm with RBC)


def timing_budget(a: Aircraft) -> dict:
    footprint = 2 * a.altitude_m * math.tan(math.radians(a.camera_vfov_deg / 2))
    # a patch first seen at the leading edge of the frame must reach the nozzle
    # line, which sits nozzle_offset_m aft of the camera axis
    d = footprint / 2 + a.nozzle_offset_m
    deadline_ms = 1000 * d / a.work_speed_ms if a.work_speed_ms > 0 else float("inf")
    L = a.t_cap_ms + a.t_pre_ms + a.t_inf_ms + a.t_dec_ms + a.t_act_ms
    stages = [("Capture", a.t_cap_ms), ("Pre-process", a.t_pre_ms), ("Inference", a.t_inf_ms),
              ("Decision", a.t_dec_ms), ("Actuation", a.t_act_ms)]
    # speed at which the loop exactly meets its deadline
    v_max = d / (L / 1000) if L > 0 else float("inf")
    return {"footprint_m": footprint, "actionable_distance_m": d, "deadline_ms": deadline_ms,
            "latency_ms": L, "slack_ms": deadline_ms - L, "meets_deadline": L <= deadline_ms,
            "max_safe_speed_ms": v_max,
            "stages": [{"stage": s, "ms": t} for s, t in stages]}


def energy_per_ha(a: Aircraft) -> dict:
    p = a.p_flight_w + a.p_compute_w + a.p_spray_w
    t_ha = 10_000 / (a.work_speed_ms * a.swath_m)
    e_j = p * t_ha
    return {"seconds_per_ha": t_ha, "joules_per_ha": e_j, "wh_per_ha": e_j / 3600,
            "share_compute": a.p_compute_w / p, "share_flight": a.p_flight_w / p,
            "share_spray": a.p_spray_w / p}


# ---------------------------------------------------------------- routing
def _dist_matrix(pts: np.ndarray) -> np.ndarray:
    lat, lon = pts[:, 0][:, None], pts[:, 1][:, None]
    return haversine(lat, lon, lat.T, lon.T)


def _route_len(order: list[int], D: np.ndarray) -> float:
    return float(sum(D[order[i], order[i + 1]] for i in range(len(order) - 1)))


def _two_opt(order: list[int], D: np.ndarray) -> list[int]:
    """Closed tour, node 0 fixed as the launch point."""
    best = order[:]
    improved = True
    while improved:
        improved = False
        for i in range(1, len(best) - 2):
            for j in range(i + 1, len(best) - 1):
                a, b, c, d = best[i - 1], best[i], best[j], best[j + 1]
                if D[a, c] + D[b, d] < D[a, b] + D[c, d] - 1e-6:
                    best[i:j + 1] = reversed(best[i:j + 1])
                    improved = True
    return best


def _tour(pts: np.ndarray) -> tuple[list[int], float]:
    """Nearest-neighbour seed then 2-opt. pts[0] is the launch point."""
    D = _dist_matrix(pts)
    n = len(pts)
    left, order = set(range(1, n)), [0]
    while left:
        nxt = min(left, key=lambda j: D[order[-1], j])
        order.append(nxt)
        left.remove(nxt)
    order.append(0)
    order = _two_opt(order, D)
    return order, _route_len(order, D)


def _launch_zones(tg: pd.DataFrame, radius_m: float) -> np.ndarray:
    """Complete-linkage clusters whose diameter stays under 2 x radius, so every
    target in a zone is within roughly radius_m of its launch point."""
    if len(tg) == 1:
        return np.array([1])
    from scipy.cluster.hierarchy import fcluster, linkage
    from scipy.spatial.distance import squareform
    D = _dist_matrix(tg[["latitude", "longitude"]].to_numpy())
    Z = linkage(squareform(D, checks=False), method="complete")
    return fcluster(Z, t=2 * radius_m, criterion="distance")


def plan(targets: pd.DataFrame, a: Aircraft, t: Treatment, base: tuple[float, float],
         launch: str = "mobile", zone_radius_m: float = 900.0, road_factor: float = 1.4,
         vehicle_kmh: float = 25.0) -> dict:
    """Partition targets into sorties that respect endurance (net of reserve)
    and tank volume, then route each with nearest-neighbour + 2-opt.

    launch="fixed": every sortie starts at the base.
    launch="mobile": the ground crew drives to a launch point at the centroid of
    each zone of nearby targets, which is standard practice for spray drones whose
    endurance rarely covers a sector from one spot.
    """
    if targets.empty:
        return {"sorties": [], "zones": [], "totals": {}, "base": list(base), "launch": launch}
    tg = targets.copy()
    tg["zone"] = _launch_zones(tg, zone_radius_m) if launch == "mobile" else 1
    usable_s = a.endurance_min * 60
    spray_l = t.spray_volume_l_ha * t.patch_area_m2 / 10_000
    work_s = (t.patch_area_m2 / a.swath_m) / a.work_speed_ms

    def sortie_cost(rows: pd.DataFrame, lp) -> dict:
        pts = np.vstack([[lp[0], lp[1]], rows[["latitude", "longitude"]].to_numpy()])
        order, dist = _tour(pts)
        n_treat = int((rows.action == "VERIFY_AND_TREAT").sum())
        flight_s = a.takeoff_land_s + dist / a.cruise_speed_ms + len(rows) * a.verify_s + n_treat * work_s
        return {"order": order, "distance_m": dist, "flight_s": flight_s,
                "spray_l": n_treat * spray_l, "n_treat": n_treat}

    zones = []
    for z, g in tg.groupby("zone"):
        lp = (float(g.latitude.mean()), float(g.longitude.mean())) if launch == "mobile" else tuple(base)
        if launch == "mobile" and len(g) == 1:
            # never launch on top of the habitat: stand off 80 m towards the base
            dlat, dlon = base[0] - lp[0], base[1] - lp[1]
            k = 80 / max(float(haversine(lp[0], lp[1], base[0], base[1])), 80)
            lp = (lp[0] + dlat * k, lp[1] + dlon * k)
        zones.append({"zone": int(z), "launch": lp, "rows": g})
    if len(zones) > 1:
        pts = np.vstack([[base[0], base[1]]] + [[z["launch"][0], z["launch"][1]] for z in zones])
        order, _ = _tour(pts)
        zones = [zones[k - 1] for k in order[1:-1]]

    sorties, zone_out = [], []
    prev = tuple(base)
    drive_km = 0.0
    for zi, z in enumerate(zones, 1):
        lp, g = z["launch"], z["rows"]
        leg = float(haversine(prev[0], prev[1], lp[0], lp[1])) * road_factor / 1000
        drive_km += leg
        prev = lp
        g = g.assign(bearing=np.degrees(np.arctan2(g.longitude - lp[1], g.latitude - lp[0])) % 360)
        g = g.sort_values("bearing")
        groups, cur = [], []
        for _, r in g.iterrows():
            c = sortie_cost(pd.DataFrame(cur + [r]), lp)
            if cur and (c["flight_s"] > usable_s or c["spray_l"] > a.tank_l):
                groups.append(pd.DataFrame(cur))
                cur = [r]
            else:
                cur.append(r)
        if cur:
            groups.append(pd.DataFrame(cur))
        ids = []
        for rows in groups:
            sorties.append((rows, lp, zi))
            ids.append(f"S{len(sorties):02d}")
        zone_out.append({"id": f"Z{zi:02d}", "launch": list(lp), "sorties": ids,
                         "n_targets": int(len(g)), "drive_km": leg})
    if launch == "mobile":
        drive_km += float(haversine(prev[0], prev[1], base[0], base[1])) * road_factor / 1000

    out = []
    p_tot = a.p_flight_w + a.p_compute_w
    for i, (rows, lp, zi) in enumerate(sorties, 1):
        c = sortie_cost(rows, lp)
        seq = [rows.iloc[k - 1] for k in c["order"][1:-1]]
        wps = [{"seq": n + 1, "site_id": r.site_id, "lat": float(r.latitude), "lon": float(r.longitude),
                "action": r.action, "probability": float(r.probability),
                "decision": r.conformal_decision, "priority": float(r.priority_score)}
               for n, r in enumerate(seq)]
        spray_time = c["n_treat"] * work_s
        energy_wh = (p_tot * c["flight_s"] + a.p_spray_w * spray_time) / 3600
        out.append({"id": f"S{i:02d}", "zone": f"Z{zi:02d}", "launch": list(lp), "waypoints": wps,
                    "distance_m": c["distance_m"], "flight_s": c["flight_s"], "spray_l": c["spray_l"],
                    "n_targets": len(wps), "n_treat": c["n_treat"], "n_verify_only": len(wps) - c["n_treat"],
                    "energy_wh": energy_wh, "battery_pct": 100 * energy_wh / a.battery_wh,
                    "endurance_used_pct": 100 * c["flight_s"] / (a.endurance_min * 60),
                    "feasible": c["flight_s"] <= usable_s})
    flight_min = sum(s["flight_s"] for s in out) / 60
    drive_min = drive_km / vehicle_kmh * 60 if launch == "mobile" else 0.0
    turnaround_min = 6.0 * max(0, len(out) - len(zones))
    totals = {
        "sorties": len(out), "zones": len(zones), "targets": int(sum(s["n_targets"] for s in out)),
        "treat": int(sum(s["n_treat"] for s in out)),
        "verify_only": int(sum(s["n_verify_only"] for s in out)),
        "distance_km": sum(s["distance_m"] for s in out) / 1000,
        "flight_min": flight_min, "drive_km": drive_km, "drive_min": drive_min,
        "field_day_min": flight_min + drive_min + turnaround_min + 10.0 * len(zones),
        "spray_l": sum(s["spray_l"] for s in out),
        "bti_kg": sum(s["n_treat"] for s in out) * t.patch_area_m2 / 10_000 * t.bti_kg_ha,
        "treated_ha": sum(s["n_treat"] for s in out) * t.patch_area_m2 / 10_000,
        "energy_wh": sum(s["energy_wh"] for s in out),
        "batteries": int(sum(math.ceil(max(s["battery_pct"], 1) / 80) for s in out)),
        "infeasible_sorties": int(sum(not s["feasible"] for s in out)),
    }
    return {"sorties": out, "zones": zone_out, "totals": totals, "base": list(base), "launch": launch}


def lawnmower(area_ha: float, a: Aircraft, t: Treatment) -> dict:
    """Full-area coverage of the same valley at the same swath: the two-pass
    and blanket baselines are both bounded below by this flight."""
    path_m = area_ha * 10_000 / a.swath_m
    flight_s = path_m / a.work_speed_ms
    sorties = math.ceil(flight_s / (a.endurance_min * 60))
    flight_s += sorties * a.takeoff_land_s
    p = a.p_flight_w + a.p_compute_w + a.p_spray_w
    return {"area_ha": area_ha, "path_km": path_m / 1000, "flight_min": flight_s / 60,
            "sorties": sorties, "energy_wh": p * flight_s / 3600,
            "spray_l_blanket": area_ha * t.spray_volume_l_ha, "bti_kg_blanket": area_ha * t.bti_kg_ha}


def hull_area_ha(lat: np.ndarray, lon: np.ndarray) -> float:
    """Convex-hull area of the monitored sites (equirectangular, fine at sector scale)."""
    from scipy.spatial import ConvexHull
    lat0 = np.radians(lat.mean())
    xy = np.c_[np.radians(lon) * 6371000 * math.cos(lat0), np.radians(lat) * 6371000]
    return float(ConvexHull(xy).volume / 10_000)


# ------------------------------------------------------------------ exports
def to_qgc_plan(mission: dict, a: Aircraft) -> dict:
    """QGroundControl .plan (JSON). Each target is a timed loiter; the companion
    computer owns the spray channel, so the file carries no spray commands."""
    s = mission["sortie"]
    base = s.get("launch") or mission["base"]
    items, seq = [], 1

    def item(cmd, lat, lon, alt, params):
        nonlocal seq
        it = {"type": "SimpleItem", "autoContinue": True, "command": cmd, "doJumpId": seq,
              "frame": 3, "params": params + [lat, lon, alt], "AMSLAltAboveTerrain": None,
              "Altitude": alt, "AltitudeMode": 1}
        seq += 1
        return it

    items.append(item(22, base[0], base[1], a.altitude_m, [0, 0, 0, None]))
    items.append({"type": "SimpleItem", "autoContinue": True, "command": 178, "doJumpId": seq,
                  "frame": 2, "params": [1, a.cruise_speed_ms, -1, 0, 0, 0, 0]})
    seq += 1
    for w in s["waypoints"]:
        items.append(item(19, w["lat"], w["lon"], a.altitude_m, [a.verify_s, 0, 0, None]))
    items.append({"type": "SimpleItem", "autoContinue": True, "command": 20, "doJumpId": seq,
                  "frame": 2, "params": [0, 0, 0, 0, 0, 0, 0]})
    return {
        "fileType": "Plan", "groundStation": "QGroundControl", "version": 1,
        "geoFence": {"circles": [], "polygons": [], "version": 2},
        "rallyPoints": {"points": [], "version": 2},
        "mission": {"cruiseSpeed": a.cruise_speed_ms, "hoverSpeed": a.work_speed_ms,
                    "firmwareType": 12, "vehicleType": 2, "globalPlanAltitudeMode": 1,
                    "plannedHomePosition": [base[0], base[1], 0], "version": 2, "items": items},
    }


def to_wpl(mission: dict, a: Aircraft) -> str:
    """MAVLink waypoint list, QGC WPL 110 text format."""
    s = mission["sortie"]
    base = s.get("launch") or mission["base"]
    rows = ["QGC WPL 110",
            f"0\t1\t0\t16\t0\t0\t0\t0\t{base[0]:.8f}\t{base[1]:.8f}\t0.000000\t1",
            f"1\t0\t3\t22\t0\t0\t0\t0\t{base[0]:.8f}\t{base[1]:.8f}\t{a.altitude_m:.6f}\t1"]
    n = 2
    for w in s["waypoints"]:
        rows.append(f"{n}\t0\t3\t19\t{a.verify_s:.1f}\t0\t0\t0\t{w['lat']:.8f}\t{w['lon']:.8f}\t{a.altitude_m:.6f}\t1")
        n += 1
    rows.append(f"{n}\t0\t2\t20\t0\t0\t0\t0\t0\t0\t0\t1")
    return "\n".join(rows) + "\n"


def to_geojson(mission: dict) -> dict:
    feats = []
    for s in mission["sorties"]:
        base = s.get("launch") or mission["base"]
        coords = [[base[1], base[0]]] + [[w["lon"], w["lat"]] for w in s["waypoints"]] + [[base[1], base[0]]]
        feats.append({"type": "Feature", "geometry": {"type": "LineString", "coordinates": coords},
                      "properties": {"sortie": s["id"], "distance_m": round(s["distance_m"], 1),
                                     "flight_s": round(s["flight_s"], 1)}})
        for w in s["waypoints"]:
            feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [w["lon"], w["lat"]]},
                          "properties": {"sortie": s["id"], **w}})
    return {"type": "FeatureCollection", "features": feats}


def defaults() -> dict:
    return {"aircraft": asdict(Aircraft()), "treatment": asdict(Treatment()),
            "base": [STATION["lat"], STATION["lon"]]}

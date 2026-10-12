"""Source loaders. Each returns a tidy frame or raises loudly.

No loader ever imputes across a quality gate, and no loader forward-fills
satellite observations. Missingness is carried forward as an explicit flag so
downstream models can see it.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import Config


class DataError(RuntimeError):
    pass


def _require(df: pd.DataFrame, cols: list[str], name: str) -> None:
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise DataError(f"{name}: missing required columns {missing}")


# ---------------------------------------------------------------- meteorology
def load_meteo(cfg: Config) -> pd.DataFrame:
    node = cfg["sources"]["meteo"]
    if not cfg.has("meteo"):
        raise DataError("meteo source absent")
    df = pd.read_csv(cfg.resolve(node["path"]), encoding="utf-8-sig")
    _require(df, node["required"], "meteo")
    df = df.rename(columns={node["date_col"]: "date", node["station_col"]: "station"})
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    df = df.sort_values(["station", "date"]).drop_duplicates(["station", "date"])

    # Humidity gate: incomplete days are biased, and 0.0% RH is implausible.
    gate = node.get("humidity_gate_col")
    df["humidity_available"] = 1
    if node.get("humidity_gate_required", True) and gate in df.columns:
        ok = df[gate].astype(str).str.lower().isin(["true", "1"])
        df.loc[~ok, "humidity_mean_pct"] = np.nan
        df["humidity_available"] = ok.astype(int)
    df.loc[df["humidity_mean_pct"] <= 0, "humidity_mean_pct"] = np.nan
    df.loc[df["humidity_mean_pct"] > 100, "humidity_mean_pct"] = np.nan

    # Reindex to a complete calendar so window features see real gaps as gaps.
    out = []
    for st, g in df.groupby("station", sort=False):
        full = pd.date_range(g["date"].min(), g["date"].max(), freq="D")
        g = g.set_index("date").reindex(full)
        g.index.name = "date"
        g["station"] = st
        for c in ("longitude", "latitude", "elevation_m"):
            if c in g.columns:
                g[c] = g[c].ffill().bfill()
        g["meteo_observed"] = g["rainfall_mm"].notna().astype(int)
        out.append(g.reset_index())
    return pd.concat(out, ignore_index=True)


# ------------------------------------------------------------------ Sentinel-1
def load_sentinel1(cfg: Config) -> pd.DataFrame:
    node = cfg["sources"]["sentinel1"]
    if not cfg.has("sentinel1"):
        raise DataError("sentinel1 source absent")
    df = pd.read_csv(cfg.resolve(node["path"]))
    _require(df, node["required"], "sentinel1")
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()

    # Orbit consistency: mixing passes makes geometry look like surface change.
    if node.get("orbit_pass"):
        df = df[df["pass"].str.upper() == node["orbit_pass"].upper()]
    if node.get("rel_orbit") is not None:
        df = df[df["rel_orbit"] == node["rel_orbit"]]
    if df.empty:
        raise DataError("sentinel1: orbit filter removed every observation")

    # ---- collapse same-day multi-track acquisitions -------------------------
    # Tracks 21 and 123 (say) can image the same site on the same day. Those are
    # two genuine looks at the same surface, so they are averaged rather than
    # discarded -- but averaged in LINEAR POWER, because dB is logarithmic and
    # averaging dB is arithmetically wrong.
    df["_vv_lin"] = 10 ** (df["vv_db"] / 10.0)
    df["_vh_lin"] = 10 ** (df["vh_db"] / 10.0)
    key = ["site_id", "date", "buffer_m"] if "buffer_m" in df.columns \
        else ["site_id", "date"]
    g = df.groupby(key, as_index=False).agg(
        _vv_lin=("_vv_lin", "mean"),
        _vh_lin=("_vh_lin", "mean"),
        n_valid_px=("n_valid_px", "sum"),
        n_looks=("_vv_lin", "size"),
        rel_orbit=("rel_orbit", "first"),
    )
    g["vv_db"] = 10.0 * np.log10(g["_vv_lin"].clip(lower=1e-12))
    g["vh_db"] = 10.0 * np.log10(g["_vh_lin"].clip(lower=1e-12))
    g = g.drop(columns=["_vv_lin", "_vh_lin"])

    # ---- pivot buffer radii into columns ------------------------------------
    # The multi-scale extraction exists because different predictors operate at
    # different spatial scales. Collapsing to one buffer would throw that away.
    if "buffer_m" in g.columns and g["buffer_m"].nunique() > 1:
        primary = node.get("primary_buffer_m")
        buffers = sorted(g["buffer_m"].unique())
        if primary not in buffers:
            primary = buffers[min(1, len(buffers) - 1)]   # default: 2nd smallest
        wide = g[g["buffer_m"] == primary].drop(columns=["buffer_m"]).copy()
        wide["primary_buffer_m"] = primary
        for b in buffers:
            if b == primary:
                continue
            side = (g[g["buffer_m"] == b][["site_id", "date", "vv_db", "vh_db"]]
                    .rename(columns={"vv_db": f"VV_DB_{b}M",
                                     "vh_db": f"VH_DB_{b}M"}))
            wide = wide.merge(side, on=["site_id", "date"], how="left")
        # scale contrast: a small bright patch inside a wet neighbourhood reads
        # differently from uniform inundation
        for b in buffers:
            if b != primary and f"VV_DB_{b}M" in wide.columns:
                wide[f"VV_SCALE_CONTRAST_{b}M"] = wide["vv_db"] - wide[f"VV_DB_{b}M"]
        g = wide

    g = g.sort_values(["site_id", "date"]).reset_index(drop=True)
    dup = g.duplicated(["site_id", "date"]).sum()
    if dup:
        raise DataError(f"sentinel1: {dup} rows still duplicate on (site_id, date) "
                        "after collapsing tracks and buffers")
    g["s1_observed"] = 1
    return g


# ------------------------------------------------------------------ Sentinel-2
def load_sentinel2(cfg: Config) -> pd.DataFrame:
    node = cfg["sources"]["sentinel2"]
    if not cfg.has("sentinel2"):
        raise DataError("sentinel2 source absent")
    df = pd.read_csv(cfg.resolve(node["path"]))
    _require(df, node["required"], "sentinel2")
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    if "cloud_frac" in df.columns:
        df = df[df["cloud_frac"] <= node.get("max_cloud_frac", 1.0)]
    df = df.sort_values(["site_id", "date"]).drop_duplicates(["site_id", "date"])
    df["s2_observed"] = 1
    return df.reset_index(drop=True)


# --------------------------------------------------------------------- terrain
def load_terrain(cfg: Config) -> pd.DataFrame:
    node = cfg["sources"]["terrain"]
    if not cfg.has("terrain"):
        raise DataError("terrain source absent")
    df = pd.read_csv(cfg.resolve(node["path"]))
    _require(df, node["required"], "terrain")
    return df.drop_duplicates("site_id").reset_index(drop=True)


# ------------------------------------------------------------------ entomology
def load_entomology(cfg: Config) -> pd.DataFrame:
    node = cfg["sources"]["entomology"]
    if not cfg.has("entomology"):
        raise DataError("entomology source absent -- Target C is not modellable")
    df = pd.read_csv(cfg.resolve(node["path"]))
    _require(df, node["required"], "entomology")
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    return df.sort_values(["site_id", "date"]).reset_index(drop=True)


# ----------------------------------------------------------------- NWP (later)
def load_nwp(cfg: Config) -> pd.DataFrame:
    node = cfg["sources"]["nwp_forecast"]
    if not cfg.has("nwp_forecast"):
        raise DataError("nwp_forecast absent -- system runs in hindcast mode only")
    df = pd.read_csv(cfg.resolve(node["path"]))
    _require(df, node["required"], "nwp_forecast")
    for c in ("date", "issue_date"):
        df[c] = pd.to_datetime(df[c]).dt.normalize()
    # A forecast is usable only if it was issued at or before the prediction date.
    df = df[df["issue_date"] <= df["date"]]
    return df.reset_index(drop=True)

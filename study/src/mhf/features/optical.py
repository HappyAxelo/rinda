"""Sentinel-2 optical features and terrain descriptors.

Optical observations are NEVER interpolated. Cloud gaps are represented by an
availability flag and an observation-age feature, so the model can learn that a
stale optical reading is less informative rather than being handed a fabricated
value. In the Kigali long rains fewer than one in ten daytime observations is
clear, so this matters.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import Config


def build_optical_features(s2: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    fc = cfg["features"]["optical"]
    if fc.get("interpolate", False):
        raise ValueError("optical interpolation is disallowed: it fabricates observations")

    idx = ["ndvi", "ndwi", "mndwi", "evi", "ndmi"]
    frames = []
    for sid, g in s2.sort_values(["site_id", "date"]).groupby("site_id", sort=False):
        g = g.set_index("date")
        f = pd.DataFrame(index=g.index)
        f["site_id"] = sid
        for c in idx:
            if c not in g.columns:
                continue
            up = c.upper()
            f[up] = g[c]
            for w in fc["windows_days"]:
                f[f"{up}_MEAN_{w}D"] = g[c].rolling(f"{w}D", closed="right",
                                                    min_periods=1).mean()
                f[f"{up}_DELTA_{w}D"] = g[c] - g[c].shift(1)
        # vegetation-water interaction: vegetated water is where larvae sit
        if {"ndvi", "mndwi"}.issubset(g.columns):
            f["VEG_WATER_INTERACTION"] = g["ndvi"] * g["mndwi"].clip(lower=0)
        f["S2_OBSERVED"] = 1
        f["S2_N_VALID_PX"] = g.get("n_valid_px", np.nan)
        frames.append(f.reset_index())

    out = pd.concat(frames, ignore_index=True)
    out.attrs["feature_group"] = "optical"
    return out


def attach_optical_to_dates(optical: pd.DataFrame, target_index: pd.DataFrame,
                            max_age_days: int = 14) -> pd.DataFrame:
    """As-of join: for each (site, date) take the most recent CLEAR optical
    observation at or before that date, and record how stale it is.

    Rows whose nearest clear observation is older than `max_age_days` keep the
    value but are flagged; nothing is invented.
    """
    left = target_index.sort_values("date")
    right = optical.sort_values("date")
    merged = pd.merge_asof(
        left, right, on="date", by="site_id", direction="backward",
        allow_exact_matches=True, suffixes=("", "_opt"),
    )
    obs_date = pd.merge_asof(
        left, right[["site_id", "date"]].assign(_obs=right["date"]),
        on="date", by="site_id", direction="backward",
    )["_obs"]
    merged["S2_AGE_DAYS"] = (merged["date"] - obs_date).dt.days
    merged["S2_AVAILABLE"] = ((merged["S2_AGE_DAYS"].notna())
                              & (merged["S2_AGE_DAYS"] <= max_age_days)).astype(int)
    return merged


# --------------------------------------------------------------------- terrain
TERRAIN_RATIONALE = {
    "elevation_m": "Temperature and vector distribution are strongly stratified by "
                   "elevation in Rwanda; risk rises below ~1700 m.",
    "slope_deg": "Standing water cannot persist on steep ground; low slope is a "
                 "necessary condition for a temporary habitat.",
    "aspect_deg": "Controls insolation and therefore evaporation rate and water "
                  "temperature, which set larval development speed.",
    "twi": "Topographic wetness index integrates upslope contributing area and "
           "local slope; it is the standard static predictor of where runoff "
           "accumulates.",
    "flow_accum": "Identifies convergence lines along which ephemeral pools form "
                  "after rainfall.",
    "dist_drainage_m": "Proximity to a drainage line raises the probability that "
                       "rainfall produces standing rather than routed water.",
    "dist_permanent_water_m": "Separates newly formed habitat from the margins of "
                              "permanent water bodies, which are a different class.",
    "ruggedness": "Rough terrain fragments potential water surfaces below the "
                  "sensor resolution and reduces detectability.",
}


def build_terrain_features(terrain: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    df = terrain.copy()
    if {"slope_deg"}.issubset(df.columns):
        df["LOW_SLOPE"] = (df["slope_deg"] <= 5).astype(int)
    if {"twi"}.issubset(df.columns):
        df["HIGH_TWI"] = (df["twi"] >= df["twi"].quantile(0.75)).astype(int)
    if {"aspect_deg"}.issubset(df.columns):
        rad = np.deg2rad(df["aspect_deg"])
        df["ASPECT_SIN"], df["ASPECT_COS"] = np.sin(rad), np.cos(rad)
        df = df.drop(columns=["aspect_deg"])
    df.attrs["feature_group"] = "terrain"
    df.attrs["rationale"] = TERRAIN_RATIONALE
    return df

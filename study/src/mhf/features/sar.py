"""Sentinel-1 water detection and temporal water-dynamics features.

Two things here are candidate scientific contributions rather than routine
feature engineering:

1. WATER-STATE TRANSITIONS. Habitat *formation* is a transition (dry -> wet),
   not a state. Features encode new / disappearing / expanding / stable water.

2. RAIN-TO-SAR RESPONSE. The change in backscatter conditioned on antecedent
   rainfall. If a location responds strongly to rain it is a depression that
   collects water; if it does not, it drains. This is a per-location hydrological
   response function learned from data, and it is the feature family most likely
   to carry early-warning signal.

Water thresholds are CALIBRATED (see calibrate_thresholds), never assumed.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import Config


# ------------------------------------------------------------- classification
def classify_water(s1: pd.DataFrame, vv_max: float, vh_max: float,
                   require_both: bool = True) -> pd.Series:
    vv_wet = s1["vv_db"] <= vv_max
    vh_wet = s1["vh_db"] <= vh_max
    wet = (vv_wet & vh_wet) if require_both else (vv_wet | vh_wet)
    return wet.astype(int)


def calibrate_thresholds(s1: pd.DataFrame, cfg: Config,
                         reference: pd.DataFrame | None = None,
                         train_mask: pd.Series | None = None) -> dict:
    """Grid-search VV/VH thresholds on the TRAIN window only.

    reference must carry a binary `ref_water` column aligned on (site_id, date).
    If no reference is available the prototype thresholds are returned unchanged
    and flagged as UNCALIBRATED, which the report then states explicitly.
    """
    wd = cfg["water_detection"]
    proto = {"vv_db_max": wd["prototype"]["vv_db_max"],
             "vh_db_max": wd["prototype"]["vh_db_max"],
             "calibrated": False, "reference": "none",
             "note": "prototype thresholds; NOT calibrated"}
    cal = wd.get("calibration", {})
    if not cal.get("enabled") or reference is None or reference.empty:
        return proto

    df = s1.merge(reference[["site_id", "date", "ref_water"]],
                  on=["site_id", "date"], how="inner")
    if train_mask is not None:
        df = df[train_mask.reindex(df.index, fill_value=False)]
    if df.empty or df["ref_water"].nunique() < 2:
        return proto

    best = (-1.0, proto["vv_db_max"], proto["vh_db_max"])
    for vv in cal["vv_grid"]:
        for vh in cal["vh_grid"]:
            pred = classify_water(df, vv, vh, cal.get("require_both", True))
            tp = int(((pred == 1) & (df["ref_water"] == 1)).sum())
            fp = int(((pred == 1) & (df["ref_water"] == 0)).sum())
            fn = int(((pred == 0) & (df["ref_water"] == 1)).sum())
            f1 = 0.0 if (2 * tp + fp + fn) == 0 else 2 * tp / (2 * tp + fp + fn)
            if f1 > best[0]:
                best = (f1, vv, vh)

    # A degenerate optimum (the grid edge with near-zero agreement) means the
    # reference carries no information about the SAR water state. Accepting it
    # would silently install an arbitrary threshold, so fall back and say so.
    min_f1 = cal.get("min_acceptable_f1", 0.05)
    if best[0] < min_f1 or best[1] in (min(cal["vv_grid"]), max(cal["vv_grid"])):
        proto["note"] = (
            f"calibration REJECTED (best F1={best[0]:.3f} at VV={best[1]}); "
            "reference and SAR water state do not agree, so prototype "
            "thresholds retained. Report this as uncalibrated.")
        proto["attempted_f1"] = round(best[0], 4)
        return proto

    return {"vv_db_max": best[1], "vh_db_max": best[2], "calibrated": True,
            "reference": cal.get("reference", "unknown"),
            "train_f1": round(best[0], 4),
            "note": "calibrated on the training window only"}


# ------------------------------------------------------------------- features
def build_sar_features(s1: pd.DataFrame, cfg: Config,
                       thresholds: dict,
                       meteo: pd.DataFrame | None = None) -> pd.DataFrame:
    """One row per (site_id, S1 acquisition date)."""
    fc = cfg["features"]["sar"]
    wd = cfg["water_detection"]
    df = s1.sort_values(["site_id", "date"]).copy()
    df["water"] = classify_water(df, thresholds["vv_db_max"],
                                 thresholds["vh_db_max"],
                                 wd["calibration"].get("require_both", True))
    df["vv_minus_vh"] = df["vv_db"] - df["vh_db"]
    # Ratio in linear power, since dB is logarithmic.
    vv_lin = 10 ** (df["vv_db"] / 10.0)
    vh_lin = 10 ** (df["vh_db"] / 10.0)
    df["vv_over_vh_linear"] = vv_lin / np.maximum(vh_lin, 1e-12)

    frames = []
    for sid, g in df.groupby("site_id", sort=False):
        g = g.sort_values("date").set_index("date")
        f = pd.DataFrame(index=g.index)
        f["site_id"] = sid
        f["VV_DB"] = g["vv_db"]
        f["VH_DB"] = g["vh_db"]
        f["VV_MINUS_VH"] = g["vv_minus_vh"]
        f["VV_OVER_VH"] = g["vv_over_vh_linear"]
        f["WATER"] = g["water"]

        # pass-to-pass change (the actual dynamics)
        f["VV_DELTA"] = g["vv_db"].diff()
        f["VH_DELTA"] = g["vh_db"].diff()
        f["DAYS_SINCE_PREV_PASS"] = g.index.to_series().diff().dt.days

        # ---- water frequency / persistence over trailing windows
        for w in fc["freq_windows_days"]:
            f[f"WATER_FREQ_{w}D"] = (g["water"].rolling(f"{w}D", closed="right",
                                                        min_periods=1).mean())
            f[f"VV_MIN_{w}D"] = g["vv_db"].rolling(f"{w}D", closed="right",
                                                   min_periods=1).min()
            f[f"WATER_COUNT_{w}D"] = (g["water"].rolling(f"{w}D", closed="right",
                                                         min_periods=1).sum())

        # consecutive wet passes
        w_arr = g["water"].to_numpy()
        persist = np.zeros(len(w_arr))
        run = 0
        for i, v in enumerate(w_arr):
            run = run + 1 if v == 1 else 0
            persist[i] = run
        f["WATER_PERSISTENCE_PASSES"] = persist

        # ---- transitions: the formation signal
        if fc.get("transitions", True):
            prev = g["water"].shift(1)
            f["WATER_NEW"] = ((prev == 0) & (g["water"] == 1)).astype(int)
            f["WATER_LOST"] = ((prev == 1) & (g["water"] == 0)).astype(int)
            f["WATER_STABLE_WET"] = ((prev == 1) & (g["water"] == 1)).astype(int)
            f["WATER_STABLE_DRY"] = ((prev == 0) & (g["water"] == 0)).astype(int)
            for w in fc["freq_windows_days"]:
                f[f"WATER_NEW_COUNT_{w}D"] = (f["WATER_NEW"]
                                              .rolling(f"{w}D", closed="right",
                                                       min_periods=1).sum())
            # expansion/contraction proxy from frequency slope
            f["WATER_EXPANSION"] = f["WATER_FREQ_7D"] - f["WATER_FREQ_30D"]

        # ---- backscatter variability (unstable surfaces = ephemeral water)
        vw = fc.get("variance_window_days", 30)
        f[f"VV_STD_{vw}D"] = g["vv_db"].rolling(f"{vw}D", closed="right",
                                                min_periods=2).std()
        f[f"VH_STD_{vw}D"] = g["vh_db"].rolling(f"{vw}D", closed="right",
                                                min_periods=2).std()

        # ---- multi-scale columns from the loader (VV_DB_10M, contrasts, ...)
        # Different predictors operate at different spatial scales, which is why
        # the export carries several buffer radii. Pass them through rather than
        # collapsing the study to a single arbitrary radius.
        passthrough = [c for c in g.columns
                       if c.startswith(("VV_DB_", "VH_DB_", "VV_SCALE_CONTRAST_"))]
        for c in passthrough:
            f[c] = g[c]
            if c.startswith("VV_DB_"):
                f[f"{c}_DELTA"] = g[c].diff()

        # observation quality, so the model can discount thin looks
        for c in ("n_valid_px", "n_looks"):
            if c in g.columns:
                f[c.upper()] = g[c]

        frames.append(f.reset_index())

    out = pd.concat(frames, ignore_index=True)

    # ---- rain-to-SAR response (candidate novel feature family)
    if fc.get("rain_response", True) and meteo is not None:
        out = _add_rain_response(out, meteo, fc)

    out.attrs["feature_group"] = "sar"
    return out


def _add_rain_response(sar: pd.DataFrame, meteo: pd.DataFrame,
                       fc: dict) -> pd.DataFrame:
    """Backscatter change per mm of antecedent rain, and its site-level slope.

    A depression that collects water shows a large negative VV_DELTA after rain;
    a well-drained slope does not. The per-site regression slope is a static
    descriptor of hydrological responsiveness, computed on the training window
    by the caller if strict separation is required.
    """
    m = meteo[["date", "RAIN_3D", "RAIN_7D"]].drop_duplicates("date")
    out = sar.merge(m, on="date", how="left")
    out["RAIN_RESPONSE_3D"] = out["VV_DELTA"] / np.where(
        out["RAIN_3D"].fillna(0) > 1.0, out["RAIN_3D"], np.nan)
    out["RAIN_RESPONSE_7D"] = out["VV_DELTA"] / np.where(
        out["RAIN_7D"].fillna(0) > 1.0, out["RAIN_7D"], np.nan)

    # per-site responsiveness: slope of VV_DELTA on RAIN_3D
    slopes = {}
    for sid, g in out.groupby("site_id"):
        d = g[["VV_DELTA", "RAIN_3D"]].dropna()
        if len(d) >= 8 and d["RAIN_3D"].std() > 0:
            slopes[sid] = float(np.polyfit(d["RAIN_3D"], d["VV_DELTA"], 1)[0])
        else:
            slopes[sid] = np.nan
    out["SITE_RAIN_RESPONSIVENESS"] = out["site_id"].map(slopes)
    return out.drop(columns=["RAIN_3D", "RAIN_7D"])


def sar_feature_columns(df: pd.DataFrame) -> list[str]:
    return [c for c in df.columns if c not in ("date", "site_id")]

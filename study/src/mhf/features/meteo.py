"""Meteorological / IoT temporal features.

Every window is BACKWARD-looking and closed on the right at t, so no feature
can see past the prediction date. This is enforced by using
`rolling(window, closed='right')` on a date-indexed frame and by the leakage
guard in mhf.assemble.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import Config

RAIN = "rainfall_mm"
TMEAN, TMIN, TMAX = "temp_mean_c", "temp_min_c", "temp_max_c"
RH = "humidity_mean_pct"


def _roll(s: pd.Series, w: int, how: str) -> pd.Series:
    r = s.rolling(f"{w}D", closed="right", min_periods=1)
    return getattr(r, how)()


def build_meteo_features(meteo: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    """One row per (station, date) with backward-looking windows and lags."""
    fc = cfg["features"]["meteo"]
    windows = fc["windows_days"]
    lags = fc["lags_days"]
    rain_thr = fc["rain_event_threshold_mm"]
    dry_thr = fc["dry_day_threshold_mm"]

    frames = []
    for st, g in meteo.groupby("station", sort=False):
        g = g.sort_values("date").set_index("date")
        f = pd.DataFrame(index=g.index)
        f["station"] = st
        rain = g[RAIN].astype(float)

        # ---- rainfall: totals, intensity, event counts, dry spells
        for w in windows:
            f[f"RAIN_{w}D"] = _roll(rain, w, "sum")
            f[f"CUM_RAIN_{w}D"] = f[f"RAIN_{w}D"]
            f[f"MAX_DAILY_RAIN_{w}D"] = _roll(rain, w, "max")
            wet = (rain >= rain_thr).astype(float)
            f[f"RAIN_EVENT_COUNT_{w}D"] = _roll(wet, w, "sum")
            dry = (rain < dry_thr).astype(float)
            f[f"DRY_DAYS_{w}D"] = _roll(dry, w, "sum")
            f[f"RAIN_INTENSITY_{w}D"] = np.where(
                f[f"RAIN_EVENT_COUNT_{w}D"] > 0,
                f[f"RAIN_{w}D"] / f[f"RAIN_EVENT_COUNT_{w}D"], 0.0)

        # days since last meaningful rain (censored at 60)
        since = np.full(len(rain), np.nan)
        counter = np.nan
        for i, v in enumerate(rain.to_numpy()):
            if np.isnan(v):
                counter = counter if not np.isnan(counter) else np.nan
            elif v >= rain_thr:
                counter = 0.0
            elif not np.isnan(counter):
                counter = min(counter + 1.0, 60.0)
            since[i] = counter
        f["DAYS_SINCE_RAIN"] = since

        # ---- temperature
        f["TEMP_CURRENT"] = g[TMEAN]
        for w in windows:
            f[f"TEMP_MEAN_{w}D"] = _roll(g[TMEAN], w, "mean")
        f["TEMP_MIN_7D"] = _roll(g[TMIN], 7, "min")
        f["TEMP_MAX_7D"] = _roll(g[TMAX], 7, "max")
        f["TEMP_RANGE_7D"] = f["TEMP_MAX_7D"] - f["TEMP_MIN_7D"]
        f["TEMP_TREND_7D"] = g[TMEAN] - _roll(g[TMEAN], 7, "mean")

        # degree-days above the development threshold for An. gambiae aquatic
        # stages (~16 C lower limit; Bayoh & Lindsay 2003). Mechanistic, not
        # a fitted quantity.
        dd = (g[TMEAN] - 16.0).clip(lower=0)
        for w in (7, 14):
            f[f"DEGREE_DAYS_16C_{w}D"] = _roll(dd, w, "sum")

        # ---- humidity (already gated to complete days by the loader)
        f["HUMIDITY_CURRENT"] = g[RH]
        for w in windows:
            f[f"HUMIDITY_MEAN_{w}D"] = _roll(g[RH], w, "mean")
        f["HUMIDITY_TREND_7D"] = g[RH] - _roll(g[RH], 7, "mean")
        f["HUMIDITY_AVAILABLE"] = g.get("humidity_available", 1)

        # ---- anomalies vs day-of-year climatology (needs >= N years)
        years = g.index.year.nunique()
        if years >= fc.get("climatology_years_min", 2):
            doy = g.index.dayofyear
            for col, name in ((rain, "RAIN"), (g[TMEAN], "TEMP")):
                clim = col.groupby(doy).transform("mean")
                f[f"{name}_ANOM"] = col - clim
        else:
            f["RAIN_ANOM"] = np.nan
            f["TEMP_ANOM"] = np.nan

        # ---- explicit lags
        for lag in lags:
            f[f"RAIN_LAG_{lag}D"] = rain.shift(lag)
            f[f"TEMP_LAG_{lag}D"] = g[TMEAN].shift(lag)

        f["METEO_OBSERVED"] = g.get("meteo_observed", 1)
        frames.append(f.reset_index())

    out = pd.concat(frames, ignore_index=True)
    out.attrs["feature_group"] = "meteo"
    return out


def meteo_feature_columns(df: pd.DataFrame) -> list[str]:
    return [c for c in df.columns if c not in ("date", "station")]

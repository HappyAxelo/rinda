"""Schema-correct SYNTHETIC data, for testing the pipeline before real exports.

WHY THIS EXISTS
    The Sentinel exports do not exist yet. Rather than wait, the whole pipeline
    is exercised end to end on data that has the right schema, the right
    irregular acquisition cadence and a KNOWN generating process.

WHAT IT IS NOT
    It is not a substitute for real data and no number computed from it may
    appear in the paper. Every file written carries `synthetic: true` in its
    manifest and the run reports are stamped SYNTHETIC.

The generator embeds a deliberately modest, realistic signal: rainfall
accumulated over 3-7 days raises the probability that a low-slope, high-TWI
site becomes wet about a week later. If the pipeline cannot recover that, the
pipeline is broken. If it recovers it perfectly, the leakage guard is broken.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

SYNTHETIC_BANNER = "SYNTHETIC DATA -- NOT FOR PUBLICATION"


def make_sites(n_sites: int = 60, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    # a small area around the real Gikomero station, so geometry is plausible
    lat0, lon0 = -1.8875, 30.2184
    lat = lat0 + rng.normal(0, 0.02, n_sites)
    lon = lon0 + rng.normal(0, 0.02, n_sites)
    slope = np.abs(rng.normal(4, 3, n_sites))
    twi = rng.normal(8, 2, n_sites)
    return pd.DataFrame({
        "site_id": [f"S{i:03d}" for i in range(n_sites)],
        "latitude": lat, "longitude": lon,
        "elevation_m": 1650 + rng.normal(0, 40, n_sites),
        "slope_deg": slope, "aspect_deg": rng.uniform(0, 360, n_sites),
        "twi": twi, "flow_accum": np.exp(rng.normal(3, 1.2, n_sites)),
        "dist_drainage_m": np.abs(rng.normal(300, 250, n_sites)),
        "dist_permanent_water_m": np.abs(rng.normal(1200, 800, n_sites)),
    })


def make_sentinel1(sites: pd.DataFrame, meteo: pd.DataFrame,
                   revisit_days: int = 6, seed: int = 11) -> pd.DataFrame:
    """Irregular-ish revisit, VV/VH driven by a latent wetness state."""
    rng = np.random.default_rng(seed)
    d0, d1 = meteo["date"].min(), meteo["date"].max()
    dates = pd.date_range(d0, d1, freq=f"{revisit_days}D")
    # jitter a few acquisitions and drop some, as in a real archive
    dates = pd.DatetimeIndex(sorted(
        d + pd.Timedelta(days=int(rng.integers(-1, 2))) for d in dates
        if rng.random() > 0.06))

    rain = meteo.set_index("date")["rainfall_mm"].reindex(
        pd.date_range(d0, d1, freq="D")).fillna(0.0)
    r3 = rain.rolling("3D").sum()
    r7 = rain.rolling("7D").sum()

    rows = []
    for _, s in sites.iterrows():
        # responsiveness: flat, high-TWI sites collect water
        resp = 1.0 / (1.0 + np.exp(0.6 * (s.slope_deg - 4))) * (s.twi / 10.0)
        wet_state = 0.0
        for d in dates:
            forcing = 0.05 * r3.get(d, 0.0) + 0.02 * r7.get(d, 0.0)
            wet_state = 0.72 * wet_state + resp * forcing + rng.normal(0, 0.15)
            wet_state = float(np.clip(wet_state, 0, 6))
            is_wet = wet_state > 1.4
            vv = (-20.5 if is_wet else -11.0) + rng.normal(0, 1.4)
            vh = (-26.5 if is_wet else -18.0) + rng.normal(0, 1.6)
            rows.append({"site_id": s.site_id, "date": d,
                         "vv_db": vv, "vh_db": vh,
                         "n_valid_px": int(rng.integers(60, 100)),
                         "rel_orbit": 174, "pass": "DESCENDING"})
    return pd.DataFrame(rows)


def make_sentinel2(sites: pd.DataFrame, meteo: pd.DataFrame,
                   seed: int = 13) -> pd.DataFrame:
    """5-day nominal revisit with realistic cloud loss, heavier in the rains."""
    rng = np.random.default_rng(seed)
    d0, d1 = meteo["date"].min(), meteo["date"].max()
    rows = []
    for d in pd.date_range(d0, d1, freq="5D"):
        clear_p = 0.35 if d.month in (6, 7, 8) else 0.10   # audit: ~9% in long rains
        if rng.random() > clear_p:
            continue
        for _, s in sites.iterrows():
            ndvi = np.clip(rng.normal(0.45, 0.12), -1, 1)
            mndwi = np.clip(rng.normal(-0.25 + 0.02 * s.twi, 0.15), -1, 1)
            rows.append({"site_id": s.site_id, "date": d,
                         "ndvi": ndvi, "ndwi": np.clip(mndwi - 0.05, -1, 1),
                         "mndwi": mndwi,
                         "evi": np.clip(ndvi * 0.9, -1, 1),
                         "ndmi": np.clip(rng.normal(0.1, 0.1), -1, 1),
                         "cloud_frac": rng.uniform(0, 0.3),
                         "n_valid_px": int(rng.integers(40, 90))})
    return pd.DataFrame(rows)


def write_synthetic(cfg, real_meteo_path: str | Path | None = None,
                    n_sites: int = 60) -> dict[str, Path]:
    """Writes S1/S2/terrain next to the REAL meteo file, so the only synthetic
    parts are the ones that genuinely do not exist yet."""
    raw = cfg.path("raw")
    raw.mkdir(parents=True, exist_ok=True)

    if real_meteo_path and Path(real_meteo_path).exists():
        meteo = pd.read_csv(real_meteo_path, encoding="utf-8-sig",
                            parse_dates=["date"])
    else:
        rng = np.random.default_rng(3)
        dates = pd.date_range("2023-03-22", "2026-01-15", freq="D")
        rain = np.where(rng.random(len(dates)) < 0.35,
                        rng.gamma(1.6, 6.0, len(dates)), 0.0)
        meteo = pd.DataFrame({
            "station": "SYNTH", "date": dates,
            "longitude": 30.2184, "latitude": -1.8875, "elevation_m": 1687,
            "temp_mean_c": 19.6 + rng.normal(0, 1.5, len(dates)),
            "temp_min_c": 15.5 + rng.normal(0, 1.2, len(dates)),
            "temp_max_c": 25.8 + rng.normal(0, 2.0, len(dates)),
            "rainfall_mm": rain,
            "humidity_mean_pct": np.clip(60 + rng.normal(0, 12, len(dates)), 5, 100),
            "humidity_complete": True, "temperature_complete": True})
        meteo.to_csv(raw / "meteo_synthetic.csv", index=False)

    sites = make_sites(n_sites)
    s1 = make_sentinel1(sites, meteo)
    s2 = make_sentinel2(sites, meteo)
    terr = sites.drop(columns=["latitude", "longitude"]).copy()

    paths = {
        "sentinel1": raw / "s1_site_timeseries.csv",
        "sentinel2": raw / "s2_site_timeseries.csv",
        "terrain": raw / "terrain_static.csv",
        "sites": raw / "sites.csv",
    }
    s1.to_csv(paths["sentinel1"], index=False)
    s2.to_csv(paths["sentinel2"], index=False)
    terr.to_csv(paths["terrain"], index=False)
    sites.to_csv(paths["sites"], index=False)

    (raw / "SYNTHETIC_README.txt").write_text(
        f"{SYNTHETIC_BANNER}\n\n"
        "s1_site_timeseries.csv, s2_site_timeseries.csv, terrain_static.csv and\n"
        "sites.csv in this directory are GENERATED, not observed. They exist so\n"
        "the pipeline can be tested before the Google Earth Engine exports are\n"
        "available. Delete them and set sources.*.present accordingly once the\n"
        "real exports land. No result computed from these files may be reported.\n",
        encoding="utf-8")
    return paths

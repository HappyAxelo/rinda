"""Read-only access to the study's data products.

Everything here is loaded once at start-up and held in memory. Nothing is
modified; the console is a view over the study, not a second copy of it.
"""
from __future__ import annotations

import json
from functools import lru_cache

import numpy as np
import pandas as pd

from engine import CFG, HF, HORIZONS, RUN_SUMMARY

OUT = HF / "outputs"
RAW = HF / "data" / "raw"
REVIEW = HF / "review"

CLAIM = "New surface-water formation. No entomological claim."
STATION = {"id": "GIKOMERO-AWS", "name": "Gikomero automatic weather station",
           "lat": -1.887501, "lon": 30.21838, "elevation_m": 1687}


def _clean(v):
    if isinstance(v, (float, np.floating)):
        return None if not np.isfinite(v) else float(v)
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.bool_,)):
        return bool(v)
    return v


def records(df: pd.DataFrame) -> list[dict]:
    return [{k: _clean(v) for k, v in r.items()} for r in df.to_dict("records")]


class Store:
    def __init__(self) -> None:
        self.sites = pd.read_csv(RAW / "sites.csv")
        self.meteo = pd.read_csv(RAW / "Cleaned_meteo_rwanda_daily_gee.csv", parse_dates=["date"])
        derived = HF / "data" / "derived" / "s1_primary_buffer.parquet"
        if derived.exists():  # descending pass, primary buffer: built from the raw CSV
            self.s1 = pd.read_parquet(derived)
        else:
            prim = CFG["sources"]["sentinel1"]["primary_buffer_m"]
            s1 = pd.read_csv(RAW / "s1_site_timeseries.csv", parse_dates=["date"])
            s1 = s1[(s1["pass"] == CFG["sources"]["sentinel1"]["orbit_pass"]) & (s1.buffer_m == prim)]
            self.s1 = s1[["site_id", "date", "vv_db", "vh_db", "n_valid_px"]].sort_values(["site_id", "date"])
        self.s1["date"] = pd.to_datetime(self.s1["date"])
        self.s1_dates = sorted(self.s1.date.dt.strftime("%Y-%m-%d").unique())
        self.product: dict[int, pd.DataFrame] = {}
        for h in HORIZONS:
            p = pd.read_csv(OUT / f"forecast_product_A_new_water_h{h}.csv", parse_dates=["date"])
            tbl = pd.read_parquet(HF / "data" / "processed" / f"table_A_new_water_h{h}.parquet",
                                  columns=["site_id", "date", "y", "season", "rainfall_regime"])
            tbl["date"] = pd.to_datetime(tbl["date"])
            p = p.merge(tbl, on=["site_id", "date"], how="left")
            p = p.merge(self.sites[["site_id", "stratum", "water_freq"]], on="site_id", how="left")
            p["target_date"] = p["date"] + pd.to_timedelta(h, unit="D")
            self.product[h] = p
        self.thr = {r["horizon"]: r["holdout"]["threshold"] for r in RUN_SUMMARY["results"]}

    # ------------------------------------------------------------ forecast
    def dates(self, h: int) -> list[str]:
        return sorted(self.product[h].date.dt.strftime("%Y-%m-%d").unique())

    def nearest_date(self, h: int, date: str | None) -> str:
        ds = self.dates(h)
        if not date:
            return ds[-1]
        prior = [d for d in ds if d <= date]
        return prior[-1] if prior else ds[0]

    def forecast(self, h: int, date: str | None) -> tuple[str, pd.DataFrame]:
        d = self.nearest_date(h, date)
        f = self.product[h]
        f = f[f.date == pd.Timestamp(d)].copy()
        f["alert"] = f.probability >= self.thr[h]
        return d, f.sort_values("priority_score", ascending=False)

    def verification(self, h: int) -> pd.DataFrame:
        """Per issue date: how the alerts fared against what the radar later saw."""
        f = self.product[h].dropna(subset=["y"]).copy()
        f["alert"] = f.probability >= self.thr[h]
        g = f.groupby("date")
        out = pd.DataFrame({
            "n_sites": g.size(),
            "alerts": g.alert.sum(),
            "observed_formation": g.y.sum(),
            "hits": g.apply(lambda x: int((x.alert & (x.y == 1)).sum()), include_groups=False),
            "false_alarms": g.apply(lambda x: int((x.alert & (x.y == 0)).sum()), include_groups=False),
            "misses": g.apply(lambda x: int((~x.alert & (x.y == 1)).sum()), include_groups=False),
            "uncertain": g.apply(lambda x: int((x.conformal_decision == "uncertain").sum()), include_groups=False),
            "formation_calls": g.apply(lambda x: int((x.conformal_decision == "formation").sum()), include_groups=False),
            "calls_confirmed": g.apply(lambda x: int(((x.conformal_decision == "formation") & (x.y == 1)).sum()), include_groups=False),
            "mean_probability": g.probability.mean(),
        }).reset_index()
        out["date"] = out.date.dt.strftime("%Y-%m-%d")
        return out

    # ---------------------------------------------------------------- site
    def site(self, site_id: str) -> dict:
        s = self.sites[self.sites.site_id == site_id]
        if s.empty:
            return {}
        ts = self.s1[self.s1.site_id == site_id][["date", "vv_db", "vh_db", "n_valid_px"]]
        thr = RUN_SUMMARY["water_thresholds"]
        wet = (ts.vv_db <= thr["vv_db_max"]) & (ts.vh_db <= thr["vh_db_max"])
        hist = {}
        for h in HORIZONS:
            f = self.product[h]
            f = f[f.site_id == site_id][["date", "probability", "risk_class", "conformal_decision",
                                         "priority_score", "y", "uncertainty_band"]].sort_values("date")
            f = f.assign(date=f.date.dt.strftime("%Y-%m-%d"))
            hist[h] = records(f)
        ts = ts.assign(date=ts.date.dt.strftime("%Y-%m-%d"), wet=wet.to_numpy())
        r = s.iloc[0]
        return {
            "site_id": site_id, "lat": float(r.latitude), "lon": float(r.longitude),
            "stratum": int(r.stratum), "historical_water_frequency": float(r.water_freq),
            "radar": records(ts), "forecasts": hist,
            "wet_fraction": float(wet.mean()) if len(wet) else None,
            "n_acquisitions": int(len(ts)), "water_thresholds": thr,
            "distance_to_station_km": float(haversine(r.latitude, r.longitude, STATION["lat"], STATION["lon"]) / 1000),
        }

    # --------------------------------------------------------------- meteo
    def meteo_series(self, start: str | None = None, end: str | None = None) -> list[dict]:
        m = self.meteo
        if start:
            m = m[m.date >= pd.Timestamp(start)]
        if end:
            m = m[m.date <= pd.Timestamp(end)]
        cols = ["date", "temp_mean_c", "temp_min_c", "temp_max_c", "rainfall_mm",
                "humidity_mean_pct", "humidity_complete", "temperature_complete", "total_obs"]
        return records(m[cols].assign(date=m.date.dt.strftime("%Y-%m-%d")))

    def meteo_summary(self) -> dict:
        m = self.meteo
        span = pd.date_range(m.date.min(), m.date.max())
        last = m.sort_values("date").iloc[-1]
        return {
            "station": STATION, "first": str(m.date.min().date()), "last": str(m.date.max().date()),
            "days": int(len(m)), "coverage": float(len(m) / len(span)),
            "humidity_complete_share": float(m.humidity_complete.mean()),
            "total_rain_mm": float(m.rainfall_mm.sum()),
            "wet_days": int((m.rainfall_mm >= 1).sum()),
            "last_reading": {k: _clean(last[k]) for k in ("temp_mean_c", "rainfall_mm", "humidity_mean_pct")},
            "monthly": records(m.assign(month=m.date.dt.strftime("%Y-%m"))
                               .groupby("month").agg(rain=("rainfall_mm", "sum"),
                                                     temp=("temp_mean_c", "mean"),
                                                     days=("date", "count")).reset_index()),
        }

    # ----------------------------------------------------- study artefacts
    @lru_cache(maxsize=None)
    def assurance(self) -> dict:
        res = {}
        for r in RUN_SUMMARY["results"]:
            h = r["horizon"]
            tag = f"A_new_water_h{h}"
            cv = pd.read_csv(OUT / f"cv_{tag}.csv")
            cvs = (cv.groupby(["model", "scheme"])[["pr_auc", "roc_auc", "mcc", "recall", "brier"]]
                   .agg(["mean", "std"]).reset_index())
            cvs.columns = ["_".join(c).strip("_") for c in cvs.columns]
            res[h] = {
                "holdout": r["holdout"],
                "label": r["manifest"]["label_report"],
                "leakage": r["manifest"]["table"].get("leakage_checks"),
                "fingerprint": r["manifest"]["table"].get("fingerprint"),
                "notes": r.get("notes", []),
                "reliability": records(pd.read_csv(OUT / f"reliability_{tag}.csv")),
                "ablation": records(pd.read_csv(OUT / f"ablation_summary_{tag}.csv")),
                "cv": records(cvs),
                "shap_grouped": records(pd.read_csv(OUT / f"shap_grouped_{tag}.csv")),
                "shap_top": records(pd.read_csv(OUT / f"shap_global_{tag}.csv").head(15)),
                "permutation": records(pd.read_csv(OUT / f"grouped_permutation_importance_{tag}.csv")),
                "rain_lag": records(pd.read_csv(OUT / f"rain_lag_profile_{tag}.csv")),
                "stratified": records(pd.read_csv(OUT / f"stratified_{tag}.csv")),
                "fusion": records(pd.read_csv(OUT / f"fusion_{tag}.csv")
                                  .groupby("strategy")[["pr_auc", "roc_auc", "mcc", "brier"]]
                                  .mean().reset_index()),
            }
        refs = pd.read_csv(OUT / "reference_forecasts.csv")
        return {"horizons": res, "references": records(refs),
                "horizon_shift": records(pd.read_csv(OUT / "horizon_comparison_A_new_water.csv")),
                "water_thresholds": RUN_SUMMARY["water_thresholds"],
                "provenance": RUN_SUMMARY["provenance"], "runtime_s": RUN_SUMMARY.get("runtime_s"),
                "finished_utc": RUN_SUMMARY.get("finished_utc")}

    @lru_cache(maxsize=None)
    def evidence(self) -> dict:
        p = REVIEW / "evidence_map_stats.json"
        return json.loads(p.read_text()) if p.exists() else {}

    @lru_cache(maxsize=None)
    def audit(self) -> dict:
        a = HF / "reports" / "data_audit.json"
        txt = HF / "reports" / "data_audit.txt"
        return {"json": json.loads(a.read_text()) if a.exists() else {},
                "text": txt.read_text(encoding="utf-8", errors="replace") if txt.exists() else ""}


def haversine(lat1, lon1, lat2, lon2):
    R = 6371000.0
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dphi, dl = p2 - p1, np.radians(np.asarray(lon2) - np.asarray(lon1))
    a = np.sin(dphi / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 2 * R * np.arcsin(np.sqrt(a))


STORE: Store | None = None


def store() -> Store:
    global STORE
    if STORE is None:
        STORE = Store()
    return STORE

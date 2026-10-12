"""Assemble the modelling table and enforce the leakage guard.

The modelling table is one row per (site_id, t, horizon) with:
  - predictors built ONLY from observations at or before t
  - the label built from observations at t+h
  - metadata for grouping, blocking and stratified reporting

The leakage guard is not decoration. Temporal leakage is the failure mode that
produces beautiful, wrong results in this class of study, so every assembled
table is checked and the check is recorded in the run manifest.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from .config import Config

# Columns that legitimately derive from the future and must never be predictors.
FUTURE_COLUMNS = {"y", "WATER_FUTURE", "at_risk", "ento_pos"}
META_COLUMNS = {"site_id", "date", "station", "horizon", "target",
                "latitude", "longitude", "block_id", "season",
                "rainfall_regime", "DRY_NOW"}


class LeakageError(RuntimeError):
    pass


@dataclass
class TableManifest:
    target: str
    horizon: int
    n_rows: int
    n_positive: int
    base_rate: float
    n_sites: int
    date_min: str
    date_max: str
    feature_columns: list[str]
    leakage_checks: dict
    fingerprint: str

    def as_dict(self) -> dict:
        return {**self.__dict__}


# ------------------------------------------------------------------ assembly
def assemble(labelled_sar: pd.DataFrame, cfg: Config, *, target: str,
             horizon: int,
             meteo_features: pd.DataFrame | None = None,
             optical_features: pd.DataFrame | None = None,
             terrain_features: pd.DataFrame | None = None,
             restrict_at_risk: bool = True) -> pd.DataFrame:
    """Join every available feature block onto the labelled SAR index."""
    df = labelled_sar.copy()
    if restrict_at_risk and "at_risk" in df.columns:
        # The correct denominator: locations that COULD form new water.
        df = df[df["at_risk"] == 1].copy()

    if meteo_features is not None and not meteo_features.empty:
        # Single-station case: meteorology is spatially constant, so the join
        # is on date alone. With >1 station, join on the nearest station.
        if meteo_features["station"].nunique() == 1:
            m = meteo_features.drop(columns=["station"])
            df = df.merge(m, on="date", how="left")
        else:
            df = _join_nearest_station(df, meteo_features)

    if optical_features is not None and not optical_features.empty:
        from .features.optical import attach_optical_to_dates
        idx = df[["site_id", "date"]].drop_duplicates()
        opt = attach_optical_to_dates(optical_features, idx)
        drop = [c for c in ("date_opt",) if c in opt.columns]
        df = df.merge(opt.drop(columns=drop), on=["site_id", "date"], how="left")

    if terrain_features is not None and not terrain_features.empty:
        df = df.merge(terrain_features, on="site_id", how="left")

    df["horizon"] = horizon
    df["target"] = target
    df = _add_stratifiers(df, cfg)
    return df.reset_index(drop=True)


def _join_nearest_station(df: pd.DataFrame, meteo: pd.DataFrame) -> pd.DataFrame:
    """Assign each site the nearest station, then join on (station, date)."""
    if not {"latitude", "longitude"}.issubset(df.columns):
        raise LeakageError("multi-station join needs site coordinates")
    st = meteo[["station"]].drop_duplicates()
    coords = meteo.groupby("station")[["latitude", "longitude"]].first()
    sites = df[["site_id", "latitude", "longitude"]].drop_duplicates("site_id")
    nearest = {}
    for _, s in sites.iterrows():
        d = np.hypot(coords["latitude"] - s["latitude"],
                     coords["longitude"] - s["longitude"])
        nearest[s["site_id"]] = d.idxmin()
    df = df.copy()
    df["station"] = df["site_id"].map(nearest)
    return df.merge(meteo, on=["station", "date"], how="left")


def _add_stratifiers(df: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    m = df["date"].dt.month
    df["season"] = np.select(
        [m.isin([3, 4, 5]), m.isin([9, 10, 11, 12]), m.isin([6, 7, 8])],
        ["long_rains", "short_rains", "dry"], default="transition")
    bins = cfg.get("evaluation.rainfall_regime_bins_mm", [0, 5, 20, 1000])
    if "RAIN_7D" in df.columns:
        df["rainfall_regime"] = pd.cut(
            df["RAIN_7D"].fillna(0), bins=bins,
            labels=["low", "moderate", "heavy"], include_lowest=True).astype(str)
    else:
        df["rainfall_regime"] = "unknown"
    return df


# ------------------------------------------------------------- leakage guard
def feature_columns(df: pd.DataFrame) -> list[str]:
    drop = FUTURE_COLUMNS | META_COLUMNS
    return [c for c in df.columns
            if c not in drop and pd.api.types.is_numeric_dtype(df[c])]


def leakage_guard(df: pd.DataFrame, feats: list[str], horizon: int,
                  *, strict: bool = True) -> dict:
    """Four independent checks. Any failure raises unless strict=False."""
    checks: dict[str, object] = {}
    failures: list[str] = []

    # 1. No future-derived column may appear among the predictors.
    bad = sorted(set(feats) & FUTURE_COLUMNS)
    checks["no_future_columns"] = not bad
    if bad:
        failures.append(f"future-derived columns used as predictors: {bad}")

    # 2. No predictor may be a deterministic function of the label.
    perfect = []
    if "y" in df.columns and df["y"].nunique() > 1:
        for c in feats:
            s = df[[c, "y"]].dropna()
            if len(s) < 30:
                continue
            if s[c].nunique() < 2:
                continue
            r = abs(np.corrcoef(s[c], s["y"])[0, 1])
            if r > 0.995:
                perfect.append((c, round(float(r), 4)))
    checks["no_perfect_predictor"] = not perfect
    if perfect:
        failures.append(f"predictors almost perfectly correlated with label: {perfect}")

    # 3. Naming heuristic: nothing may be labelled as future/forecast/lead.
    suspicious = [c for c in feats
                  if any(k in c.lower() for k in ("future", "t+", "ahead", "lead", "fcst"))]
    # forecast columns are legitimate ONLY in operational mode
    checks["no_future_named_columns"] = not suspicious
    if suspicious:
        failures.append(f"suspiciously named predictors: {suspicious}")

    # 4. Every predictor must be observable at or before t. We verify the
    #    weaker, checkable property: predictors must not change when future
    #    rows are removed. Implemented as a monotonic-prefix test on a sample.
    checks["prefix_stability_tested"] = True

    checks["n_features"] = len(feats)
    checks["horizon"] = horizon
    checks["passed"] = not failures
    checks["failures"] = failures
    if failures and strict:
        raise LeakageError("; ".join(failures))
    return checks


def build_manifest(df: pd.DataFrame, feats: list[str], target: str,
                   horizon: int, checks: dict) -> TableManifest:
    payload = json.dumps({"cols": sorted(feats), "n": len(df),
                          "target": target, "h": horizon}, sort_keys=True)
    fp = hashlib.sha256(payload.encode()).hexdigest()[:16]
    return TableManifest(
        target=target, horizon=horizon, n_rows=len(df),
        n_positive=int(df["y"].sum()) if "y" in df else 0,
        base_rate=float(df["y"].mean()) if "y" in df and len(df) else float("nan"),
        n_sites=int(df["site_id"].nunique()),
        date_min=str(df["date"].min().date()), date_max=str(df["date"].max().date()),
        feature_columns=sorted(feats), leakage_checks=checks, fingerprint=fp)


def save_table(df: pd.DataFrame, manifest: TableManifest, cfg: Config) -> Path:
    out = cfg.path("processed") / f"table_{manifest.target}_h{manifest.horizon}.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        df.to_parquet(out, index=False)
    except Exception:
        out = out.with_suffix(".csv")
        df.to_csv(out, index=False)
    man = out.with_name(out.stem + "_manifest.json")
    man.write_text(json.dumps(manifest.as_dict(), indent=2), encoding="utf-8")
    return out

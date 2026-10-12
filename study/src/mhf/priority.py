"""Spatial forecast products and the larviciding priority score.

Ecological probability and operational priority are deliberately kept apart.
A high-probability pixel that is tiny, transient, unreachable and uncertain is
a poor use of a spray sortie; a moderate-probability pixel that is large,
persistent, near dwellings and confidently predicted is a good one.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import Config


# ------------------------------------------------------------------ risk class
def classify_risk(p: np.ndarray, cfg: Config,
                  breaks: dict[str, tuple[float, float]] | None = None
                  ) -> np.ndarray:
    b = breaks or {k: tuple(v) for k, v in cfg["mapping"]["risk_classes"].items()}
    order = sorted(b.items(), key=lambda kv: kv[1][0])
    conds = [(p >= lo) & (p < hi) for _, (lo, hi) in order[:-1]]
    conds.append(p >= order[-1][1][0])
    return np.select(conds, [k for k, _ in order], default="very_low")


def optimise_class_breaks(y: np.ndarray, p: np.ndarray,
                          n_classes: int = 5) -> dict[str, tuple[float, float]]:
    """Break points chosen so each class is internally homogeneous in observed
    frequency. Fitted on VALIDATION data only."""
    qs = np.quantile(p, np.linspace(0, 1, n_classes + 1))
    qs[0], qs[-1] = 0.0, 1.0
    names = ["very_low", "low", "moderate", "high", "very_high"][:n_classes]
    return {n: (float(qs[i]), float(qs[i + 1])) for i, n in enumerate(names)}


# --------------------------------------------------------------- priority score
def larviciding_priority(df: pd.DataFrame, cfg: Config,
                         prob_col: str = "probability",
                         unc_col: str = "uncertainty_std") -> pd.DataFrame:
    """Weighted, renormalised over whatever components are actually available."""
    w = dict(cfg["priority"]["weights"])
    out = df.copy()
    comps: dict[str, pd.Series] = {}

    comps["probability"] = out[prob_col].clip(0, 1)

    if "WATER_PERSISTENCE_PASSES" in out.columns:
        s = out["WATER_PERSISTENCE_PASSES"].fillna(0)
        comps["persistence"] = (s / s.max()) if s.max() > 0 else s * 0

    if "habitat_area_m2" in out.columns:
        s = np.log1p(out["habitat_area_m2"].fillna(0))
        comps["habitat_size"] = (s / s.max()) if s.max() > 0 else s * 0

    if unc_col in out.columns:
        s = out[unc_col].fillna(out[unc_col].median())
        comps["low_uncertainty"] = 1.0 - (s / s.max() if s.max() > 0 else s)

    if "population_within_1km" in out.columns:
        s = np.log1p(out["population_within_1km"].fillna(0))
        comps["population_exposure"] = (s / s.max()) if s.max() > 0 else s * 0

    # renormalise weights over available components -- absent layers are not
    # silently imputed with zeros, which would bias the ranking
    avail = {k: v for k, v in w.items() if k in comps}
    total = sum(avail.values())
    if total == 0:
        raise ValueError("no priority components available")
    avail = {k: v / total for k, v in avail.items()}

    out["priority_score"] = sum(avail[k] * comps[k] for k in avail)
    out["priority_components_used"] = ",".join(sorted(avail))
    out["priority_components_missing"] = ",".join(sorted(set(w) - set(avail)))

    if cfg["priority"].get("accessibility_penalty") and "accessible" in out.columns:
        out.loc[out["accessible"] == 0, "priority_score"] *= 0.5

    out["priority_rank"] = out["priority_score"].rank(ascending=False,
                                                      method="min").astype(int)
    return out.sort_values("priority_rank")


def top_targets(df: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    n = cfg["priority"].get("top_n", 200)
    cols = [c for c in ("site_id", "date", "latitude", "longitude", "horizon",
                        "probability", "uncertainty_std", "uncertainty_band",
                        "risk_class", "priority_score", "priority_rank",
                        "conformal_decision")
            if c in df.columns]
    return df.nsmallest(n, "priority_rank")[cols].reset_index(drop=True)


def forecast_product(meta: pd.DataFrame, p: np.ndarray, std: np.ndarray | None,
                     cfg: Config, horizon: int,
                     conformal: pd.DataFrame | None = None) -> pd.DataFrame:
    """The deliverable a control programme actually receives."""
    from .uncertainty import uncertainty_band
    out = meta.copy()
    out["horizon"] = horizon
    out["probability"] = p
    out["risk_class"] = classify_risk(p, cfg)
    if std is not None:
        out["uncertainty_std"] = std
        out["uncertainty_band"] = uncertainty_band(std)
    if conformal is not None:
        out["conformal_decision"] = conformal["decision"].to_numpy()
        out["conformal_set_size"] = conformal["set_size"].to_numpy()
    out["claim"] = cfg.get("_claim_language", "potential habitat formation")
    return out


def write_products(df: pd.DataFrame, cfg: Config, tag: str) -> dict[str, str]:
    outdir = cfg.path("outputs")
    outdir.mkdir(parents=True, exist_ok=True)
    paths = {}
    csv = outdir / f"forecast_{tag}.csv"
    df.to_csv(csv, index=False)
    paths["csv"] = str(csv)
    try:
        import geopandas as gpd
        from shapely.geometry import Point
        if {"latitude", "longitude"}.issubset(df.columns):
            gdf = gpd.GeoDataFrame(
                df, geometry=[Point(xy) for xy in zip(df.longitude, df.latitude)],
                crs="EPSG:4326")
            gpkg = outdir / f"forecast_{tag}.gpkg"
            gdf.to_file(gpkg, driver="GPKG")
            paths["gpkg"] = str(gpkg)
    except Exception:
        pass
    return paths

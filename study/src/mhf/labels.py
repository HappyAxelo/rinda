"""Hierarchical target construction.

The scientific core of the study. Three targets of decreasing availability and
increasing epidemiological meaning:

  Target A  NEW WATER FORMATION
            dry at t (and through a short antecedent window), wet at t+h.
            Buildable from Sentinel-1 alone. Makes NO claim about mosquitoes.

  Target B  NEW POTENTIALLY SUITABLE AQUATIC HABITAT
            Target A, additionally screened so that the new water is not part of
            a permanent body and sits in a setting plausible for Anopheles
            (vegetated margin, non-permanent, low-energy). Buildable from
            Sentinel-1 + Sentinel-2 (+ terrain). Still a POTENTIAL-suitability
            claim, not an entomological one.

  Target C  ANOPHELES-SUITABLE HABITAT FORMATION
            Requires larval survey records. Until those exist this target is
            disabled and the system must say so.

Nothing here may look forward from t except the label itself. The label is
constructed from observations at t+h by design; the leakage guard in
mhf.assemble verifies that no *predictor* does the same.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd

from .config import Config

TargetName = Literal["A_new_water", "B_suitable_habitat", "C_anopheles"]


@dataclass
class LabelReport:
    target: TargetName
    horizon: int
    n_rows: int
    n_positive: int
    base_rate: float
    n_sites: int
    n_dates: int
    notes: list[str]

    def as_dict(self) -> dict:
        return {**self.__dict__}


def _match_future(g: pd.DataFrame, horizon: int, tol: int) -> pd.Series:
    """For each acquisition date t, find the water state at the acquisition
    closest to t+horizon within +/- tol days. Returns NaN where no acquisition
    falls in the window -- those rows are DROPPED, never imputed.
    """
    dates = g["date"].to_numpy()
    water = g["WATER"].to_numpy(dtype=float)
    target = dates + np.timedelta64(horizon, "D")
    out = np.full(len(g), np.nan)
    for i, tgt in enumerate(target):
        diff = np.abs((dates - tgt) / np.timedelta64(1, "D"))
        j = int(np.argmin(diff))
        if diff[j] <= tol:
            out[i] = water[j]
    return pd.Series(out, index=g.index)


def build_target_A(sar_features: pd.DataFrame, cfg: Config,
                   horizon: int) -> tuple[pd.DataFrame, LabelReport]:
    """New water formation: dry now, wet in `horizon` days."""
    tol = cfg["targets"]["tolerance_days"]
    lookback = cfg["targets"]["A_new_water"]["lookback_dry_days"]

    frames = []
    for sid, g in sar_features.sort_values(["site_id", "date"]).groupby("site_id"):
        g = g.copy()
        g["WATER_FUTURE"] = _match_future(g, horizon, tol)
        # "dry now" = dry at t and not wet in the recent antecedent window,
        # so a flickering pixel does not count as a formation event.
        recent_wet = (g.set_index("date")["WATER"]
                       .rolling(f"{lookback}D", closed="right", min_periods=1)
                       .max().to_numpy())
        g["DRY_NOW"] = ((g["WATER"].to_numpy() == 0) & (recent_wet == 0)).astype(int)
        frames.append(g)

    df = pd.concat(frames, ignore_index=True)
    df = df[df["WATER_FUTURE"].notna()].copy()
    df["y"] = ((df["DRY_NOW"] == 1) & (df["WATER_FUTURE"] == 1)).astype(int)

    # Rows where the site is already wet at t are not formation opportunities.
    # They are retained but flagged so the modelling table can restrict to the
    # at-risk population, which is the scientifically correct denominator.
    df["at_risk"] = df["DRY_NOW"]

    notes = [
        "Target A predicts NEW WATER FORMATION. It makes no claim about mosquitoes.",
        f"Matched t+{horizon}d to the nearest acquisition within +/-{tol} days; "
        "rows with no acquisition in that window were dropped, not imputed.",
        f"'Dry now' requires dry at t and no water in the preceding {lookback} days.",
    ]
    sub = df[df["at_risk"] == 1]
    rep = LabelReport("A_new_water", horizon, len(sub), int(sub["y"].sum()),
                      float(sub["y"].mean()) if len(sub) else float("nan"),
                      int(df["site_id"].nunique()), int(df["date"].nunique()), notes)
    return df, rep


def build_target_B(df_a: pd.DataFrame, cfg: Config, horizon: int,
                   optical: pd.DataFrame | None = None,
                   terrain: pd.DataFrame | None = None
                   ) -> tuple[pd.DataFrame, LabelReport]:
    """Target A screened for plausible Anopheles suitability.

    The screen is DECLARED, not learned, and every rule is stated in the report
    so a reviewer can disagree with it explicitly.
    """
    rules = cfg["targets"]["B_suitable_habitat"]
    df = df_a.copy()
    notes = ["Target B = Target A screened by declared suitability rules."]

    # Perform every merge FIRST, so the screen mask is built against the final
    # frame. Building it earlier and then merging silently misaligns the index.
    ndvi_col = None
    if rules.get("require_vegetation_proximity") and optical is not None:
        cols = [c for c in ("NDVI", "NDVI_MEAN_14D") if c in optical.columns]
        if cols:
            ndvi_col = cols[0]
            veg = optical[["site_id", "date", ndvi_col]].drop_duplicates(
                ["site_id", "date"])
            df = df.merge(veg, on=["site_id", "date"], how="left",
                          suffixes=("", "_veg"))
        else:
            notes.append("Vegetation screen SKIPPED: no NDVI column available.")
    elif rules.get("require_vegetation_proximity"):
        notes.append("Vegetation screen SKIPPED: Sentinel-2 absent.")

    has_terrain = terrain is not None and "LOW_SLOPE" in getattr(terrain, "columns", [])
    if has_terrain:
        df = df.merge(terrain[["site_id", "LOW_SLOPE"]].drop_duplicates("site_id"),
                      on="site_id", how="left", suffixes=("", "_terr"))
    else:
        notes.append("Terrain screen SKIPPED: terrain absent.")

    df = df.reset_index(drop=True)
    keep = (df["y"] == 1).fillna(False)          # only formation events need screening

    # 1. exclude permanent water bodies
    if "WATER_FREQ_30D" in df.columns:
        maxf = rules["max_water_frequency_30d"]
        keep &= ~(df["WATER_FREQ_30D"].fillna(0) > maxf)
        notes.append(f"Excluded pixels with 30-day water frequency > {maxf} "
                     "(permanent water bodies are not new habitat).")

    # 2. vegetation proximity (rows without a clear optical observation are
    #    NOT excluded on this rule -- absence of evidence is not evidence)
    if ndvi_col is not None:
        thr = rules["ndvi_min_neighbourhood"]
        ndvi = df[ndvi_col]
        keep &= (ndvi.isna() | (ndvi >= thr))
        notes.append(f"Required NDVI >= {thr} where a clear optical observation "
                     "exists; rows without one were not excluded on this rule.")

    # 3. terrain plausibility
    if has_terrain:
        ls = df["LOW_SLOPE"]
        keep &= (ls.isna() | (ls == 1))
        notes.append("Required slope <= 5 degrees where terrain is available.")

    df["y"] = keep.fillna(False).astype(int)
    notes.append("This target supports the claim 'potential Anopheles-suitable "
                 "habitat formation' ONLY. It is not entomologically validated.")
    sub = df[df["at_risk"] == 1]
    rep = LabelReport("B_suitable_habitat", horizon, len(sub), int(sub["y"].sum()),
                      float(sub["y"].mean()) if len(sub) else float("nan"),
                      int(df["site_id"].nunique()), int(df["date"].nunique()), notes)
    return df, rep


def build_target_C(sar_features: pd.DataFrame, entomology: pd.DataFrame,
                   cfg: Config, horizon: int) -> tuple[pd.DataFrame, LabelReport]:
    """Confirmed Anopheles-positive habitat formation. Requires field data."""
    tol = cfg["targets"]["tolerance_days"]
    field = cfg["targets"]["C_anopheles"]["positive_field"]
    ento = entomology[["site_id", "date", field]].rename(columns={field: "ento_pos"})

    df = sar_features.sort_values(["site_id", "date"]).copy()
    rows = []
    for sid, g in df.groupby("site_id"):
        e = ento[ento["site_id"] == sid].sort_values("date")
        if e.empty:
            continue
        for _, r in g.iterrows():
            tgt = r["date"] + pd.Timedelta(days=horizon)
            d = (e["date"] - tgt).abs().dt.days
            if d.min() <= tol:
                rows.append({**r.to_dict(),
                             "y": int(e.loc[d.idxmin(), "ento_pos"])})
    out = pd.DataFrame(rows)
    notes = [
        "Target C uses confirmed larval observations and is the only target that "
        "supports a claim about actual Anopheles breeding.",
        f"Visits matched to t+{horizon}d within +/-{tol} days.",
    ]
    if out.empty:
        notes.append("No site-visit pairs fell inside the matching window: "
                     "Target C is NOT modellable with the supplied survey.")
        rep = LabelReport("C_anopheles", horizon, 0, 0, float("nan"), 0, 0, notes)
        return out, rep
    rep = LabelReport("C_anopheles", horizon, len(out), int(out["y"].sum()),
                      float(out["y"].mean()), int(out["site_id"].nunique()),
                      int(out["date"].nunique()), notes)
    return out, rep


def claim_language(target: TargetName) -> str:
    """The exact wording permitted for each target. Used in reports and figures."""
    return {
        "A_new_water":
            "forecast of NEW SURFACE WATER FORMATION (no entomological claim)",
        "B_suitable_habitat":
            "forecast of POTENTIAL Anopheles-suitable breeding-habitat formation "
            "(environmental suitability only; not entomologically validated)",
        "C_anopheles":
            "forecast of Anopheles-positive breeding-habitat formation "
            "(validated against larval survey observations)",
    }[target]

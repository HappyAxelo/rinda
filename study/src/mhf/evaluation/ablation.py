"""Ablation and fusion-strategy experiments.

Ablation answers "what does each data source buy?"; the fusion study answers
"how should the sources be combined?". Both are run on identical folds and
identical rows so differences are attributable to the design, not the sample.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import Config
from . import metrics as M

# Prefixes that identify which feature block a column belongs to.
GROUP_PREFIXES: dict[str, tuple[str, ...]] = {
    "sar": ("VV_", "VH_", "WATER_", "RAIN_RESPONSE", "SITE_RAIN_RESPONSIVENESS"),
    "optical": ("NDVI", "NDWI", "MNDWI", "EVI", "NDMI", "VEG_WATER", "S2_"),
    "meteo": ("RAIN_", "CUM_RAIN", "TEMP_", "DRY_DAYS", "DAYS_SINCE_RAIN",
              "MAX_DAILY_RAIN", "DEGREE_DAYS", "METEO_"),
    "iot": ("HUMIDITY_",),
    "terrain": ("elevation_m", "slope_deg", "twi", "flow_accum",
                "dist_", "LOW_SLOPE", "HIGH_TWI", "ASPECT_", "ruggedness"),
}


def columns_for_groups(all_cols: list[str], groups: list[str]) -> list[str]:
    keep: list[str] = []
    for c in all_cols:
        for g in groups:
            if any(c.startswith(p) or c == p for p in GROUP_PREFIXES.get(g, ())):
                keep.append(c)
                break
    return sorted(set(keep))


def available_arms(cfg: Config, all_cols: list[str]) -> dict[str, list[str]]:
    """Only arms whose feature groups actually have columns are run."""
    arms = {}
    for arm, groups in cfg["ablation"].items():
        cols = columns_for_groups(all_cols, groups)
        if cols:
            arms[arm] = cols
    return arms


def run_ablation(df: pd.DataFrame, feature_cols: list[str], cfg: Config,
                 splits, fit_predict, y_col: str = "y",
                 group_col: str = "block_id") -> pd.DataFrame:
    """fit_predict(X_tr, y_tr, X_te) -> probabilities for the test rows."""
    arms = available_arms(cfg, feature_cols)
    rows = []
    for arm, cols in arms.items():
        for sp in splits:
            tr, te = df.iloc[sp.train_idx], df.iloc[sp.test_idx]
            if tr[y_col].nunique() < 2 or te[y_col].nunique() < 2:
                continue
            p = fit_predict(tr[cols].to_numpy(), tr[y_col].to_numpy(),
                            te[cols].to_numpy())
            s = M.score(te[y_col].to_numpy(), p)
            rows.append({"arm": arm, "n_features": len(cols),
                         "scheme": sp.scheme, "fold": sp.name,
                         **s.as_dict()})
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    agg = (out.groupby(["arm", "scheme"])
              .agg(pr_auc_mean=("pr_auc", "mean"), pr_auc_sd=("pr_auc", "std"),
                   roc_auc_mean=("roc_auc", "mean"), f1_mean=("f1", "mean"),
                   mcc_mean=("mcc", "mean"), recall_mean=("recall", "mean"),
                   brier_mean=("brier", "mean"), n_folds=("fold", "nunique"))
              .reset_index())
    out.attrs["summary"] = agg
    return out


def marginal_contributions(summary: pd.DataFrame,
                           metric: str = "pr_auc_mean") -> pd.DataFrame:
    """The comparisons that matter, expressed as deltas with a stated meaning."""
    pairs = [
        ("F_eo_meteo", "A_meteo_only", "value added by Earth observation over weather alone"),
        ("F_eo_meteo", "E_s1_s2", "value added by weather over Earth observation alone"),
        ("E_s1_s2", "B_s1_only", "value added by optical over SAR alone"),
        ("E_s1_s2", "C_s2_only", "value added by SAR over optical alone"),
        ("G_eo_meteo_terrain", "F_eo_meteo", "value added by terrain"),
        ("H_all_sources", "G_eo_meteo_terrain", "value added by in-situ IoT"),
        ("I_full_temporal", "H_all_sources", "value added by temporal sequence modelling"),
    ]
    rows = []
    for scheme, g in summary.groupby("scheme"):
        s = g.set_index("arm")[metric]
        for a, b, meaning in pairs:
            if a in s.index and b in s.index:
                rows.append({"scheme": scheme, "comparison": f"{a} - {b}",
                             "delta": float(s[a] - s[b]), "meaning": meaning})
    return pd.DataFrame(rows)


def optimism_from_random_split(summary: pd.DataFrame,
                               metric: str = "pr_auc_mean") -> pd.DataFrame:
    """V1 minus the blocked schemes. This difference is a reportable result."""
    rows = []
    piv = summary.pivot_table(index="arm", columns="scheme", values=metric)
    if "V1_random" not in piv.columns:
        return pd.DataFrame()
    for sch in piv.columns:
        if sch == "V1_random":
            continue
        d = (piv["V1_random"] - piv[sch]).dropna()
        rows.append({"vs": sch, "mean_optimism": float(d.mean()),
                     "max_optimism": float(d.max()),
                     "interpretation": "how much a random split would have "
                                       "overstated performance"})
    return pd.DataFrame(rows)


def run_fusion_study(df: pd.DataFrame, feature_cols: list[str], cfg: Config,
                     splits, fit_predict_tabular, y_col: str = "y") -> pd.DataFrame:
    """Early vs late fusion on tabular learners.

    Intermediate fusion is the neural branch architecture and is evaluated in the
    deep-learning stage; it appears here only if that stage ran.
    """
    branches = {g: columns_for_groups(feature_cols, [g])
                for g in ("sar", "optical", "meteo", "terrain")}
    branches = {k: v for k, v in branches.items() if v}
    rows = []
    for sp in splits:
        tr, te = df.iloc[sp.train_idx], df.iloc[sp.test_idx]
        if tr[y_col].nunique() < 2 or te[y_col].nunique() < 2:
            continue
        y_tr, y_te = tr[y_col].to_numpy(), te[y_col].to_numpy()

        # EARLY: concatenate everything, one model
        allc = sorted({c for v in branches.values() for c in v})
        p_early = fit_predict_tabular(tr[allc].to_numpy(), y_tr, te[allc].to_numpy())
        rows.append({"strategy": "early", "fold": sp.name, "scheme": sp.scheme,
                     **M.score(y_te, p_early).as_dict()})

        # LATE: one model per branch, average the probabilities
        preds = []
        for cols in branches.values():
            preds.append(fit_predict_tabular(tr[cols].to_numpy(), y_tr,
                                             te[cols].to_numpy()))
        p_late = np.mean(preds, axis=0)
        rows.append({"strategy": "late", "fold": sp.name, "scheme": sp.scheme,
                     **M.score(y_te, p_late).as_dict()})
    return pd.DataFrame(rows)

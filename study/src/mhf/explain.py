"""Explainability: SHAP, grouped permutation importance, ALE.

Grouped importance matters more than per-feature importance here, because
lagged rainfall variables are strongly correlated and individual attributions
are unstable under collinearity. ALE is used instead of partial dependence for
the same reason.

Every function returns a frame; plotting lives in mhf.figures so the analysis
is testable without a display.
"""
from __future__ import annotations

import warnings

import numpy as np
import pandas as pd
from sklearn.inspection import permutation_importance

try:
    import shap
    HAS_SHAP = True
except Exception:                                    # pragma: no cover
    HAS_SHAP = False

from .evaluation.ablation import GROUP_PREFIXES, columns_for_groups


# Models are fitted on plain arrays but explained with named frames, so that
# importances can be reported per feature. sklearn warns about the mismatch on
# every call; the mismatch is intentional and the warning is noise.
warnings.filterwarnings(
    "ignore", message="X has feature names, but .* was fitted without")


def shap_values(model, X: pd.DataFrame, max_rows: int = 2000,
                seed: int = 0) -> pd.DataFrame | None:
    if not HAS_SHAP:
        return None
    Xs = X.sample(min(len(X), max_rows), random_state=seed) if len(X) > max_rows else X
    est = model.named_steps["est"] if hasattr(model, "named_steps") else model
    try:
        Xt = (model[:-1].transform(Xs) if hasattr(model, "named_steps") else Xs.to_numpy())
        expl = shap.TreeExplainer(est)
        vals = expl.shap_values(Xt)
        # Binary classifiers return the positive-class slice in different
        # shapes across shap versions: a 2-element list (older), a 3-D
        # (n, features, classes) array (>=0.45), or a plain 2-D array.
        if isinstance(vals, list):
            vals = vals[1] if len(vals) > 1 else vals[0]
        vals = np.asarray(vals)
        if vals.ndim == 3:
            vals = vals[..., -1]
    except Exception:
        # Model-agnostic fallback. It is orders of magnitude slower than the
        # tree path, so it is deliberately starved of rows: a coarse estimate
        # that returns is worth more here than an exact one that never does.
        try:
            bg = shap.sample(Xs, min(50, len(Xs)), random_state=seed)
            Xf = Xs.sample(min(len(Xs), 200), random_state=seed)
            expl = shap.Explainer(model.predict_proba, bg)
            vals = expl(Xf).values[..., 1]
            Xs = Xf
        except Exception:
            return None
    return pd.DataFrame(np.asarray(vals), columns=Xs.columns, index=Xs.index)


def global_importance(sv: pd.DataFrame) -> pd.DataFrame:
    imp = sv.abs().mean().sort_values(ascending=False)
    return pd.DataFrame({"feature": imp.index, "mean_abs_shap": imp.to_numpy()})


def grouped_importance(sv: pd.DataFrame) -> pd.DataFrame:
    """Modality-level importance. Robust to within-group collinearity."""
    rows = []
    cols = list(sv.columns)
    for g in GROUP_PREFIXES:
        gc = columns_for_groups(cols, [g])
        if gc:
            rows.append({"group": g, "n_features": len(gc),
                         "mean_abs_shap": float(sv[gc].abs().sum(axis=1).mean())})
    out = pd.DataFrame(rows).sort_values("mean_abs_shap", ascending=False)
    if not out.empty:
        out["share"] = out["mean_abs_shap"] / out["mean_abs_shap"].sum()
    return out


def grouped_permutation_importance(model, X: pd.DataFrame, y: np.ndarray,
                                   scoring: str = "average_precision",
                                   n_repeats: int = 10, seed: int = 0
                                   ) -> pd.DataFrame:
    """Permute a whole modality at once -- the honest way to ask
    'what would we lose if this data source disappeared?'"""
    from sklearn.metrics import average_precision_score
    rng = np.random.default_rng(seed)
    base = average_precision_score(y, model.predict_proba(X)[:, 1])
    rows = []
    for g in GROUP_PREFIXES:
        gc = columns_for_groups(list(X.columns), [g])
        if not gc:
            continue
        drops = []
        for _ in range(n_repeats):
            Xp = X.copy()
            idx = rng.permutation(len(Xp))
            Xp[gc] = Xp[gc].to_numpy()[idx]
            drops.append(base - average_precision_score(
                y, model.predict_proba(Xp)[:, 1]))
        rows.append({"group": g, "n_features": len(gc),
                     "importance_drop_pr_auc": float(np.mean(drops)),
                     "sd": float(np.std(drops, ddof=1)) if n_repeats > 1 else 0.0})
    return pd.DataFrame(rows).sort_values("importance_drop_pr_auc", ascending=False)


def ale_1d(model, X: pd.DataFrame, feature: str, n_bins: int = 20) -> pd.DataFrame:
    """Accumulated local effects. Correct under correlated predictors, where
    partial dependence is not."""
    x = X[feature].to_numpy()
    finite = np.isfinite(x)
    if finite.sum() < 50:
        return pd.DataFrame()
    qs = np.unique(np.quantile(x[finite], np.linspace(0, 1, n_bins + 1)))
    if len(qs) < 3:
        return pd.DataFrame()
    eff = []
    for i in range(len(qs) - 1):
        m = (x >= qs[i]) & (x <= qs[i + 1]) & finite
        if m.sum() < 5:
            eff.append(0.0)
            continue
        lo, hi = X[m].copy(), X[m].copy()
        lo[feature], hi[feature] = qs[i], qs[i + 1]
        d = (model.predict_proba(hi)[:, 1] - model.predict_proba(lo)[:, 1])
        eff.append(float(np.mean(d)))
    ale = np.cumsum(eff)
    ale = ale - ale.mean()
    return pd.DataFrame({"feature": feature,
                         "bin_left": qs[:-1], "bin_right": qs[1:], "ale": ale})


def horizon_comparison(imp7: pd.DataFrame, imp14: pd.DataFrame) -> pd.DataFrame:
    """Does the driver of the forecast change with lead time?

    The scientific question this answers: rainfall is expected to dominate the
    7-day horizon while slower-moving terrain and persistence terms should
    matter more at 14 days. Reported, not assumed.
    """
    a = imp7.set_index("group")["share"].rename("share_7d")
    b = imp14.set_index("group")["share"].rename("share_14d")
    out = pd.concat([a, b], axis=1).fillna(0.0)
    out["delta_14d_minus_7d"] = out["share_14d"] - out["share_7d"]
    return out.reset_index().sort_values("delta_14d_minus_7d", ascending=False)


def lag_profile(sv: pd.DataFrame, stem: str = "RAIN_") -> pd.DataFrame:
    """Which antecedent window carries the signal? Answers questions like
    'does rainfall 3-7 days back matter more than yesterday's rain?'"""
    import re
    rows = []
    for c in sv.columns:
        if not c.startswith(stem):
            continue
        m = re.search(r"(\d+)D", c)
        if m:
            rows.append({"feature": c, "window_days": int(m.group(1)),
                         "mean_abs_shap": float(sv[c].abs().mean())})
    df = pd.DataFrame(rows)
    return df.sort_values("window_days") if not df.empty else df

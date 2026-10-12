"""Publication figures.

Analysis lives elsewhere; this module only draws. Every function takes a frame
that has already been computed and returns the path it wrote, or None if
matplotlib is unavailable — a missing plotting library must never stop a run
from producing its numbers.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    HAS_MPL = True
except Exception:                                    # pragma: no cover
    HAS_MPL = False

DPI = 300


def _save(fig, outdir: Path, name: str) -> str:
    outdir.mkdir(parents=True, exist_ok=True)
    p = outdir / f"{name}.png"
    fig.savefig(p, dpi=DPI, bbox_inches="tight")
    plt.close(fig)
    return str(p)


def precision_recall_curve(y: np.ndarray, p: np.ndarray, base_rate: float,
                           outdir: Path, name: str) -> str | None:
    """PR curve against the base rate, which is the only honest reference line
    for a rare event."""
    if not HAS_MPL:
        return None
    from sklearn.metrics import average_precision_score, precision_recall_curve as prc
    pr, rc, _ = prc(y, p)
    ap = average_precision_score(y, p)
    fig, ax = plt.subplots(figsize=(5, 4))
    ax.plot(rc, pr, lw=2, label=f"model (PR-AUC = {ap:.3f})")
    ax.axhline(base_rate, ls="--", c="0.4", lw=1,
               label=f"base rate = {base_rate:.3f}")
    ax.set_xlabel("Recall")
    ax.set_ylabel("Precision")
    ax.set_ylim(0, 1)
    ax.set_xlim(0, 1)
    ax.legend(frameon=False, fontsize=8)
    ax.set_title(name.replace("_", " "))
    return _save(fig, outdir, f"pr_curve_{name}")


def reliability_diagram(rel: pd.DataFrame, outdir: Path, name: str) -> str | None:
    if not HAS_MPL or rel.empty:
        return None
    fig, ax = plt.subplots(figsize=(4.2, 4))
    ax.plot([0, 1], [0, 1], ls="--", c="0.5", lw=1, label="perfect calibration")
    ax.plot(rel["mean_predicted"], rel["observed_frequency"], "o-", lw=1.6,
            label="model")
    for _, r in rel.iterrows():
        ax.annotate(f"{int(r['n'])}", (r["mean_predicted"], r["observed_frequency"]),
                    fontsize=6, xytext=(2, 3), textcoords="offset points")
    ax.set_xlabel("Mean predicted probability")
    ax.set_ylabel("Observed frequency")
    ax.legend(frameon=False, fontsize=8)
    ax.set_title(f"Reliability — {name.replace('_', ' ')}")
    return _save(fig, outdir, f"reliability_{name}")


def ablation_bars(summary: pd.DataFrame, outdir: Path, name: str,
                  metric: str = "pr_auc_mean") -> str | None:
    if not HAS_MPL or summary.empty:
        return None
    piv = summary.pivot_table(index="arm", columns="scheme", values=metric)
    fig, ax = plt.subplots(figsize=(6.5, 0.45 * len(piv) + 1.6))
    piv.plot.barh(ax=ax, width=0.8)
    ax.set_xlabel("PR-AUC (mean across folds)")
    ax.set_ylabel("")
    ax.legend(frameon=False, fontsize=8, title="validation scheme",
              title_fontsize=8)
    ax.set_title(f"Ablation — {name.replace('_', ' ')}")
    return _save(fig, outdir, f"ablation_{name}")


def grouped_importance_bars(imp: pd.DataFrame, outdir: Path, name: str,
                            value_col: str = "importance_drop_pr_auc"
                            ) -> str | None:
    if not HAS_MPL or imp.empty or value_col not in imp.columns:
        return None
    d = imp.sort_values(value_col)
    fig, ax = plt.subplots(figsize=(5.2, 0.5 * len(d) + 1.4))
    err = d["sd"] if "sd" in d.columns else None
    ax.barh(d["group"], d[value_col], xerr=err, color="#3b6ea5")
    ax.set_xlabel("Loss in PR-AUC when the modality is permuted")
    ax.set_title(f"Modality importance — {name.replace('_', ' ')}")
    return _save(fig, outdir, f"importance_{name}")


def cv_scheme_comparison(cv: pd.DataFrame, outdir: Path, name: str,
                         metric: str = "pr_auc") -> str | None:
    """The optimism figure: what a random split would have claimed versus what
    blocked validation actually supports."""
    if not HAS_MPL or cv.empty:
        return None
    d = cv[cv["model"] != "baseline_majority"]
    if d.empty:
        return None
    piv = d.pivot_table(index="model", columns="scheme", values=metric)
    fig, ax = plt.subplots(figsize=(6, 0.6 * len(piv) + 1.8))
    piv.plot.bar(ax=ax, width=0.8, rot=20)
    ax.set_ylabel(metric.replace("_", "-").upper())
    ax.set_xlabel("")
    ax.legend(frameon=False, fontsize=8, title="validation scheme",
              title_fontsize=8)
    ax.set_title(f"Validation protocol comparison — {name.replace('_', ' ')}")
    return _save(fig, outdir, f"cv_schemes_{name}")


def lag_profile_plot(lp: pd.DataFrame, outdir: Path, name: str) -> str | None:
    if not HAS_MPL or lp.empty:
        return None
    d = lp.groupby("window_days")["mean_abs_shap"].mean().reset_index()
    fig, ax = plt.subplots(figsize=(5, 3.4))
    ax.plot(d["window_days"], d["mean_abs_shap"], "o-")
    ax.set_xlabel("Antecedent window (days)")
    ax.set_ylabel("Mean |SHAP|")
    ax.set_title(f"Rainfall lag profile — {name.replace('_', ' ')}")
    return _save(fig, outdir, f"lag_profile_{name}")


def risk_map(prod: pd.DataFrame, outdir: Path, name: str) -> str | None:
    """Site-level forecast probability in geographic coordinates. Not a
    interpolated surface: each point is a sampled site, and nothing between
    them is claimed."""
    if not HAS_MPL or prod.empty:
        return None
    if not {"latitude", "longitude", "probability"}.issubset(prod.columns):
        return None
    d = (prod.groupby(["site_id", "latitude", "longitude"], as_index=False)
             ["probability"].max())
    fig, ax = plt.subplots(figsize=(5.4, 5))
    sc = ax.scatter(d["longitude"], d["latitude"], c=d["probability"],
                    cmap="YlOrRd", s=18, vmin=0, vmax=max(0.2, d["probability"].max()),
                    edgecolors="none")
    fig.colorbar(sc, ax=ax, label="max forecast probability over test window")
    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")
    ax.set_aspect("equal", adjustable="datalim")
    ax.set_title(f"Forecast surface — {name.replace('_', ' ')}")
    return _save(fig, outdir, f"risk_map_{name}")


def label_timeline(df: pd.DataFrame, outdir: Path, name: str) -> str | None:
    """When formation events actually happen, against rainfall. Establishes
    that the positive class is seasonal, not uniform."""
    if not HAS_MPL or df.empty:
        return None
    d = df.groupby("date").agg(events=("y", "sum"), n=("y", "size"))
    if "RAIN_7D" in df.columns:
        d["rain"] = df.groupby("date")["RAIN_7D"].mean()
    fig, ax = plt.subplots(figsize=(8, 3.2))
    ax.bar(d.index, d["events"], width=3.0, color="#3b6ea5", label="formation events")
    ax.set_ylabel("Sites with new water at t+h")
    if "rain" in d.columns:
        ax2 = ax.twinx()
        ax2.plot(d.index, d["rain"], c="#c44e52", lw=1.0, alpha=0.8,
                 label="7-day antecedent rainfall")
        ax2.set_ylabel("7-day rainfall (mm)")
    ax.set_title(f"Event timeline — {name.replace('_', ' ')}")
    fig.autofmt_xdate()
    return _save(fig, outdir, f"timeline_{name}")

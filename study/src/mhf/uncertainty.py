"""Calibration, uncertainty and conformal prediction.

Operational vector control needs to know not only "how likely" but "how sure".
A 0.68 with wide uncertainty and a 0.68 with narrow uncertainty warrant
different decisions, so probability and uncertainty are always emitted together.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.calibration import IsotonicRegression
from sklearn.linear_model import LogisticRegression


# ------------------------------------------------------------------ calibration
def fit_calibrator(p_val: np.ndarray, y_val: np.ndarray, method: str = "isotonic"):
    """Fitted on VALIDATION only. Never on test."""
    if method == "none":
        return None
    if method == "isotonic":
        ir = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
        ir.fit(p_val, y_val)
        return ir
    if method == "platt":
        lr = LogisticRegression(max_iter=1000)
        lr.fit(p_val.reshape(-1, 1), y_val)
        return lr
    raise ValueError(method)


def apply_calibrator(cal, p: np.ndarray) -> np.ndarray:
    if cal is None:
        return p
    if isinstance(cal, IsotonicRegression):
        return np.clip(cal.predict(p), 0.0, 1.0)
    return cal.predict_proba(p.reshape(-1, 1))[:, 1]


def reliability_table(y: np.ndarray, p: np.ndarray, n_bins: int = 10) -> pd.DataFrame:
    edges = np.linspace(0, 1, n_bins + 1)
    idx = np.clip(np.digitize(p, edges[1:-1]), 0, n_bins - 1)
    rows = []
    for b in range(n_bins):
        m = idx == b
        if not m.any():
            continue
        rows.append({"bin": b, "lo": edges[b], "hi": edges[b + 1],
                     "n": int(m.sum()), "mean_predicted": float(p[m].mean()),
                     "observed_frequency": float(y[m].mean()),
                     "gap": float(p[m].mean() - y[m].mean())})
    return pd.DataFrame(rows)


def brier_decomposition(y: np.ndarray, p: np.ndarray, n_bins: int = 10) -> dict:
    """Murphy decomposition: Brier = reliability - resolution + uncertainty."""
    edges = np.linspace(0, 1, n_bins + 1)
    idx = np.clip(np.digitize(p, edges[1:-1]), 0, n_bins - 1)
    ybar = float(y.mean())
    rel = res = 0.0
    n = len(y)
    for b in range(n_bins):
        m = idx == b
        if not m.any():
            continue
        nk, pk, ok = int(m.sum()), float(p[m].mean()), float(y[m].mean())
        rel += nk * (pk - ok) ** 2
        res += nk * (ok - ybar) ** 2
    return {"brier": float(np.mean((p - y) ** 2)),
            "reliability_MCB": rel / n, "resolution_DSC": res / n,
            "uncertainty_UNC": ybar * (1 - ybar)}


# ------------------------------------------------------------------- ensembles
def ensemble_uncertainty(prob_matrix: np.ndarray) -> dict[str, np.ndarray]:
    """prob_matrix: (n_models, n_samples)."""
    mean = prob_matrix.mean(axis=0)
    return {"probability": mean,
            "std": prob_matrix.std(axis=0),
            "range": prob_matrix.max(axis=0) - prob_matrix.min(axis=0),
            "entropy": -(mean * np.log(np.clip(mean, 1e-9, 1))
                         + (1 - mean) * np.log(np.clip(1 - mean, 1e-9, 1)))}


# ------------------------------------------------------------------- conformal
def mondrian_conformal(p_cal: np.ndarray, y_cal: np.ndarray, p_test: np.ndarray,
                       alpha: float = 0.1) -> pd.DataFrame:
    """Class-conditional conformal prediction sets with finite-sample coverage.

    Returns, for each test row, whether each label is in the prediction set.
    A set of size 2 ({0,1}) means the model genuinely cannot discriminate -- an
    honest "I do not know" that a bare probability hides.
    """
    out = {}
    for cls in (0, 1):
        scores = 1.0 - (p_cal if cls == 1 else 1.0 - p_cal)
        s_cls = scores[y_cal == cls]
        if len(s_cls) < 10:
            out[cls] = np.ones(len(p_test), dtype=bool)
            continue
        k = int(np.ceil((len(s_cls) + 1) * (1 - alpha))) - 1
        q = np.sort(s_cls)[min(k, len(s_cls) - 1)]
        s_test = 1.0 - (p_test if cls == 1 else 1.0 - p_test)
        out[cls] = s_test <= q
    df = pd.DataFrame({"in_set_0": out[0], "in_set_1": out[1],
                       "probability": p_test})
    df["set_size"] = df["in_set_0"].astype(int) + df["in_set_1"].astype(int)
    df["decision"] = np.select(
        [(df.set_size == 1) & df.in_set_1, (df.set_size == 1) & df.in_set_0,
         df.set_size == 2],
        ["formation", "no_formation", "uncertain"], default="empty")
    return df


def empirical_coverage(conf: pd.DataFrame, y_true: np.ndarray) -> dict:
    covered = np.where(y_true == 1, conf["in_set_1"], conf["in_set_0"])
    return {"empirical_coverage": float(covered.mean()),
            "mean_set_size": float(conf["set_size"].mean()),
            "pct_uncertain": float((conf["set_size"] == 2).mean())}


def uncertainty_band(std: np.ndarray, q_low: float = 0.33,
                     q_high: float = 0.66) -> np.ndarray:
    lo, hi = np.quantile(std, [q_low, q_high])
    return np.select([std <= lo, std <= hi], ["low", "medium"], default="high")

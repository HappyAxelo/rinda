"""Metrics. Accuracy is reported but never alone, and never first.

The 99% question is handled explicitly by `accuracy_is_trivial`, which compares
a model's accuracy against the majority-class baseline. If a model reaches high
accuracy only by predicting "no formation" everywhere, the report says so in
those words.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd
from sklearn.metrics import (accuracy_score, average_precision_score,
                             balanced_accuracy_score, brier_score_loss,
                             confusion_matrix, f1_score, log_loss,
                             matthews_corrcoef, precision_score, recall_score,
                             roc_auc_score)


@dataclass
class Scores:
    n: int
    n_positive: int
    base_rate: float
    threshold: float
    accuracy: float
    majority_accuracy: float
    accuracy_lift: float
    precision: float
    recall: float
    specificity: float
    f1: float
    balanced_accuracy: float
    roc_auc: float
    pr_auc: float
    pr_auc_over_base: float
    mcc: float
    brier: float
    log_loss: float
    ece: float
    tn: int
    fp: int
    fn: int
    tp: int
    number_needed_to_treat: float
    trivial_accuracy_warning: str

    def as_dict(self) -> dict:
        return asdict(self)


def expected_calibration_error(y: np.ndarray, p: np.ndarray,
                               n_bins: int = 10) -> float:
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    idx = np.clip(np.digitize(p, edges[1:-1]), 0, n_bins - 1)
    ece = 0.0
    for b in range(n_bins):
        m = idx == b
        if not m.any():
            continue
        ece += (m.mean()) * abs(y[m].mean() - p[m].mean())
    return float(ece)


def accuracy_is_trivial(acc: float, base_rate: float,
                        recall: float, tol: float = 0.02) -> str:
    """The guard against a meaningless 99%."""
    majority = max(base_rate, 1.0 - base_rate)
    if acc >= 0.95 and recall < 0.5:
        return (f"HIGH ACCURACY IS MISLEADING: accuracy {acc:.3f} with recall "
                f"{recall:.3f}. The model is mostly predicting the majority "
                f"class (base rate {base_rate:.4f}). Do not report this as skill.")
    if acc <= majority + tol:
        return (f"NO SKILL OVER MAJORITY CLASS: accuracy {acc:.3f} vs majority "
                f"baseline {majority:.3f}. Accuracy is uninformative here; "
                f"use PR-AUC and MCC.")
    return ""


def score(y_true: np.ndarray, p: np.ndarray, threshold: float = 0.5) -> Scores:
    y = np.asarray(y_true).astype(int)
    p = np.asarray(p, dtype=float)
    yhat = (p >= threshold).astype(int)

    base = float(y.mean()) if len(y) else float("nan")
    majority = max(base, 1.0 - base) if len(y) else float("nan")
    acc = accuracy_score(y, yhat)
    rec = recall_score(y, yhat, zero_division=0)
    prec = precision_score(y, yhat, zero_division=0)

    labels = [0, 1]
    tn, fp, fn, tp = confusion_matrix(y, yhat, labels=labels).ravel()
    spec = tn / (tn + fp) if (tn + fp) else float("nan")

    two_class = y.min() != y.max()
    roc = roc_auc_score(y, p) if two_class else float("nan")
    ap = average_precision_score(y, p) if two_class else float("nan")

    return Scores(
        n=len(y), n_positive=int(y.sum()), base_rate=base, threshold=threshold,
        accuracy=float(acc), majority_accuracy=float(majority),
        accuracy_lift=float(acc - majority),
        precision=float(prec), recall=float(rec), specificity=float(spec),
        f1=float(f1_score(y, yhat, zero_division=0)),
        balanced_accuracy=float(balanced_accuracy_score(y, yhat)),
        roc_auc=float(roc), pr_auc=float(ap),
        pr_auc_over_base=float(ap / base) if two_class and base > 0 else float("nan"),
        mcc=float(matthews_corrcoef(y, yhat)) if two_class else float("nan"),
        brier=float(brier_score_loss(y, p)) if len(y) else float("nan"),
        log_loss=float(log_loss(y, np.clip(p, 1e-7, 1 - 1e-7), labels=labels))
        if two_class else float("nan"),
        ece=expected_calibration_error(y, p),
        tn=int(tn), fp=int(fp), fn=int(fn), tp=int(tp),
        number_needed_to_treat=float(1.0 / prec) if prec > 0 else float("inf"),
        trivial_accuracy_warning=accuracy_is_trivial(acc, base, rec),
    )


def optimise_threshold(y: np.ndarray, p: np.ndarray,
                       objective: str = "f1") -> float:
    """Chosen on VALIDATION data only. Never on test."""
    grid = np.unique(np.quantile(p, np.linspace(0.01, 0.99, 99)))
    best, best_t = -np.inf, 0.5
    for t in grid:
        yhat = (p >= t).astype(int)
        if objective == "f1":
            v = f1_score(y, yhat, zero_division=0)
        elif objective == "mcc":
            v = matthews_corrcoef(y, yhat) if y.min() != y.max() else -1
        elif objective == "balanced_accuracy":
            v = balanced_accuracy_score(y, yhat)
        else:
            raise ValueError(objective)
        if v > best:
            best, best_t = v, float(t)
    return best_t


def bootstrap_ci(y: np.ndarray, p: np.ndarray, groups: np.ndarray,
                 metric: str = "pr_auc", n: int = 1000,
                 ci: float = 0.95, seed: int = 0) -> dict:
    """Resample GROUPS (spatial blocks), not rows, so dependence is respected."""
    rng = np.random.default_rng(seed)
    uniq = np.unique(groups)
    vals = []
    for _ in range(n):
        pick = rng.choice(uniq, size=len(uniq), replace=True)
        idx = np.concatenate([np.where(groups == g)[0] for g in pick])
        yy, pp = y[idx], p[idx]
        if yy.min() == yy.max():
            continue
        if metric == "pr_auc":
            vals.append(average_precision_score(yy, pp))
        elif metric == "roc_auc":
            vals.append(roc_auc_score(yy, pp))
        else:
            raise ValueError(metric)
    if not vals:
        return {"metric": metric, "mean": float("nan"), "lo": float("nan"),
                "hi": float("nan"), "n_resamples": 0}
    a = (1 - ci) / 2
    return {"metric": metric, "mean": float(np.mean(vals)),
            "sd": float(np.std(vals, ddof=1)),
            "lo": float(np.quantile(vals, a)),
            "hi": float(np.quantile(vals, 1 - a)),
            "n_resamples": len(vals)}


def paired_test(p_a: np.ndarray, p_b: np.ndarray, y: np.ndarray,
                groups: np.ndarray, metric: str = "pr_auc",
                n: int = 1000, seed: int = 0) -> dict:
    """Block-bootstrap paired difference between two models on the same rows."""
    rng = np.random.default_rng(seed)
    uniq = np.unique(groups)
    diffs = []
    fn = average_precision_score if metric == "pr_auc" else roc_auc_score
    for _ in range(n):
        pick = rng.choice(uniq, size=len(uniq), replace=True)
        idx = np.concatenate([np.where(groups == g)[0] for g in pick])
        yy = y[idx]
        if yy.min() == yy.max():
            continue
        diffs.append(fn(yy, p_a[idx]) - fn(yy, p_b[idx]))
    if not diffs:
        return {"delta": float("nan"), "p_two_sided": float("nan")}
    d = np.array(diffs)
    p_val = 2 * min((d <= 0).mean(), (d >= 0).mean())
    return {"metric": metric, "delta": float(d.mean()),
            "lo": float(np.quantile(d, 0.025)), "hi": float(np.quantile(d, 0.975)),
            "p_two_sided": float(min(1.0, p_val)), "n_resamples": len(d)}


def stratified_scores(df: pd.DataFrame, y_col: str, p_col: str,
                      by: list[str], threshold: float = 0.5) -> pd.DataFrame:
    rows = []
    for keys, g in df.groupby(by, dropna=False):
        if len(g) < 20 or g[y_col].nunique() < 2:
            continue
        s = score(g[y_col].to_numpy(), g[p_col].to_numpy(), threshold)
        rec = dict(zip(by, keys if isinstance(keys, tuple) else (keys,)))
        rows.append({**rec, **s.as_dict()})
    return pd.DataFrame(rows)

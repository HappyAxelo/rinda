"""Tabular model registry. Optional dependencies degrade gracefully."""
from __future__ import annotations

from typing import Any

import numpy as np
from sklearn.dummy import DummyClassifier
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.tree import DecisionTreeClassifier

try:
    from xgboost import XGBClassifier
    HAS_XGB = True
except Exception:                                    # pragma: no cover
    HAS_XGB = False

try:
    from lightgbm import LGBMClassifier
    HAS_LGBM = True
except Exception:                                    # pragma: no cover
    HAS_LGBM = False


def _imputed(est) -> Pipeline:
    """Median imputation INSIDE the pipeline so it is fitted on train folds only."""
    return Pipeline([("impute", SimpleImputer(strategy="median")), ("est", est)])


def _scaled(est) -> Pipeline:
    return Pipeline([("impute", SimpleImputer(strategy="median")),
                     ("scale", StandardScaler()), ("est", est)])


def build_model(name: str, cfg, n_pos: int, n_neg: int) -> Any:
    seed = cfg["project"]["seed"]
    strategy = cfg.get("models.imbalance.strategy", "class_weight")
    cw = "balanced" if strategy == "class_weight" else None
    spw = max(1.0, n_neg / max(n_pos, 1)) if strategy == "class_weight" else 1.0

    # 'fast' is for smoke tests and CI only. Reported runs must use 'full'.
    fast = cfg.get("models.profile", "full") == "fast"
    n_rf = 150 if fast else 600
    n_gb = 200 if fast else 800
    n_lgb = 300 if fast else 1200

    if name == "baseline_majority":
        return DummyClassifier(strategy="prior")
    if name == "baseline_prevalence":
        return DummyClassifier(strategy="stratified", random_state=seed)
    if name == "logistic_regression":
        return _scaled(LogisticRegression(max_iter=2000, class_weight=cw,
                                          C=1.0, random_state=seed))
    if name == "decision_tree":
        return _imputed(DecisionTreeClassifier(max_depth=6, min_samples_leaf=50,
                                               class_weight=cw, random_state=seed))
    if name == "random_forest":
        return _imputed(RandomForestClassifier(
            n_estimators=n_rf, max_depth=None, min_samples_leaf=10,
            max_features="sqrt", class_weight=cw, n_jobs=-1, random_state=seed))
    if name == "xgboost":
        if not HAS_XGB:
            return None
        return _imputed(XGBClassifier(
            n_estimators=n_gb, learning_rate=0.03, max_depth=5,
            subsample=0.8, colsample_bytree=0.8, reg_lambda=1.0,
            min_child_weight=5, scale_pos_weight=spw, eval_metric="aucpr",
            tree_method="hist", n_jobs=-1, random_state=seed))
    if name == "lightgbm":
        if not HAS_LGBM:
            return None
        return _imputed(LGBMClassifier(
            n_estimators=n_lgb, learning_rate=0.03, num_leaves=31,
            min_child_samples=30, subsample=0.8, colsample_bytree=0.8,
            reg_lambda=1.0, class_weight=cw, n_jobs=-1, random_state=seed,
            verbose=-1))
    raise ValueError(f"unknown model: {name}")


def available_tabular(cfg) -> list[str]:
    wanted = ["baseline_majority"] + list(cfg.get("models.tabular", []))
    out = []
    for n in wanted:
        if n == "xgboost" and not HAS_XGB:
            continue
        if n == "lightgbm" and not HAS_LGBM:
            continue
        out.append(n)
    return out


def predict_proba(model, X: np.ndarray) -> np.ndarray:
    p = model.predict_proba(X)
    return p[:, 1] if p.ndim == 2 and p.shape[1] > 1 else p.ravel()

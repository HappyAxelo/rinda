"""Version-3 model: the v2 feature set and tuned ensemble, refit on a fixed cadence.

v2 froze one model and kept it. v3 keeps the v2 design (geometry-aware radar history,
terrain, Sentinel-2, XGBoost + LightGBM) but treats the model as perishable: it is refit
every `every_days` on every outcome that is observable at the refit date, and its
calibration, alert thresholds and conformal pool are re-estimated from the model's own
earlier out-of-sample forecasts. The class lives in the package (not in a script) so
the RINDA console can unpickle it.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import average_precision_score

SEED = 20260909
PRECISION_TARGET = 0.75          # high-precision alert level, fixed before any v3 result was seen


def make(kind: str, params: dict, n_pos: int, n_neg: int, seed: int = SEED):
    p = dict(params or {})
    spw = max(1.0, n_neg / max(n_pos, 1)) ** p.pop("spw_power", 0.5)
    p.pop("tau", None)
    if kind == "xgb":
        from xgboost import XGBClassifier
        base = dict(n_estimators=600, learning_rate=0.03, max_depth=5, subsample=0.8, colsample_bytree=0.6,
                    min_child_weight=5, reg_lambda=1.0)
        base.update(p)
        return XGBClassifier(tree_method="hist", eval_metric="aucpr", n_jobs=-1, random_state=seed,
                             scale_pos_weight=spw, **base)
    from lightgbm import LGBMClassifier
    base = dict(n_estimators=600, learning_rate=0.03, num_leaves=31, min_child_samples=30, subsample=0.8,
                subsample_freq=1, colsample_bytree=0.6, reg_lambda=1.0)
    base.update(p)
    return LGBMClassifier(scale_pos_weight=spw, n_jobs=-1, random_state=seed, verbose=-1, **base)


def recency_weights(dates: pd.Series, tau) -> np.ndarray | None:
    if not tau:
        return None
    return np.exp(-(dates.max() - dates).dt.days.to_numpy(float) / tau)


@dataclass
class Ensemble:
    """Mean probability of XGBoost and LightGBM members (optionally several seeds each)."""
    kinds: list[str]
    params: dict
    cols: list[str]
    seeds: tuple[int, ...] = (SEED,)
    members: list = field(default_factory=list)

    def fit(self, d: pd.DataFrame) -> "Ensemble":
        X, y = d[self.cols].to_numpy(float), d.y.to_numpy()
        self.members = []
        for k in self.kinds:
            p = dict(self.params[k])
            w = recency_weights(d.date, p.get("tau"))
            for s in self.seeds:
                m = make(k, p, int(y.sum()), int((1 - y).sum()), seed=s)
                m.fit(X, y, sample_weight=w)
                self.members.append(m)
        return self

    def raw(self, X: pd.DataFrame) -> np.ndarray:
        A = X[self.cols].to_numpy(float)
        return np.mean([m.predict_proba(A)[:, 1] for m in self.members], 0)

    def contributions(self, X: pd.DataFrame) -> np.ndarray:
        """Mean per-feature log-odds contributions of the members (last column = bias)."""
        A = X[self.cols].to_numpy(float)
        out = []
        for m in self.members:
            if m.__class__.__name__.startswith("XGB"):
                import xgboost as xgb
                out.append(m.get_booster().predict(xgb.DMatrix(A), pred_contribs=True))
            else:
                out.append(m.predict(A, pred_contrib=True))
        return np.mean(out, 0)


def threshold_f1(y: np.ndarray, p: np.ndarray) -> float:
    best, thr = -1.0, 0.5
    for t in np.unique(np.round(p, 4)):
        c = p >= t
        tp = (c & (y == 1)).sum(); fp = (c & (y == 0)).sum(); fn = (~c & (y == 1)).sum()
        f = 2 * tp / max(2 * tp + fp + fn, 1)
        if f > best:
            best, thr = f, float(t)
    return thr


def threshold_precision(y: np.ndarray, p: np.ndarray, target: float = PRECISION_TARGET, min_calls: int = 30) -> float:
    """Lowest threshold whose precision on (y, p) reaches `target` with at least `min_calls` calls."""
    order = np.argsort(-p)
    ps, ys = p[order], y[order]
    tp = np.cumsum(ys); n = np.arange(1, len(ys) + 1)
    prec = tp / n
    ok = np.where((prec >= target) & (n >= min_calls))[0]
    if not len(ok):
        return float(ps[min(min_calls, len(ps)) - 1]) if len(ps) else 1.0
    return float(ps[ok.max()])


@dataclass
class Recalibration:
    """Isotonic calibration + alert thresholds + conformal pool, estimated from earlier
    out-of-sample forecasts whose outcomes were observable at the refit date."""
    cal: IsotonicRegression
    thr_f1: float
    thr_precision: float
    pool_p: np.ndarray
    pool_y: np.ndarray
    n_rows: int
    window: tuple[str, str]

    @classmethod
    def from_history(cls, hist: pd.DataFrame, lookback_days: int = 365, pool_days: int = 120) -> "Recalibration":
        end = hist.date.max()
        h = hist[hist.date > end - pd.Timedelta(days=lookback_days)]
        cal = IsotonicRegression(out_of_bounds="clip", y_min=0, y_max=1).fit(h.raw, h.y)
        pc = cal.predict(h.raw)
        recent = h.date > end - pd.Timedelta(days=pool_days)
        return cls(cal=cal, thr_f1=threshold_f1(h.y.to_numpy(), pc),
                   thr_precision=threshold_precision(h.y.to_numpy(), pc),
                   pool_p=pc[recent.to_numpy()], pool_y=h.y.to_numpy()[recent.to_numpy()],
                   n_rows=len(h), window=(str(h.date.min().date()), str(end.date())))

    def prob(self, raw: np.ndarray) -> np.ndarray:
        return self.cal.predict(raw)

    def conformal(self, p: np.ndarray, alpha: float = 0.1) -> np.ndarray:
        """Mondrian (class-conditional) split-conformal sets from the recent pool."""
        out = []
        q = {}
        for cls in (0, 1):
            sc = (1 - self.pool_p[self.pool_y == 1]) if cls == 1 else self.pool_p[self.pool_y == 0]
            k = int(np.ceil((len(sc) + 1) * (1 - alpha))) - 1
            q[cls] = np.sort(sc)[min(max(k, 0), len(sc) - 1)] if len(sc) >= 20 else np.inf
        in1 = (1 - p) <= q[1]; in0 = p <= q[0]
        out = np.select([in1 & ~in0, in0 & ~in1, in1 & in0], ["formation", "no_formation", "uncertain"], "empty")
        return out


@dataclass
class OperationalModel:
    """What RINDA serves: one refit ensemble plus its recalibration state."""
    horizon: int
    ensemble: Ensemble
    recal: Recalibration
    trained_to: str
    refit_every_days: int
    feature_families: dict
    evaluation: dict = field(default_factory=dict)
    version: str = "v3"

    @property
    def cols(self) -> list[str]:
        return self.ensemble.cols

    def prob(self, X: pd.DataFrame) -> np.ndarray:
        return self.recal.prob(self.ensemble.raw(X))


def ap(y, p) -> float:
    return float(average_precision_score(y, p))

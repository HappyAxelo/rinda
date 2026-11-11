"""Reproducible training on confirmed historical tables.

    python server/training.py <run_id>    (started by the API as a separate process)

Training runs out of process so it can never take the web server's memory. A run
ends as a "candidate" that nothing serves. Rules:

  * splits are by time, with an embargo of horizon + 2 days so no training row's
    outcome falls inside the next window;
  * calibration and the operating line are fitted only on the calibration window;
  * the test window is scored once, and also on spatial blocks held out of training;
  * every metric is reported beside simple baselines;
  * the same data, config and seed give the same prediction hash.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

os.environ.setdefault("ARROW_DEFAULT_MEMORY_POOL", "system")
sys.path.insert(0, str(Path(__file__).parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from db import now, tx  # noqa: E402
from engine import VAR  # noqa: E402

RUNS = VAR / "models" / "registry"
TOOL = Path(__file__).resolve()
TARGET_RULES = {
    "A": {"label": "New surface-water formation", "needs": None},
    "B": {"label": "Persistence of observed water", "needs": "A prepared table with a persistence label."},
    "C": {"label": "Field-verified water presence", "needs": "A prepared table built from verified field water records."},
    "D": {"label": "Entomologically confirmed mosquito breeding", "needs": "A prepared table built from RBC dipping records, including negatives."},
    "E": {"label": "Larval abundance or density", "needs": "A prepared table with larvae per dip, with sampling effort."},
}
EXCLUDE = {"y", "date", "site_id", "season", "rainfall_regime", "target", "origin", "stratum", "lat", "lon", "latitude", "longitude"}
BLOCKED_PREFIX = ("future_", "label_", "target_", "y_")


def temporal_splits(dates: pd.Series, horizon: int, train_end=None, cal_end=None) -> dict:
    """Train / calibrate / test masks with an embargo so outcomes never cross a boundary."""
    d = pd.to_datetime(dates)
    u = d.sort_values().unique()
    t_end = pd.Timestamp(train_end) if train_end else pd.Timestamp(u[int(len(u) * 0.6)])
    c_end = pd.Timestamp(cal_end) if cal_end else pd.Timestamp(u[int(len(u) * 0.8)])
    if not t_end < c_end:
        raise ValueError("The training window must end before the calibration window.")
    embargo = pd.Timedelta(days=int(horizon) + 2)
    train = d <= t_end - embargo
    cal = (d > t_end) & (d <= c_end - embargo)
    test = d > c_end
    if not (train.any() and cal.any() and test.any()):
        raise ValueError("A split is empty. Check the dates against the data range.")
    return {"train": train.to_numpy(), "cal": cal.to_numpy(), "test": test.to_numpy(), "train_end": str(t_end.date()), "cal_end": str(c_end.date()),
            "embargo_days": int(embargo.days), "train_last": str(d[train].max().date()), "cal_first": str(d[cal].min().date()),
            "cal_last": str(d[cal].max().date()), "test_first": str(d[test].min().date()), "test_last": str(d[test].max().date())}


def binary_metrics(y: np.ndarray, p: np.ndarray, thr: float) -> dict:
    from sklearn.metrics import average_precision_score, brier_score_loss, confusion_matrix, f1_score, precision_score, recall_score
    if len(np.unique(y)) < 2:
        return {"n": int(len(y)), "events": int(y.sum()), "note": "Only one class in this window."}
    call = (p >= thr).astype(int)
    tn, fp, fn, tp = confusion_matrix(y, call, labels=[0, 1]).ravel()
    return {"n": int(len(y)), "events": int(y.sum()), "base_rate": float(y.mean()), "pr_auc": float(average_precision_score(y, p)),
            "precision": float(precision_score(y, call, zero_division=0)), "recall": float(recall_score(y, call, zero_division=0)),
            "f1": float(f1_score(y, call, zero_division=0)), "brier": float(brier_score_loss(y, p)), "ece": expected_calibration_error(y, p),
            "confusion": {"tp": int(tp), "fp": int(fp), "fn": int(fn), "tn": int(tn)}, "threshold": float(thr)}


def expected_calibration_error(y: np.ndarray, p: np.ndarray, bins: int = 10) -> float:
    idx = np.minimum((p * bins).astype(int), bins - 1)
    return float(sum(abs(y[idx == b].mean() - p[idx == b].mean()) * (idx == b).mean() for b in range(bins) if (idx == b).any()))


def reliability(y: np.ndarray, p: np.ndarray, bins: int = 10) -> list[dict]:
    idx = np.minimum((p * bins).astype(int), bins - 1)
    return [{"bin": b / bins, "mean_predicted": float(p[idx == b].mean()), "observed": float(y[idx == b].mean()), "n": int((idx == b).sum())}
            for b in range(bins) if (idx == b).any()]


def best_f1_threshold(y: np.ndarray, p: np.ndarray) -> float:
    from sklearn.metrics import precision_recall_curve
    pr, re, th = precision_recall_curve(y, p)
    f = 2 * pr[:-1] * re[:-1] / np.maximum(pr[:-1] + re[:-1], 1e-9)
    return float(th[int(np.argmax(f))]) if len(th) else 0.5


def spatial_blocks(sites: pd.Series, n_folds: int = 3, seed: int = 42) -> pd.Series:
    """Deterministic assignment of each site's ~500 m cell to a fold."""
    from data import store
    s = store().sites.set_index("site_id")
    def block(sid):
        if sid in s.index:
            r = s.loc[sid]
            return f"{round(r.latitude / 0.0045)}_{round(r.longitude / 0.0045)}"
        return str(sid)
    cells = {x: block(x) for x in pd.unique(sites)}
    names = sorted(set(cells.values()))
    order = np.random.default_rng(seed).permutation(len(names))
    fold = {names[i]: int(k % n_folds) for k, i in enumerate(order)}
    return sites.map(lambda x: fold[cells[x]]).astype(int)


def _fit(X: pd.DataFrame, y: np.ndarray, cfg: dict, seed: int):
    from xgboost import XGBClassifier
    ratio = max(1.0, (y == 0).sum() / max((y == 1).sum(), 1))
    m = XGBClassifier(n_estimators=int(cfg.get("n_estimators", 300)), max_depth=int(cfg.get("max_depth", 5)), learning_rate=float(cfg.get("learning_rate", 0.05)),
                      subsample=0.8, colsample_bytree=0.8, scale_pos_weight=min(ratio, 20.0), random_state=seed, seed=seed, n_jobs=1, tree_method="hist", eval_metric="aucpr")
    m.fit(X, y)
    return m


def choose_features(df: pd.DataFrame, listed: list[str] | None) -> list[str]:
    pool = listed if listed else list(df.columns)
    return [c for c in pool if c in df.columns and c.lower() not in EXCLUDE and not c.lower().startswith(BLOCKED_PREFIX)
            and pd.api.types.is_numeric_dtype(df[c]) and df[c].notna().any()]


def train(df: pd.DataFrame, target: str, horizon: int, cfg: dict, seed: int, listed: list[str] | None = None) -> dict:
    """Fit, calibrate and evaluate. Pure with respect to the database."""
    from sklearn.isotonic import IsotonicRegression
    df = df.dropna(subset=["y", "date"]).sort_values(["date", "site_id"]).reset_index(drop=True)
    y = df["y"].astype(int).to_numpy()
    sp = temporal_splits(df["date"], horizon, cfg.get("train_end"), cfg.get("cal_end"))
    feats = choose_features(df, listed)
    if not feats:
        raise ValueError("No usable numeric features.")
    X = df[feats].astype("float32")
    tr, ca, te = sp["train"], sp["cal"], sp["test"]
    for name, mask in (("train", tr), ("calibration", ca), ("test", te)):
        if y[mask].sum() < 5 or (y[mask] == 0).sum() < 5:
            raise ValueError(f"The {name} window has too few examples of each outcome.")
    model = _fit(X[tr], y[tr], cfg, seed)
    iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0).fit(model.predict_proba(X[ca])[:, 1], y[ca])
    p_cal = iso.predict(model.predict_proba(X[ca])[:, 1])
    p_te = iso.predict(model.predict_proba(X[te])[:, 1])
    thr = best_f1_threshold(y[ca], p_cal)
    result = {"model": binary_metrics(y[te], p_te, thr)}

    base = float(y[tr].mean())
    baselines = {"B0_training_base_rate": binary_metrics(y[te], np.full(te.sum(), base), base)}
    if "WATER_FREQ_30D" in df:
        r = df["WATER_FREQ_30D"].fillna(0).to_numpy()
        baselines["B1_recent_water_frequency_30d"] = binary_metrics(y[te], r[te], best_f1_threshold(y[ca], r[ca]))
    result["baselines"] = baselines

    folds = spatial_blocks(df["site_id"], 3, seed).to_numpy()
    held, oof = [], np.full(len(df), np.nan)
    for f in range(3):
        trn = tr & (folds != f)
        tst = te & (folds == f)
        if y[trn].sum() < 5 or tst.sum() == 0:
            continue
        m = _fit(X[trn], y[trn], {**cfg, "n_estimators": min(int(cfg.get("n_estimators", 300)), 200)}, seed)
        oof[tst] = m.predict_proba(X[tst])[:, 1]
        held.append(int(tst.sum()))
    mask = ~np.isnan(oof)
    from sklearn.metrics import average_precision_score
    result["geographic_holdout"] = {"folds": 3, "block_m": 500, "rows": int(mask.sum()),
                                    "pr_auc": float(average_precision_score(y[mask], oof[mask])) if mask.any() and len(np.unique(y[mask])) == 2 else None,
                                    "note": "Trained without the held-out 500 m blocks, scored on them in the test window. Uncalibrated."}
    result["reliability"] = reliability(y[te], p_te)
    gain = model.get_booster().get_score(importance_type="gain")
    total = sum(gain.values()) or 1
    result["feature_importance"] = sorted(({"feature": k, "gain_share": v / total} for k, v in gain.items()), key=lambda r: -r["gain_share"])[:30]
    result["n_features"] = len(feats)
    result["splits"] = {k: v for k, v in sp.items() if k not in ("train", "cal", "test")} | {"n_train": int(tr.sum()), "n_calibration": int(ca.sum()), "n_test": int(te.sum())}
    result["seed"] = seed
    result["target"] = target
    result["target_label"] = TARGET_RULES[target]["label"]
    result["horizon"] = horizon
    preds = pd.DataFrame({"site_id": df.loc[te, "site_id"].astype(str).to_numpy(), "date": pd.to_datetime(df.loc[te, "date"]).dt.strftime("%Y-%m-%d").to_numpy(),
                          "probability": np.round(p_te.astype(float), 6), "y": y[te]})
    return {"metrics": result, "predictions": preds, "model": model, "calibrator": iso, "features": feats, "threshold": thr}


# ---------------------------------------------------------------- job side
def _finish(run_id: int, status: str, message: str, **kw) -> None:
    with tx() as c:
        c.execute("UPDATE training_runs SET status=?, message=?, finished_utc=?, metrics=COALESCE(?,metrics), model_id=COALESCE(?,model_id), "
                  "predictions_path=COALESCE(?,predictions_path), predictions_sha256=COALESCE(?,predictions_sha256) WHERE id=?",
                  (status, message, now(), kw.get("metrics"), kw.get("model_id"), kw.get("path"), kw.get("sha"), run_id))


def run_job(run_id: int) -> int:
    import joblib
    with tx() as c:
        r = dict(c.execute("SELECT * FROM training_runs WHERE id=?", (run_id,)).fetchone())
        ds = dict(c.execute("SELECT * FROM dataset_versions WHERE id=?", (r["dataset_version_id"],)).fetchone())
        c.execute("UPDATE training_runs SET status='running', started_utc=? WHERE id=?", (now(), run_id))
    try:
        cfg = json.loads(r["config"] or "{}")
        listed = json.loads(ds["columns"]) if ds["provenance"] == "STUDY" else None
        cols = None if not listed else list(dict.fromkeys(["site_id", "date", "y", *listed]))
        df = pd.read_parquet(ds["processed_path"], columns=cols)
        out = train(df, r["target"], r["horizon"], cfg, r["seed"], listed)
        RUNS.mkdir(parents=True, exist_ok=True)
        mid = f"{r['target']}-h{r['horizon']}-run{run_id}"
        path = RUNS / f"{mid}.joblib"
        joblib.dump({"model": out["model"], "calibrator": out["calibrator"], "features": out["features"], "threshold": out["threshold"]}, path)
        pp = RUNS / f"{mid}_predictions.parquet"
        out["predictions"].to_parquet(pp, index=False)
        sha = hashlib.sha256(out["predictions"].to_csv(index=False).encode()).hexdigest()
        m = out["metrics"]
        m["dataset"] = {"id": ds["id"], "kind": ds["kind"], "version": ds["version"], "sha256": ds["sha256"], "provenance": ds["provenance"]}
        m["predictions_sha256"] = sha
        warn = int(ds["provenance"] == "STUDY" and r["target"] == "A") or int(ds["provenance"] == "SYNTHETIC")
        status = "synthetic_fixture" if ds["provenance"] == "SYNTHETIC" else "candidate"
        with tx() as c:
            c.execute("INSERT INTO model_registry (id, target, horizon, version, status, path, metrics, training_run_id, dataset_version_id, audit_warning, notes, created_utc) "
                      "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                      (mid, r["target"], r["horizon"], f"run{run_id}", status, str(path), json.dumps(m), run_id, ds["id"], warn,
                       "Research only. Not served. Requires explicit approval.", now()))
        _finish(run_id, "completed", "Completed. Nothing was promoted.", metrics=json.dumps(m), model_id=mid, path=str(pp), sha=sha)
        return 0
    except Exception as e:  # noqa: BLE001 - recorded on the run so the analyst sees it
        _finish(run_id, "failed", f"{type(e).__name__}: {e}"[:500])
        return 1


def start_job(run_id: int) -> None:
    env = dict(os.environ, OMP_NUM_THREADS="1")
    subprocess.Popen([sys.executable, str(TOOL), str(run_id)], cwd=str(TOOL.parent.parent), env=env,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


if __name__ == "__main__":
    sys.exit(run_job(int(sys.argv[1])))

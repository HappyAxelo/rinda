"""Forecast engine.

Loads (or trains once, then caches) the habitat-formation model exactly as the
study pipeline fits it: XGBoost on the train window, isotonic calibration on the
validation window, threshold carried over from the published run. Every number
the console shows about the model is computed here from the same objects, so the
console cannot drift from the study.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import xgboost as xgb

ROOT = Path(__file__).resolve().parents[1]
# The study bundle (config, tables, outputs) ships in ./study; RINDA_STUDY points
# elsewhere, e.g. at a live habitat-forecast checkout that is still being re-run.
HF = Path(os.environ.get("RINDA_STUDY", ROOT / "study")).resolve()
VAR = Path(os.environ.get("RINDA_VAR", ROOT / "var")).resolve()
(VAR / "models").mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(HF / "src"))

from mhf import uncertainty as U                      # noqa: E402
from mhf.config import load_config                    # noqa: E402
from mhf.evaluation import metrics as M               # noqa: E402
from mhf.models import tabular as T                   # noqa: E402

CFG = load_config(HF / "configs" / "paper_run.yaml")
RUN_SUMMARY = json.loads((HF / "reports" / "run_summary.json").read_text())
HORIZONS = (7, 14)
RISK_BREAKS = {k: tuple(v) for k, v in CFG["mapping"]["risk_classes"].items()}
ENGINE_VERSION = "3.0.0"

# v3 bundle (scripts/v3_export_rinda.py in habitat-forecast). When present it is
# served; the v1 study run stays available as the archived Paper 1 assurance record.
V3 = HF / "v3"
V3_CARD = json.loads((V3 / "model_card.json").read_text()) if (V3 / "model_card.json").exists() else None

# Feature families as programme staff read them, not as the code names them.
# Order matters: the first matching rule wins.
FAMILY_RULES = [
    ("Rainfall forecast", ("NWP_", "X_FCRAIN")),
    ("Rainfall and soil moisture", ("CH_", "E5_", "ST_", "X_RAIN", "X_CHRAIN", "X_SM", "RAIN", "CUM_RAIN",
                                    "MAX_DAILY_RAIN", "DRY_DAYS", "DAYS_SINCE_RAIN")),
    ("Site water history, same orbit", ("TO_", "CO_")),
    ("Orbit geometry", ("ORBIT", "EXPECTED_", "PAIR", "GEOM", "REL_ORBIT", "SAME_ORBIT")),
    ("Site water history", ("WFREQ", "N_PASSES", "DAYS_SINCE_WATER", "MARGIN", "SWITCH_RATE", "PAST_FORM",
                            "NB_", "WATER_")),
    ("Terrain and land cover", ("twi", "hand", "slope", "elev", "dem", "gsw_", "wc_", "tpi", "flow", "dist_")),
    ("Optical (Sentinel-2)", ("S2_",)),
    ("Radar surface state", ("VV_", "VH_", "SITE_RAIN_RESP", "RAIN_RESPONSE")),
    ("Temperature", ("TEMP_", "DEGREE_DAYS")),
    ("Humidity", ("HUMIDITY",)),
    ("Acquisition quality", ("N_LOOKS", "N_VALID_PX", "DAYS_SINCE_PREV_PASS", "METEO_OBSERVED")),
    ("Season", ("DOY", "MONTH", "SEASON")),
]

# Features a rainfall scenario may rescale: amounts only. Counts, dry-day runs
# and radar response terms are physical observations and stay fixed.
RAIN_AMOUNT_PREFIXES = ("RAIN_1D", "RAIN_3D", "RAIN_5D", "RAIN_7D", "RAIN_14D",
                        "RAIN_LAG_", "CUM_RAIN_", "MAX_DAILY_RAIN_", "RAIN_INTENSITY_",
                        "CH_RAIN_", "CH_MAXDAY_", "E5_RAIN_", "E5_MAXRAIN_", "NWP_ECMWF_RAIN_", "NWP_GFS_RAIN_",
                        "NWP_ECMWF_MAXDAY_", "NWP_GFS_MAXDAY_")


def _num(v) -> float | None:
    """JSON-safe number: missing inputs travel as null, never as NaN."""
    return None if v is None or pd.isna(v) or not np.isfinite(v) else float(v)


def _first(r: pd.DataFrame, cols) -> float | None:
    for c in cols:
        if c in r:
            return _num(r[c].iloc[0])
    return None


def family_of(col: str) -> str:
    for fam, prefixes in FAMILY_RULES:
        if any(col.startswith(p) or p in col for p in prefixes):
            return fam
    return "Other"


def classify_risk(p: np.ndarray) -> np.ndarray:
    order = sorted(RISK_BREAKS.items(), key=lambda kv: kv[1][0])
    conds = [(p >= lo) & (p < hi) for _, (lo, hi) in order[:-1]]
    conds.append(p >= order[-1][1][0])
    return np.select(conds, [k for k, _ in order], default="very_low")


def _published(h: int) -> dict:
    for r in RUN_SUMMARY["results"]:
        if r["horizon"] == h:
            return r
    raise KeyError(h)


@dataclass
class HorizonModel:
    horizon: int
    fcols: list[str]
    pipe: object
    cal: object
    threshold: float
    p_val: np.ndarray
    y_val: np.ndarray
    fingerprint: str
    trained_utc: str
    train_window: tuple[str, str]
    val_window: tuple[str, str]
    test_window: tuple[str, str]
    n_train: int
    n_pos_train: int
    test_scores: dict = field(default_factory=dict)
    reproduction: dict = field(default_factory=dict)

    # ---------------------------------------------------------------- scoring
    def raw(self, X: pd.DataFrame) -> np.ndarray:
        return T.predict_proba(self.pipe, X[self.fcols].to_numpy(dtype=float))

    def prob(self, X: pd.DataFrame) -> np.ndarray:
        return U.apply_calibrator(self.cal, self.raw(X))

    def conformal(self, p: np.ndarray, alpha: float = 0.1) -> pd.DataFrame:
        return U.mondrian_conformal(self.p_val, self.y_val, p, alpha=alpha)

    def contributions(self, X: pd.DataFrame) -> np.ndarray:
        """Per-feature additive contributions on the model's log-odds scale."""
        imp = self.pipe.named_steps["impute"].transform(X[self.fcols].to_numpy(dtype=float))
        booster = self.pipe.named_steps["est"].get_booster()
        return booster.predict(xgb.DMatrix(imp), pred_contribs=True)


class V3HorizonModel:
    """Adapter giving the v3 operational model the interface the console uses."""

    def __init__(self, h: int, om, card: dict) -> None:
        self.horizon, self.om = h, om
        self.fcols = list(om.cols)
        hc = card["horizons"][str(h)]
        ev = card["evaluation"][str(h)]["full"]
        self.threshold = float(om.recal.thr_precision)      # alert line: precision >= 0.75 on recent forecasts
        self.threshold_f1 = float(om.recal.thr_f1)
        self.fingerprint = hc["sha256"][:16]
        self.trained_utc = card["manifest"]["built_utc"]
        self.train_window = ("2023-03-01", om.trained_to)
        self.val_window = tuple(om.recal.window)
        self.test_window = (ev["date_from"], ev["date_to"])
        self.n_train = int(card["manifest"]["models"][f"h{h}"]["rows"])
        self.n_pos_train = int(card["manifest"]["models"][f"h{h}"]["events"])
        self.refit_every_days = om.refit_every_days
        self.test_scores = {"pr_auc": ev["pr_auc"], "base_rate": ev["base_rate"],
                            "precision": ev["precision_threshold"]["precision"],
                            "recall": ev["precision_threshold"]["recall"],
                            "precision_at_f1": ev["f1_threshold"]["precision"],
                            "recall_at_f1": ev["f1_threshold"]["recall"], "brier": ev["brier"],
                            "B1_pr_auc": ev["B1_pr_auc"], "v2_pr_auc": ev["v2_same_rows"]["v2_pr_auc"]}
        self.reproduction = {"note": "v3 is served from the frozen bundle; scores are rolling out-of-sample "
                                     "forecasts with recalibration from earlier forecasts only."}

    def raw(self, X: pd.DataFrame) -> np.ndarray:
        return self.om.ensemble.raw(X)

    def prob(self, X: pd.DataFrame) -> np.ndarray:
        return self.om.prob(X)

    def conformal(self, p: np.ndarray, alpha: float = 0.1) -> pd.DataFrame:
        # Served tiers (see habitat-forecast scripts/v3_export_rinda.py): 'formation' is the
        # high-precision alert line; below it, a site the 90% set cannot rule out is 'uncertain'.
        p = np.asarray(p, float)
        d = self.om.recal.conformal(p, alpha)
        tier = np.where(p >= self.threshold, "formation", np.where(d == "no_formation", "no_formation", "uncertain"))
        return pd.DataFrame({"decision": tier})

    def contributions(self, X: pd.DataFrame) -> np.ndarray:
        return self.om.ensemble.contributions(X)


class Engine:
    def __init__(self) -> None:
        self.models: dict[int, HorizonModel] = {}
        self.tables: dict[int, pd.DataFrame] = {}
        self.status = "initialising"
        self.log: list[str] = []
        self._lock = threading.Lock()
        self.version = "v3" if V3_CARD else "v1"

    def _say(self, msg: str) -> None:
        self.log.append(f"{time.strftime('%H:%M:%S')}  {msg}")
        print(f"[engine] {msg}", flush=True)

    # ------------------------------------------------------------------ load
    def load(self) -> None:
        if V3_CARD:
            with self._lock:
                for h in HORIZONS:
                    om = joblib.load(V3 / f"operational_h{h}.joblib")
                    self.models[h] = V3HorizonModel(h, om, V3_CARD)
                    tbl = pd.read_parquet(V3 / f"window_h{h}.parquet")
                    tbl["date"] = pd.to_datetime(tbl["date"])
                    self.tables[h] = tbl
                    self._say(f"h{h}: v3 model {self.models[h].fingerprint} trained to {om.trained_to}, "
                              f"{len(om.cols)} features")
                self.status = "ready"
            return
        self._load_v1()

    def _load_v1(self) -> None:
        """Fit (once) on the full table, but keep only the operational window in
        memory: explanations and scenarios only ever touch forecast issue dates."""
        test_start = pd.Timestamp(CFG["validation"]["temporal"]["test_start"])
        with self._lock:
            for h in HORIZONS:
                path = HF / "data" / "processed" / f"table_A_new_water_h{h}.parquet"
                manifest = json.loads((HF / "data" / "processed" / f"table_A_new_water_h{h}_manifest.json").read_text())
                cached = self._cached(h, manifest)
                if cached is None:
                    full = pd.read_parquet(path)
                    full["date"] = pd.to_datetime(full["date"])
                    cached = self._train(h, full, manifest)
                    del full
                self.models[h] = cached
                # The operational window is cached beside the model: reading it back
                # from the 111-column parquet costs ~300 MB of transient Arrow memory.
                window = VAR / "models" / f"window_h{h}_{cached.fingerprint}.pkl"
                if window.exists():
                    self.tables[h] = pd.read_pickle(window)
                    continue
                cols = ["site_id", "date", "y"] + cached.fcols
                tbl = pd.read_parquet(path, columns=cols, filters=[("date", ">=", test_start)])
                tbl["date"] = pd.to_datetime(tbl["date"])
                tbl = tbl.astype({c: "float32" for c in cached.fcols})
                tbl.to_pickle(window)
                self.tables[h] = tbl
            self.status = "ready"

    @staticmethod
    def _fingerprint(manifest: dict) -> str:
        key = ",".join(manifest["feature_columns"]) + str(manifest["n_rows"]) + str(manifest["n_positive"])
        return hashlib.sha1(key.encode()).hexdigest()[:16]

    def _cached(self, h: int, manifest: dict) -> HorizonModel | None:
        path = VAR / "models" / f"habitat_formation_h{h}.joblib"
        if not path.exists():
            return None
        m: HorizonModel = joblib.load(path)
        if m.fingerprint == self._fingerprint(manifest) and getattr(m, "reproduction", None):
            self._say(f"h{h}: loaded cached model {m.fingerprint}")
            return m
        return None

    def _train(self, h: int, tbl: pd.DataFrame, manifest: dict) -> HorizonModel:
        fcols = manifest["feature_columns"]
        fp = self._fingerprint(manifest)
        path = VAR / "models" / f"habitat_formation_h{h}.joblib"
        self._say(f"h{h}: fitting model on study table ({len(tbl):,} rows, {len(fcols)} features)")
        t = CFG["validation"]["temporal"]
        tr = tbl[tbl.date <= pd.Timestamp(t["train_end"])]
        va = tbl[(tbl.date > pd.Timestamp(t["train_end"])) & (tbl.date <= pd.Timestamp(t["val_end"]))]
        te = tbl[tbl.date >= pd.Timestamp(t["test_start"])]
        pipe = T.build_model("xgboost", CFG, int(tr.y.sum()), int((1 - tr.y).sum()))
        pipe.fit(tr[fcols].to_numpy(dtype=float), tr.y.to_numpy())
        p_va_raw = T.predict_proba(pipe, va[fcols].to_numpy(dtype=float))
        cal = U.fit_calibrator(p_va_raw, va.y.to_numpy(), "isotonic")
        p_va = U.apply_calibrator(cal, p_va_raw)
        thr = float(_published(h)["holdout"]["threshold"])
        p_te = U.apply_calibrator(cal, T.predict_proba(pipe, te[fcols].to_numpy(dtype=float)))
        scores = M.score(te.y.to_numpy(), p_te, thr).as_dict()
        pub = _published(h)["holdout"]["scores"]
        rep = {"published_pr_auc": pub["pr_auc"], "engine_pr_auc": scores["pr_auc"],
               "published_roc_auc": pub["roc_auc"], "engine_roc_auc": scores["roc_auc"],
               "abs_diff_pr_auc": abs(pub["pr_auc"] - scores["pr_auc"])}
        m = HorizonModel(
            horizon=h, fcols=fcols, pipe=pipe, cal=cal, threshold=thr,
            p_val=p_va, y_val=va.y.to_numpy(), fingerprint=fp,
            trained_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            train_window=(str(tr.date.min().date()), str(tr.date.max().date())),
            val_window=(str(va.date.min().date()), str(va.date.max().date())),
            test_window=(str(te.date.min().date()), str(te.date.max().date())),
            n_train=len(tr), n_pos_train=int(tr.y.sum()),
            test_scores={k: (None if isinstance(v, float) and not np.isfinite(v) else v)
                         for k, v in scores.items()},
            reproduction=rep)
        joblib.dump(m, path)
        self._say(f"h{h}: PR-AUC engine {scores['pr_auc']:.4f} vs published {pub['pr_auc']:.4f}")
        return m

    # ------------------------------------------------------------- queries
    def row(self, h: int, site_id: str, date: str) -> pd.DataFrame:
        tbl = self.tables[h]
        r = tbl[(tbl.site_id == site_id) & (tbl.date == pd.Timestamp(date))]
        return r

    def explain(self, h: int, site_id: str, date: str, top: int = 12) -> dict:
        m, r = self.models[h], self.row(h, site_id, date)
        if r.empty:
            return {"available": False}
        contrib = m.contributions(r)[0]
        bias, vals = float(contrib[-1]), contrib[:-1]
        feats = pd.DataFrame({"feature": m.fcols, "value": r[m.fcols].iloc[0].to_numpy(),
                              "contribution": vals})
        feats["family"] = feats.feature.map(family_of)
        fam = (feats.groupby("family").contribution.sum()
               .sort_values(key=np.abs, ascending=False))
        topf = feats.reindex(feats.contribution.abs().sort_values(ascending=False).index).head(top)
        p_raw = float(m.raw(r)[0])
        p = float(m.prob(r)[0])
        conf = m.conformal(np.array([p])).iloc[0]
        return {
            "available": True, "site_id": site_id, "date": date, "horizon": h,
            "probability": p, "raw_score": p_raw, "baseline_logit": bias,
            "missing_inputs": [c for c in m.fcols if pd.isna(r[c].iloc[0])],
            "risk_class": str(classify_risk(np.array([p]))[0]),
            "decision": str(conf["decision"]), "threshold": m.threshold,
            "label_observed": int(r.y.iloc[0]),
            "families": [{"family": k, "contribution": float(v)} for k, v in fam.items()],
            "drivers": [{"feature": f.feature, "family": f.family,
                         "value": _num(f.value),
                         "contribution": float(f.contribution)} for f in topf.itertuples()],
        }

    def scenario(self, h: int, site_id: str, date: str, rain_mult: float,
                 temp_delta: float) -> dict:
        m, r = self.models[h], self.row(h, site_id, date)
        if r.empty:
            return {"available": False}
        rain_cols = [c for c in m.fcols if c.startswith(RAIN_AMOUNT_PREFIXES)]
        temp_cols = [c for c in m.fcols if c.startswith("TEMP_") and not
                     c.startswith(("TEMP_RANGE", "TEMP_TREND", "TEMP_ANOM"))]

        def perturb(k: float, dt: float) -> pd.DataFrame:
            x = r.copy()
            x[rain_cols] = x[rain_cols] * k
            x[temp_cols] = x[temp_cols] + dt
            if "TEMP_ANOM" in m.fcols:
                x["TEMP_ANOM"] = x["TEMP_ANOM"] + dt
            for c in ("DEGREE_DAYS_16C_7D", "DEGREE_DAYS_16C_14D"):
                if c in m.fcols:
                    days = 7 if "7D" in c else 14
                    x[c] = np.maximum(0, x[c] + dt * days)
            return x

        grid = np.round(np.linspace(0, 3, 31), 2)
        batch = pd.concat([perturb(k, temp_delta) for k in grid] + [perturb(rain_mult, temp_delta), r])
        p = m.prob(batch)
        base_p, scen_p = float(p[-1]), float(p[-2])
        conf = m.conformal(np.array([base_p, scen_p]))
        return {
            "available": True, "site_id": site_id, "date": date, "horizon": h,
            "baseline": {"probability": base_p, "decision": str(conf.decision.iloc[0]),
                         "risk_class": str(classify_risk(np.array([base_p]))[0])},
            "scenario": {"probability": scen_p, "decision": str(conf.decision.iloc[1]),
                         "risk_class": str(classify_risk(np.array([scen_p]))[0]),
                         "rain_multiplier": rain_mult, "temp_delta_c": temp_delta},
            "curve": [{"rain_multiplier": float(k), "probability": float(v)}
                      for k, v in zip(grid, p[:len(grid)])],
            "observed_rain_7d": _first(r, ("RAIN_7D", "CH_RAIN_7D", "E5_RAIN_7D")),
            "observed_rain_14d": _first(r, ("RAIN_14D", "CH_RAIN_14D", "E5_RAIN_14D")),
            "missing_inputs": [c for c in m.fcols if pd.isna(r[c].iloc[0])],
            "perturbed_features": len(rain_cols) + len(temp_cols),
        }

    def score_batch(self, h: int, X: pd.DataFrame) -> pd.DataFrame:
        m = self.models[h]
        missing = [c for c in m.fcols if c not in X.columns]
        for c in missing:
            X[c] = np.nan
        p = m.prob(X)
        conf = m.conformal(p)
        out = X[[c for c in ("site_id", "date") if c in X.columns]].copy()
        out["probability"] = p
        out["risk_class"] = classify_risk(p)
        out["decision"] = conf["decision"].to_numpy()
        out["alert"] = p >= m.threshold
        out.attrs["missing_features"] = missing
        return out

    def card(self) -> dict:
        out = {"engine_version": ENGINE_VERSION, "status": self.status, "model_version": self.version,
               "horizons": {}}
        for h, m in self.models.items():
            if isinstance(m, V3HorizonModel):
                out["horizons"][h] = {
                    "algorithm": "XGBoost + LightGBM ensemble, refit every "
                                 f"{m.refit_every_days} days, isotonic recalibration on earlier forecasts",
                    "n_features": len(m.fcols), "threshold": m.threshold, "threshold_f1": m.threshold_f1,
                    "trained_utc": m.trained_utc, "fingerprint": m.fingerprint,
                    "train_window": m.train_window, "val_window": m.val_window,
                    "test_window": m.test_window, "n_train": m.n_train, "n_pos_train": m.n_pos_train,
                    "test_scores": m.test_scores, "reproduction": m.reproduction,
                    "refit_every_days": m.refit_every_days, "conformal_alpha": 0.1,
                }
                continue
            out["horizons"][h] = {
                "algorithm": "Gradient-boosted trees (XGBoost, hist), isotonic calibration",
                "n_features": len(m.fcols), "threshold": m.threshold,
                "trained_utc": m.trained_utc, "fingerprint": m.fingerprint,
                "train_window": m.train_window, "val_window": m.val_window,
                "test_window": m.test_window, "n_train": m.n_train,
                "n_pos_train": m.n_pos_train, "test_scores": m.test_scores,
                "reproduction": m.reproduction,
                "conformal_alpha": 0.1,
            }
        return out


ENGINE = Engine()

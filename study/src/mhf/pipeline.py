"""End-to-end orchestrator.

Runs the phases in order, skipping anything whose inputs are absent and saying
so. Every run writes a manifest recording exactly what was and was not done, so
a result can never be mistaken for one produced from richer data.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from . import assemble as A
from . import explain as E
from . import figures as FIG
from . import labels as L
from . import preprocessing as P
from . import priority as PR
from . import uncertainty as U
from . import validation as V
from .config import Config, load_config
from .evaluation import ablation as AB
from .evaluation import metrics as M
from .features import meteo as FM
from .features import optical as FO
from .features import sar as FS
from .models import tabular as T


@dataclass
class RunResult:
    target: str
    horizon: int
    manifest: dict
    holdout_scores: dict = field(default_factory=dict)
    cv_scores: pd.DataFrame | None = None
    ablation: pd.DataFrame | None = None
    notes: list[str] = field(default_factory=list)
    tables: dict[str, pd.DataFrame] = field(default_factory=dict)
    figures: dict[str, str] = field(default_factory=dict)
    diagnostics: dict = field(default_factory=dict)


def _log(msg: str) -> None:
    print(f"[mhf] {msg}", flush=True)


def build_features(cfg: Config) -> dict[str, pd.DataFrame | None]:
    out: dict[str, pd.DataFrame | None] = {"meteo": None, "sar": None,
                                           "optical": None, "terrain": None,
                                           "thresholds": None}
    if cfg.has("meteo"):
        _log("building meteorological features")
        out["meteo"] = FM.build_meteo_features(P.load_meteo(cfg), cfg)

    if cfg.has("sentinel1"):
        s1 = P.load_sentinel1(cfg)
        ref = None
        if cfg.has("sentinel2"):
            s2 = P.load_sentinel2(cfg)
            thr = cfg["water_detection"]["calibration"]["mndwi_water_threshold"]
            ref = s2[["site_id", "date", "mndwi"]].assign(
                ref_water=(s2["mndwi"] > thr).astype(int))
        thresholds = FS.calibrate_thresholds(s1, cfg, reference=ref)
        _log(f"water thresholds: {thresholds}")
        out["thresholds"] = thresholds
        out["sar"] = FS.build_sar_features(s1, cfg, thresholds, meteo=out["meteo"])

    if cfg.has("sentinel2"):
        _log("building optical features")
        out["optical"] = FO.build_optical_features(P.load_sentinel2(cfg), cfg)

    if cfg.has("terrain"):
        _log("building terrain features")
        out["terrain"] = FO.build_terrain_features(P.load_terrain(cfg), cfg)
    return out


def build_table(cfg: Config, feats: dict, target: str, horizon: int
                ) -> tuple[pd.DataFrame, list[str], dict]:
    if feats["sar"] is None:
        raise P.DataError("cannot build a table without Sentinel-1")

    lab, rep = L.build_target_A(feats["sar"], cfg, horizon)
    if target == "B_suitable_habitat":
        lab, rep = L.build_target_B(lab, cfg, horizon,
                                    optical=feats["optical"],
                                    terrain=feats["terrain"])
    elif target == "C_anopheles":
        ento = P.load_entomology(cfg)
        lab, rep = L.build_target_C(feats["sar"], ento, cfg, horizon)

    # attach coordinates for spatial blocking
    if cfg.has("terrain") or Path(cfg.resolve("data/raw/sites.csv")).exists():
        sp = cfg.resolve("data/raw/sites.csv")
        if sp.exists():
            sites = pd.read_csv(sp)[["site_id", "latitude", "longitude"]]
            lab = lab.merge(sites, on="site_id", how="left")

    df = A.assemble(lab, cfg, target=target, horizon=horizon,
                    meteo_features=feats["meteo"],
                    optical_features=feats["optical"],
                    terrain_features=feats["terrain"])
    df["block_id"] = V._block_ids(df, cfg)
    fcols = A.feature_columns(df)
    checks = A.leakage_guard(df, fcols, horizon, strict=True)
    man = A.build_manifest(df, fcols, target, horizon, checks)
    A.save_table(df, man, cfg)
    return df, fcols, {"label_report": rep.as_dict(), "table": man.as_dict()}


def _fit_predict_factory(cfg: Config, model_name: str):
    def fp(X_tr, y_tr, X_te):
        n_pos, n_neg = int(y_tr.sum()), int((1 - y_tr).sum())
        m = T.build_model(model_name, cfg, n_pos, n_neg)
        m.fit(X_tr, y_tr)
        return T.predict_proba(m, X_te)
    return fp


def run_target(cfg: Config, feats: dict, target: str, horizon: int) -> RunResult:
    _log(f"=== target={target} horizon={horizon}d ===")
    df, fcols, info = build_table(cfg, feats, target, horizon)
    notes = [L.claim_language(target),
             f"mode={cfg.mode}",
             cfg["project"]["scope_note"].strip()]

    if df["y"].nunique() < 2:
        notes.append("NOT EVALUABLE: label has a single class in this table.")
        return RunResult(target, horizon, info, notes=notes)

    # ---------------- cross-validated model comparison on blocked folds
    rows = []
    schemes = [s for s in cfg["validation"]["schemes"]]
    for name in T.available_tabular(cfg):
        fp = _fit_predict_factory(cfg, name)
        for scheme in schemes:
            splits = list(V.get_splitter(scheme)(df, cfg))
            for sp in splits:
                try:
                    V.assert_no_group_overlap(df, sp)
                    V.assert_temporal_order(df, sp, horizon)
                except AssertionError as e:
                    notes.append(f"split rejected: {e}")
                    continue
                tr, te = df.iloc[sp.train_idx], df.iloc[sp.test_idx]
                if tr["y"].nunique() < 2 or te["y"].nunique() < 2:
                    continue
                p = fp(tr[fcols].to_numpy(), tr["y"].to_numpy(),
                       te[fcols].to_numpy())
                rows.append({"model": name, "scheme": scheme, "fold": sp.name,
                             **M.score(te["y"].to_numpy(), p).as_dict()})
    cv = pd.DataFrame(rows)

    # ---------------- untouched temporal holdout for the headline number
    hold = V.temporal_holdout(df, cfg)
    holdout: dict = {}
    tables: dict[str, pd.DataFrame] = {}
    figs: dict[str, str] = {}
    diags: dict = {}
    if len(hold["train"]) and len(hold["test"]):
        best = _select_best(cv, cfg)
        if best:
            fp = _fit_predict_factory(cfg, best)
            tr = df.iloc[np.concatenate([hold["train"]])]
            va = df.iloc[hold["val"]] if len(hold["val"]) else tr
            te = df.iloc[hold["test"]]
            if tr["y"].nunique() > 1 and te["y"].nunique() > 1:
                # Fit ONCE, then score validation and test from the same model.
                n_pos, n_neg = int(tr["y"].sum()), int((1 - tr["y"]).sum())
                mdl = T.build_model(best, cfg, n_pos, n_neg)
                mdl.fit(tr[fcols].to_numpy(), tr["y"].to_numpy())
                p_va_raw = T.predict_proba(mdl, va[fcols].to_numpy())
                p_te_raw = T.predict_proba(mdl, te[fcols].to_numpy())

                # Calibrate FIRST, then choose the threshold on the calibrated
                # scale. Isotonic regression rewrites the probability scale, so a
                # threshold picked on raw scores is meaningless after calibration
                # -- that mismatch silently drives recall to zero.
                cal = U.fit_calibrator(p_va_raw, va["y"].to_numpy(),
                                       cfg.get("uncertainty.calibration", "isotonic"))
                p_va = U.apply_calibrator(cal, p_va_raw)
                p_te = U.apply_calibrator(cal, p_te_raw)

                thr = (M.optimise_threshold(
                           va["y"].to_numpy(), p_va,
                           cfg.get("evaluation.threshold_optimisation.objective", "f1"))
                       if cfg.get("evaluation.threshold_optimisation.enabled", True)
                       else 0.5)
                s = M.score(te["y"].to_numpy(), p_te, thr)
                ci = M.bootstrap_ci(te["y"].to_numpy(), p_te,
                                    te["block_id"].to_numpy(),
                                    n=cfg.get("evaluation.bootstrap.n", 1000),
                                    seed=cfg["project"]["seed"])
                holdout = {"model": best, "threshold": thr,
                           "scores": s.as_dict(), "pr_auc_ci": ci,
                           "brier_decomposition": U.brier_decomposition(
                               te["y"].to_numpy(), p_te)}
                if s.trivial_accuracy_warning:
                    notes.append(s.trivial_accuracy_warning)

                tables, figs, diags = _final_stage(
                    cfg, df, fcols, target, horizon, mdl, best, tr, va, te,
                    p_va, p_te, thr, notes)
                holdout.update(diags)

    # ---------------- ablation on blocked folds only
    abl = None
    blocked = [s for s in schemes if s != "V1_random"]
    if blocked:
        splits = list(V.get_splitter(blocked[0])(df, cfg))
        base_model = _select_best(cv, cfg) or "random_forest"
        abl = AB.run_ablation(df, fcols, cfg, splits,
                              _fit_predict_factory(cfg, base_model))

    # ---------------- fusion strategy comparison, on those same folds
    if blocked and cfg.get("fusion_study.strategies"):
        splits = list(V.get_splitter(blocked[0])(df, cfg))
        base_model = _select_best(cv, cfg) or "random_forest"
        fus = AB.run_fusion_study(df, fcols, cfg, splits,
                                  _fit_predict_factory(cfg, base_model))
        if not fus.empty:
            tables["fusion"] = fus
            notes.append(
                "Intermediate fusion is the neural branch architecture; it is "
                "absent here because the deep stage did not run.")

    return RunResult(target, horizon, info, holdout, cv, abl, notes,
                     tables, figs, diags)


# ------------------------------------------------------------- final stage
def _final_stage(cfg: Config, df: pd.DataFrame, fcols: list[str], target: str,
                 horizon: int, mdl, model_name: str, tr: pd.DataFrame,
                 va: pd.DataFrame, te: pd.DataFrame, p_va: np.ndarray,
                 p_te: np.ndarray, thr: float, notes: list[str]
                 ) -> tuple[dict[str, pd.DataFrame], dict[str, str], dict]:
    """Everything downstream of the headline number: calibration, uncertainty,
    explanation, the operational product and the figures.

    Each block is independently guarded. A failure here degrades the run to
    "we have the metrics but not that diagnostic" and says so, rather than
    discarding a model fit that took minutes to produce.
    """
    tables: dict[str, pd.DataFrame] = {}
    figs: dict[str, str] = {}
    diags: dict = {}
    tag = f"{target}_h{horizon}"
    figdir = cfg.path("figures")
    y_te = te["y"].to_numpy()
    seed = cfg["project"]["seed"]

    # --- calibration ---------------------------------------------------
    try:
        rel = U.reliability_table(y_te, p_te)
        tables["reliability"] = rel
        figs["reliability"] = FIG.reliability_diagram(rel, figdir, tag)
        figs["pr_curve"] = FIG.precision_recall_curve(
            y_te, p_te, float(y_te.mean()), figdir, tag)
    except Exception as e:                                   # pragma: no cover
        notes.append(f"calibration diagnostics unavailable: {e}")

    # --- conformal prediction sets --------------------------------------
    # Calibrated on validation, which the threshold also used but the test set
    # never touched; coverage is therefore an honest out-of-sample statement.
    try:
        alpha = cfg.get("uncertainty.conformal.alpha", 0.1)
        conf = U.mondrian_conformal(p_va, va["y"].to_numpy(), p_te, alpha=alpha)
        cov = U.empirical_coverage(conf, y_te)
        diags["conformal"] = {"alpha": alpha, "target_coverage": 1 - alpha, **cov}
        tables["conformal"] = conf
        _log(f"conformal: coverage={cov['empirical_coverage']:.3f} "
             f"uncertain={cov['pct_uncertain']:.1%}")
    except Exception as e:                                   # pragma: no cover
        conf = None
        notes.append(f"conformal prediction unavailable: {e}")

    # --- ensemble spread as a second uncertainty channel ------------------
    std = None
    try:
        mat = []
        for name in T.available_tabular(cfg):
            if name.startswith("baseline"):
                continue
            m = T.build_model(name, cfg, int(tr["y"].sum()),
                              int((1 - tr["y"]).sum()))
            m.fit(tr[fcols].to_numpy(), tr["y"].to_numpy())
            mat.append(T.predict_proba(m, te[fcols].to_numpy()))
        if len(mat) > 1:
            ens = U.ensemble_uncertainty(np.vstack(mat))
            std = ens["std"]
            diags["ensemble"] = {
                "n_models": len(mat),
                "mean_std": float(std.mean()),
                "pr_auc_of_ensemble_mean": float(
                    M.score(y_te, ens["probability"], thr).pr_auc)}
    except Exception as e:                                   # pragma: no cover
        notes.append(f"ensemble uncertainty unavailable: {e}")

    # --- explanation ------------------------------------------------------
    # Grouped permutation is the primary statement because the lagged rainfall
    # terms are collinear and per-feature attributions are unstable under that.
    X_te = te[fcols]
    try:
        gpi = E.grouped_permutation_importance(
            mdl, X_te, y_te, n_repeats=cfg.get("explain.n_repeats", 5),
            seed=seed)
        tables["grouped_permutation_importance"] = gpi
        figs["importance"] = FIG.grouped_importance_bars(gpi, figdir, tag)
    except Exception as e:                                   # pragma: no cover
        notes.append(f"grouped permutation importance unavailable: {e}")

    try:
        sv = E.shap_values(mdl, X_te, max_rows=cfg.get("explain.shap_rows", 2000),
                           seed=seed)
        if sv is not None:
            tables["shap_global"] = E.global_importance(sv)
            tables["shap_grouped"] = E.grouped_importance(sv)
            lp = E.lag_profile(sv, "RAIN_")
            if not lp.empty:
                tables["rain_lag_profile"] = lp
                figs["lag_profile"] = FIG.lag_profile_plot(lp, figdir, tag)
        else:
            notes.append("SHAP unavailable; grouped permutation reported instead.")
    except Exception as e:                                   # pragma: no cover
        notes.append(f"SHAP unavailable: {e}")

    # --- accumulated local effects for the top drivers ---------------------
    try:
        top = (tables.get("shap_global", pd.DataFrame()).head(6)["feature"].tolist()
               if "shap_global" in tables else [])
        ales = [E.ale_1d(mdl, X_te, f) for f in top]
        ales = [a for a in ales if not a.empty]
        if ales:
            tables["ale"] = pd.concat(ales, ignore_index=True)
    except Exception as e:                                   # pragma: no cover
        notes.append(f"ALE unavailable: {e}")

    # --- performance by season and rainfall regime -------------------------
    try:
        sdf = te.copy()
        sdf["p"] = p_te
        by = [c for c in cfg.get("evaluation.stratify_by", [])
              if c in sdf.columns]
        if by:
            tables["stratified"] = M.stratified_scores(sdf, "y", "p", by, thr)
    except Exception as e:                                   # pragma: no cover
        notes.append(f"stratified scores unavailable: {e}")

    # --- operational product ----------------------------------------------
    try:
        cfg.raw["_claim_language"] = L.claim_language(target)
        meta_cols = [c for c in ("site_id", "date", "latitude", "longitude",
                                 "WATER_PERSISTENCE_PASSES", "block_id")
                     if c in te.columns]
        prod = PR.forecast_product(te[meta_cols], p_te, std, cfg,
                                   horizon, conformal=conf)
        prod = PR.larviciding_priority(prod, cfg)
        tables["forecast_product"] = prod
        tables["top_targets"] = PR.top_targets(prod, cfg)
        PR.write_products(prod, cfg, tag)
        figs["risk_map"] = FIG.risk_map(prod, figdir, tag)
        diags["priority"] = {
            "components_used": prod["priority_components_used"].iloc[0],
            "components_missing": prod["priority_components_missing"].iloc[0],
            "n_rows": int(len(prod))}
    except Exception as e:                                   # pragma: no cover
        notes.append(f"forecast product unavailable: {e}")

    try:
        figs["timeline"] = FIG.label_timeline(df, figdir, tag)
    except Exception as e:                                   # pragma: no cover
        notes.append(f"timeline figure unavailable: {e}")

    return tables, {k: v for k, v in figs.items() if v}, diags


def _select_best(cv: pd.DataFrame, cfg: Config) -> str | None:
    """Chosen on blocked CV, never on the holdout."""
    if cv is None or cv.empty:
        return None
    m = cfg.get("evaluation.primary_metric", "pr_auc")
    sub = cv[cv["scheme"] != "V1_random"]
    if sub.empty:
        sub = cv
    sub = sub[sub["model"] != "baseline_majority"]
    if sub.empty:
        return None
    return sub.groupby("model")[m].mean().idxmax()


def run(config_path: str = "configs/default.yaml") -> dict:
    t0 = time.time()
    cfg = load_config(config_path)
    # Any previous summary is deleted immediately, so an interrupted run
    # cannot leave a stale file that reads like a fresh result.
    stale = cfg.path("reports") / "run_summary.json"
    if stale.exists():
        stale.unlink()
    cfg.write_provenance()
    _log(f"mode={cfg.mode} sources={cfg.available_sources()}")

    if not cfg.enabled_targets():
        _log("NO TARGETS BUILDABLE. Run the data audit for what is missing.")
        return {"status": "blocked", "provenance": cfg.provenance()}

    feats = build_features(cfg)
    results = []
    for target in cfg.enabled_targets():
        for h in cfg["targets"]["horizons_days"]:
            try:
                results.append(run_target(cfg, feats, target, h))
            except Exception as e:                          # keep going
                _log(f"FAILED target={target} h={h}: {e}")
                results.append(RunResult(target, h, {}, notes=[f"FAILED: {e}"]))

    out = {
        "config_path": str(config_path),
        "config_name": cfg["project"]["name"],
        "model_profile": cfg.get("models.profile", "full"),
        "finished_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "provenance": cfg.provenance(),
        "water_thresholds": feats.get("thresholds"),
        "runtime_s": round(time.time() - t0, 1),
        "results": [{"target": r.target, "horizon": r.horizon,
                     "manifest": r.manifest, "holdout": r.holdout_scores,
                     "figures": r.figures, "notes": r.notes} for r in results],
    }

    outdir = cfg.path("outputs")
    outdir.mkdir(parents=True, exist_ok=True)
    for r in results:
        tag = f"{r.target}_h{r.horizon}"
        if r.cv_scores is not None and not r.cv_scores.empty:
            r.cv_scores.to_csv(outdir / f"cv_{tag}.csv", index=False)
            FIG.cv_scheme_comparison(r.cv_scores, cfg.path("figures"), tag)
        if r.ablation is not None and not r.ablation.empty:
            r.ablation.to_csv(outdir / f"ablation_{tag}.csv", index=False)
            summ = r.ablation.attrs.get("summary")
            if summ is not None:
                summ.to_csv(outdir / f"ablation_summary_{tag}.csv", index=False)
                FIG.ablation_bars(summ, cfg.path("figures"), tag)
                # What each source buys, and what a random split would have
                # overclaimed -- both are results, not diagnostics.
                mc = AB.marginal_contributions(summ)
                if not mc.empty:
                    mc.to_csv(outdir / f"marginal_contributions_{tag}.csv",
                              index=False)
                op = AB.optimism_from_random_split(summ)
                if not op.empty:
                    op.to_csv(outdir / f"optimism_{tag}.csv", index=False)
        for name, tbl in r.tables.items():
            if tbl is not None and not tbl.empty:
                tbl.to_csv(outdir / f"{name}_{tag}.csv", index=False)

    # Does the driver of the forecast change with lead time? Only answerable
    # once both horizons have run against the same target.
    for target in {r.target for r in results}:
        byh = {r.horizon: r.tables.get("shap_grouped") for r in results
               if r.target == target}
        hs = sorted(h for h, t in byh.items() if t is not None and not t.empty)
        if len(hs) >= 2:
            hc = E.horizon_comparison(byh[hs[0]], byh[hs[-1]])
            hc.to_csv(outdir / f"horizon_comparison_{target}.csv", index=False)

    rep = cfg.path("reports") / "run_summary.json"
    rep.parent.mkdir(parents=True, exist_ok=True)
    rep.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    _log(f"done in {out['runtime_s']}s -> {rep}")
    return out

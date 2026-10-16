"""Forecast provenance, freshness, model status, verification categories and alerts.

Everything a user needs to judge a forecast travels with it: when it was
generated, which issue date and validity window it covers, how old its inputs
were, which model produced it, and whether that model has earned operational use.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache

import pandas as pd

from data import store
from engine import ENGINE, ENGINE_VERSION, ROOT, RUN_SUMMARY, V3_CARD

RULES_PATH = ROOT / "config" / "operational_rules.json"
TOLERANCE_DAYS = 2  # target matched to the nearest radar pass within +/- 2 days

TARGET = ("New surface-water formation at a monitoring point: dry on the issue date and the 3 days "
          "before it, wet at the target date by Sentinel-1 VV/VH thresholds. Not confirmed breeding, "
          "not vector presence, not transmission risk.")
RESOLUTION = ("Monitoring points: 600 sites, each a 50 m-radius radar sample. Results describe that "
              "circle only; they are not a continuous surface and not a breeding-site location.")


@lru_cache(maxsize=1)
def rules() -> dict:
    return json.loads(RULES_PATH.read_text(encoding="utf-8"))


def today() -> date:
    return datetime.now(timezone.utc).date()


# ------------------------------------------------------------- model status
@lru_cache(maxsize=None)
def model_status(h: int) -> dict:
    if V3_CARD:
        return _model_status_v3(h)
    refs = store().assurance()["references"]
    rows = [r for r in refs if r["horizon"] == h]
    model = next(r for r in rows if r["reference"] == "model")
    base = max((r for r in rows if r["reference"] not in ("model", "B0_climatology")), key=lambda r: r["pr_auc"])
    beaten = base["pr_auc"] >= model["pr_auc"]
    return {
        "status": "research_stage",
        "label": "Research stage: not validated for intervention decisions",
        "reasons": ([f"A persistence baseline ({base['reference']}) scores PR-AUC {base['pr_auc']:.3f} against the "
                     f"model's {model['pr_auc']:.3f} on the same test rows."] if beaten else []) + [
            "No field (entomological) validation yet: outcomes are radar-detected water only.",
            "Hindcast: no numerical rainfall forecast is used, so rain after the issue date is unknown.",
            "Water thresholds are prototype values, not calibrated against field reference.",
            "One weather station: no spatial weather variation.",
        ],
        "model_pr_auc": model["pr_auc"], "best_baseline": base["reference"], "best_baseline_pr_auc": base["pr_auc"],
        "outperforms_baseline": not beaten,
    }


def _model_status_v3(h: int) -> dict:
    ev = V3_CARD["evaluation"][str(h)]["full"]
    m, b1, v2 = ev["pr_auc"], ev["B1_pr_auc"], ev["v2_same_rows"]["v2_pr_auc"]
    op = ev["precision_threshold"]
    return {
        "status": "research_stage",
        "label": "Research stage: not validated for intervention decisions",
        "reasons": [
            f"Rolling out-of-sample forecasts {ev['date_from']} to {ev['date_to']}: PR-AUC {m:.3f}, against "
            f"{b1:.3f} for the 30-day water-frequency persistence rule and {v2:.3f} for the frozen v2 model.",
            f"High-priority alerts (precision target 0.75): precision {op['precision']:.2f}, recall {op['recall']:.2f}.",
            "No field (entomological) validation yet: outcomes are radar-detected water only.",
            "The Feb-Sep 2026 window was blind for v2 but not for v3; v3 still needs an untouched test.",
            "Rainfall inputs add no measurable skill; the forecast rests on radar history, orbit geometry and terrain.",
        ],
        "model_pr_auc": m, "best_baseline": "B1_recent_water_freq_30d", "best_baseline_pr_auc": b1,
        "outperforms_baseline": m > b1,
    }


# ------------------------------------------------------------ categories
def category(call: str, probability: float) -> str:
    vp = rules()["verification_priority"]
    if call == "formation":
        return "high"
    if call == "uncertain":
        return "insufficient"
    thr = vp["moderate"].get("moderate_min_probability")
    if thr is not None and probability >= float(thr):
        return "moderate"
    return "lower"


def category_labels() -> dict:
    return {k: {"label": v["label"], "rule": v["rule"], "meaning": v["meaning"]}
            for k, v in rules()["verification_priority"].items()}


# ------------------------------------------------------------- provenance
def _last_on_or_before(dates: list[str], d: str) -> str | None:
    prior = [x for x in dates if x <= d]
    return prior[-1] if prior else None


@lru_cache(maxsize=None)
def _meteo_dates() -> list[str]:
    return sorted(store().meteo.date.dt.strftime("%Y-%m-%d"))


@lru_cache(maxsize=None)
def _s1_dates() -> list[str]:
    return sorted(store().s1.date.dt.strftime("%Y-%m-%d").unique())


def forecast_meta(h: int, issue: str) -> dict:
    st = store()
    fr = rules()["freshness"]
    latest = st.dates(h)[-1]
    issue_d = date.fromisoformat(issue)
    target = issue_d + timedelta(days=h)
    radar = _last_on_or_before(_s1_dates(), issue)
    weather = _last_on_or_before(_meteo_dates(), issue)
    radar_lag = (issue_d - date.fromisoformat(radar)).days if radar else None
    weather_lag = (issue_d - date.fromisoformat(weather)).days if weather else None
    age = (today() - issue_d).days
    expires = target + timedelta(days=TOLERANCE_DAYS)
    warnings = []
    if age > fr["max_forecast_age_days"]:
        warnings.append({"level": "critical", "code": "stale_forecast",
                         "message": f"This forecast was issued {age} days ago. It describes the past and must not be used for current operations."})
    if issue != latest:
        warnings.append({"level": "info", "code": "not_latest",
                         "message": f"Viewing an earlier issue. The latest available issue is {latest}."})
    if radar_lag is not None and radar_lag > fr["max_radar_lag_days"]:
        warnings.append({"level": "warn", "code": "radar_lag",
                         "message": f"Latest radar pass before issue was {radar_lag} days old."})
    if weather_lag is None or weather_lag > fr["max_weather_lag_days"]:
        warnings.append({"level": "warn", "code": "weather_gap",
                         "message": f"Weather inputs end {weather_lag if weather_lag is not None else 'before the record'} days before the issue date."})
    if h == 14:
        if V3_CARD:
            e7, e14 = (V3_CARD["evaluation"][k]["full"]["pr_auc"] for k in ("7", "14"))
            msg = f"14-day forecasts are less reliable than 7-day forecasts (PR-AUC {e14:.2f} vs {e7:.2f}). Treat as an early outlook."
        else:
            msg = "14-day forecasts are markedly less reliable than 7-day forecasts (PR-AUC 0.31 vs 0.58). Treat as an early outlook."
        warnings.append({"level": "warn", "code": "long_horizon", "message": msg})
    m = ENGINE.models[h]
    return {
        "issue_date": issue, "horizon_days": h,
        "target_date": target.isoformat(),
        "valid_window": [(target - timedelta(days=TOLERANCE_DAYS)).isoformat(), expires.isoformat()],
        "generated_utc": RUN_SUMMARY.get("finished_utc"),
        "generation": ("Rolling hindcast: each issue comes from a model refitted on outcomes observable before it, "
                       "with calibration and alert line taken from earlier forecasts only.") if V3_CARD else
                      "Hindcast: produced in one study run after the fact, using only inputs dated on or before each issue date.",
        "target": TARGET, "units": "Calibrated probability (0–1) and a 90% conformal call",
        "spatial_resolution": RESOLUTION,
        "inputs": {"radar_last_pass": radar, "radar_lag_days": radar_lag,
                   "weather_last_day": weather, "weather_lag_days": weather_lag},
        "model": {"engine": ENGINE_VERSION, "fingerprint": m.fingerprint,
                  "algorithm": "XGBoost + LightGBM, scheduled refits (v3)" if V3_CARD else "XGBoost + isotonic calibration",
                  "train_window": m.train_window, "calibration_window": m.val_window, "test_window": m.test_window},
        "model_status": model_status(h),
        "mode": RUN_SUMMARY["provenance"]["mode"],
        "age_days": age, "latest_issue": latest, "is_latest": issue == latest,
        "status": "stale" if age > fr["max_forecast_age_days"] else "current",
        "leakage_guard": "passed" if all(r["manifest"]["table"]["leakage_checks"]["passed"] for r in RUN_SUMMARY["results"]) else "failed",
        "warnings": warnings,
    }


# ------------------------------------------------------------------ alerts
def alerts_for(h: int, issue: str, rows: pd.DataFrame, states: dict) -> list[dict]:
    ar = rules()["alerts"]
    meta = forecast_meta(h, issue)
    expired = today() > date.fromisoformat(meta["valid_window"][1])
    out = []
    for r in rows.itertuples():
        cat = category(r.conformal_decision, r.probability)
        if cat not in ar["raise_for"]:
            continue
        aid = f"A-{issue}-H{h:02d}-{r.site_id}"
        s = states.get(aid, {})
        out.append({
            "id": aid, "site_id": r.site_id, "issue_date": issue, "horizon": h, "category": cat,
            "why": "Model's 90% prediction set contains only 'formation' for this monitoring point.",
            "evidence": {"probability": float(r.probability), "uncertainty_band": r.uncertainty_band,
                         "wet_passes_recent": float(r.WATER_PERSISTENCE_PASSES or 0)},
            "relevant_until": meta["valid_window"][1], "target_date": meta["target_date"],
            "uncertainty": f"Calibrated probability {r.probability:.2f}; model status research stage.",
            "action": ar["recommended_action"], "responsible_role": ar["responsible_role"],
            "state": s.get("state") or ("expired" if expired else "open"),
            "state_note": s.get("note"), "state_ts": s.get("ts_utc"), "expired": expired,
        })
    return out

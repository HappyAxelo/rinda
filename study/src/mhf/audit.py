"""Phase 1 -- data audit.

Reports only what can be verified from the files on disk. Every check that
cannot be run because a source is absent is reported as ABSENT, never assumed.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .config import Config


@dataclass
class SourceAudit:
    name: str
    present: bool
    path: str
    n_rows: int | None = None
    n_cols: int | None = None
    date_min: str | None = None
    date_max: str | None = None
    span_days: int | None = None
    day_coverage_pct: float | None = None
    n_sites: int | None = None
    n_duplicates: int | None = None
    duplicate_key: list[str] = None
    missing_columns: list[str] = None
    null_pct: dict[str, float] = None
    notes: list[str] = None

    def __post_init__(self) -> None:
        self.missing_columns = self.missing_columns or []
        self.duplicate_key = self.duplicate_key or []
        self.null_pct = self.null_pct or {}
        self.notes = self.notes or []


def _basic(df: pd.DataFrame, required: list[str], date_col: str,
           site_col: str | None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "n_rows": len(df),
        "n_cols": df.shape[1],
        "missing_columns": [c for c in required if c not in df.columns],
        "null_pct": {c: round(float(df[c].isna().mean() * 100), 2)
                     for c in df.columns if df[c].isna().any()},
    }
    if date_col in df.columns:
        d = pd.to_datetime(df[date_col], errors="coerce")
        out["date_min"] = str(d.min().date()) if d.notna().any() else None
        out["date_max"] = str(d.max().date()) if d.notna().any() else None
        if d.notna().any():
            span = (d.max() - d.min()).days + 1
            out["span_days"] = int(span)
            n_days = int(d.dt.normalize().nunique())
            out["day_coverage_pct"] = round(100 * n_days / span, 1)
    if site_col and site_col in df.columns:
        out["n_sites"] = int(df[site_col].nunique())
        keys = [site_col, date_col] if date_col in df.columns else [site_col]
        # Satellite exports carry one row per buffer radius and per track,
        # so (site, date) alone is not the key. Counting those as duplicates
        # would report a data problem that does not exist.
        for extra in ("buffer_m", "rel_orbit"):
            if extra in df.columns:
                keys.append(extra)
        out["duplicate_key"] = keys
        out["n_duplicates"] = int(df.duplicated(keys).sum())
    return out


def audit_meteo(cfg: Config) -> SourceAudit:
    node = cfg["sources"]["meteo"]
    if not cfg.has("meteo"):
        return SourceAudit("meteo", False, node["path"],
                           notes=["file absent or present:false"])
    df = pd.read_csv(cfg.resolve(node["path"]), encoding="utf-8-sig")
    a = SourceAudit("meteo", True, node["path"],
                    **_basic(df, node["required"], node["date_col"],
                             node["station_col"]))
    notes: list[str] = []

    if node["station_col"] in df.columns and df[node["station_col"]].nunique() == 1:
        notes.append(
            f"SINGLE STATION ({df[node['station_col']].iloc[0]}). Meteorological "
            "predictors vary in time but NOT in space. Any apparent spatial skill "
            "comes from SAR/optical/terrain, not from weather."
        )

    # independent events, not raw rows -- this is the real sample size
    if "rainfall_mm" in df.columns and node["date_col"] in df.columns:
        s = (df.assign(_d=pd.to_datetime(df[node["date_col"]]))
               .sort_values("_d").set_index("_d")["rainfall_mm"].fillna(0.0))
        wet = (s >= 1.0).astype(int)
        spells = int((wet.diff() == 1).sum())
        notes.append(
            f"{spells} independent wet spells (>=1 mm runs) across {len(s)} daily "
            f"rows. Effective meteorological sample size ~= {spells}, not {len(s)}."
        )
        a.null_pct.setdefault("_dry_day_pct", round(float((s == 0).mean() * 100), 1))

    # humidity gate -- audit found a systematic bias on incomplete days
    gate = node.get("humidity_gate_col")
    if gate and gate in df.columns and "humidity_mean_pct" in df.columns:
        g = df[gate].astype(str).str.lower().isin(["true", "1"])
        if g.any() and (~g).any():
            diff = df.loc[g, "humidity_mean_pct"].mean() - df.loc[~g, "humidity_mean_pct"].mean()
            notes.append(
                f"Humidity complete on {int(g.sum())}/{len(df)} days "
                f"({100 * g.mean():.1f}%). Incomplete days differ by {diff:+.1f} pts "
                "-> NOT missing at random. Gate on the flag; never impute across it."
            )
        n_zero = int((df["humidity_mean_pct"] == 0).sum())
        if n_zero:
            notes.append(f"{n_zero} days report exactly 0.0% RH (implausible) -> treat as missing.")

    a.notes = notes
    return a


def audit_generic(cfg: Config, name: str, date_col: str = "date",
                  site_col: str = "site_id") -> SourceAudit:
    node = cfg["sources"][name]
    if not cfg.has(name):
        return SourceAudit(name, False, node["path"],
                           notes=["file absent or present:false"])
    df = pd.read_csv(cfg.resolve(node["path"]))
    a = SourceAudit(name, True, node["path"],
                    **_basic(df, node.get("required", []), date_col, site_col))
    notes: list[str] = []

    if "buffer_m" in df.columns and df["buffer_m"].nunique() > 1:
        b = sorted(df["buffer_m"].unique())
        n_unique = int(df.drop_duplicates([site_col, date_col]).shape[0])
        notes.append(
            f"{len(b)} buffer radii present {b}: {len(df)} rows describe about "
            f"{n_unique} site-dates. The loader pivots radii into columns; it "
            "does NOT collapse them, so the multi-scale design is preserved.")

    if "rel_orbit" in df.columns and df["rel_orbit"].nunique() > 1:
        orb = sorted(df["rel_orbit"].dropna().unique().tolist())
        key_dups = a.n_duplicates or 0
        notes.append(
            f"{len(orb)} relative orbits present {orb}. Tracks with different "
            "incidence angles carry a systematic backscatter offset, so "
            "rel_orbit is retained as a model feature.")
        if key_dups:
            notes.append(
                f"{key_dups} rows share (site, date, buffer, orbit) -- two tracks "
                "imaging the same day. These are genuine repeat looks and are "
                "averaged in LINEAR POWER by the loader, not discarded.")

    if "pass" in df.columns and df["pass"].nunique() > 1:
        notes.append(
            f"Both orbit passes exported {sorted(df['pass'].unique())}. "
            f"Config keeps '{node.get('orbit_pass')}' only.")

    a.notes = notes
    return a


def run_audit(cfg: Config) -> dict[str, Any]:
    audits = {
        "meteo": audit_meteo(cfg),
        "sentinel1": audit_generic(cfg, "sentinel1"),
        "sentinel2": audit_generic(cfg, "sentinel2"),
        "terrain": audit_generic(cfg, "terrain", date_col="__none__"),
        "entomology": audit_generic(cfg, "entomology"),
        "nwp_forecast": audit_generic(cfg, "nwp_forecast", site_col="__none__"),
    }

    feasibility = {
        "mode": cfg.mode,
        "enabled_targets": cfg.enabled_targets(),
        "blocked_targets": cfg.blocked_targets(),
        "modelling_table_buildable": cfg.has("sentinel1"),
        "spatial_modelling_possible": cfg.has("sentinel1") or cfg.has("terrain"),
        "deep_learning_gate": _deep_learning_gate(cfg, audits),
        "cross_station_validation_possible": (
            (audits["meteo"].n_sites or 0) > 1
        ),
    }

    report = {
        "provenance": cfg.provenance(),
        "sources": {k: asdict(v) for k, v in audits.items()},
        "feasibility": feasibility,
        "required_later": _required_later(cfg),
    }
    out = cfg.path("reports") / "data_audit.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return report


def _deep_learning_gate(cfg: Config, audits: dict[str, SourceAudit]) -> dict[str, Any]:
    """Deep models are reported only if the data can actually support them."""
    gate = cfg.get("models.power_gate", {})
    verdict = {
        "min_positive_sequences_required": gate.get("min_positive_sequences"),
        "min_independent_events_required": gate.get("min_independent_events"),
        "status": "NOT_EVALUABLE",
        "reason": "",
    }
    if not cfg.has("sentinel1"):
        verdict["reason"] = (
            "No Sentinel-1 export, so no sequences and no labels exist yet."
        )
        return verdict
    verdict["status"] = "CHECK_AT_TRAIN_TIME"
    verdict["reason"] = (
        "Sequence count is known only after label generation; the trainer "
        "re-checks this gate and refuses to report a deep model that fails it."
    )
    return verdict


def _required_later(cfg: Config) -> list[dict[str, str]]:
    need = []
    if not cfg.has("sentinel1"):
        need.append({"item": "Sentinel-1 per-site time series",
                     "blocks": "all EO features; Targets A and B; the entire model"})
    if not cfg.has("sentinel2"):
        need.append({"item": "Sentinel-2 per-site time series",
                     "blocks": "optical features; Target B suitability screen"})
    if not cfg.has("terrain"):
        need.append({"item": "Terrain rasters (DEM, slope, TWI, flow accumulation)",
                     "blocks": "terrain features; ablation arms G and H"})
    if not cfg.has("entomology"):
        need.append({"item": "Larval survey records",
                     "blocks": "Target C; any claim about confirmed Anopheles breeding"})
    if not cfg.has("nwp_forecast"):
        need.append({"item": "Numerical weather prediction rainfall/temperature",
                     "blocks": "operational forecast mode (system stays a hindcast)"})
    node = cfg["sources"]["meteo"]
    if cfg.has("meteo"):
        df = pd.read_csv(cfg.resolve(node["path"]), encoding="utf-8-sig",
                         usecols=[node["station_col"]])
        if df[node["station_col"]].nunique() < 2:
            need.append({"item": "Additional weather stations",
                         "blocks": "spatially varying meteorology; cross-station validation"})
    return need


def format_audit(report: dict[str, Any]) -> str:
    """Human-readable audit for the terminal and for reports/data_audit.txt."""
    L: list[str] = []
    p = report["provenance"]
    L.append("=" * 72)
    L.append("DATA AUDIT — " + p["project"])
    L.append("study area : " + p["study_area"])
    L.append("mode       : " + p["mode"].upper())
    L.append("=" * 72)
    for name, a in report["sources"].items():
        if not a["present"]:
            L.append(f"\n[ABSENT ] {name:12s} {a['path']}")
            for n in a["notes"]:
                L.append(f"           - {n}")
            continue
        L.append(f"\n[PRESENT] {name:12s} rows={a['n_rows']} cols={a['n_cols']}")
        if a["date_min"]:
            L.append(f"           dates {a['date_min']} .. {a['date_max']} "
                     f"(span {a['span_days']}d, coverage {a['day_coverage_pct']}%)")
        if a["n_sites"] is not None:
            L.append(f"           sites={a['n_sites']} duplicates={a['n_duplicates']}")
        if a["missing_columns"]:
            L.append(f"           !! MISSING COLUMNS: {a['missing_columns']}")
        for n in a["notes"]:
            L.append(f"           - {n}")
    f = report["feasibility"]
    L.append("\n" + "=" * 72)
    L.append("FEASIBILITY")
    L.append("=" * 72)
    L.append(f"  modelling table buildable : {f['modelling_table_buildable']}")
    L.append(f"  enabled targets           : {f['enabled_targets'] or 'NONE'}")
    for t, why in f["blocked_targets"].items():
        L.append(f"  blocked: {t:22s} -> {why}")
    L.append(f"  cross-station validation  : {f['cross_station_validation_possible']}")
    L.append(f"  deep learning             : {f['deep_learning_gate']['status']}"
             f" ({f['deep_learning_gate']['reason']})")
    L.append("\nREQUIRED LATER")
    for r in report["required_later"]:
        L.append(f"  - {r['item']}\n      blocks: {r['blocks']}")
    return "\n".join(L)

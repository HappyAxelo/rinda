"""Leakage-safe site-history, acquisition-geometry and neighbourhood features.

Added after the paper run showed that a one-line persistence rule (30-day
water frequency, B1) beat the gradient-boosted model. Three families:

1. ACQUISITION GEOMETRY. The record mixes two descending relative orbits
   (21 and 123). Orbit 123 reads ~1.5 dB darker in VV, so a site that is dry
   under orbit 21 is far more likely to cross the water threshold under orbit
   123. The orbit of the acquisition at t+h is fixed by the published
   Sentinel-1 observation plan, so it is known at forecast time. Only the
   acquisition's DATE and ORBIT are used, never its backscatter.

2. LONG SITE MEMORY. Water frequency over 90/180/365 days and the whole past,
   per-orbit backscatter history, distance of the last look in the target
   orbit to the water threshold, and the site's past formation rate computed
   from outcomes that were already observable at t (outcome date <= t).

3. NEIGHBOURHOOD. Current wetness and past formation rate of the k nearest
   sites, from acquisitions on or before t.

Every feature is an as-of join on dates <= t. `SITE_RAIN_RESPONSIVENESS` in the
paper table was fitted on the whole record; `RAIN_RESP_SLOPE_PAST` is the
expanding, past-only replacement.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

VV_MAX, VH_MAX = -17.0, -23.0
ORBITS = (21, 123)


def load_series(path, buffer_m: int = 50) -> pd.DataFrame:
    s = pd.read_csv(path, parse_dates=["date"])
    s = s[(s["buffer_m"] == buffer_m) & (s["pass"] == "DESCENDING")]
    s = s[["site_id", "date", "vv_db", "vh_db", "rel_orbit"]].copy()
    s["rel_orbit"] = s["rel_orbit"].astype(int)
    s["water"] = ((s.vv_db <= VV_MAX) & (s.vh_db <= VH_MAX)).astype(int)
    s["margin"] = np.maximum(s.vv_db - VV_MAX, s.vh_db - VH_MAX)  # <=0 means water
    return s.sort_values(["site_id", "date"]).reset_index(drop=True)


def _roll(g: pd.DataFrame, col: str, w: str, fn: str) -> np.ndarray:
    r = g.set_index("date")[col].rolling(w, closed="right", min_periods=1)
    return getattr(r, fn)().to_numpy()


def _site_history(s: pd.DataFrame) -> pd.DataFrame:
    """Per (site, acquisition) statistics using acquisitions <= date."""
    out = []
    for sid, g in s.groupby("site_id", sort=False):
        f = pd.DataFrame({"site_id": sid, "date": g.date.to_numpy()})
        w = g.water.to_numpy()
        for d in (90, 180, 365):
            f[f"WFREQ_{d}D"] = _roll(g, "water", f"{d}D", "mean")
        f["WFREQ_ALL"] = np.cumsum(w) / np.arange(1, len(w) + 1)
        f["N_PASSES_ALL"] = np.arange(1, len(w) + 1)
        last_wet = pd.Series(np.where(w == 1, g.date.to_numpy(), np.datetime64("NaT")))
        last_wet = last_wet.ffill().to_numpy()
        f["DAYS_SINCE_WATER"] = (g.date.to_numpy() - last_wet) / np.timedelta64(1, "D")
        f["MARGIN_NOW"] = g.margin.to_numpy()
        f["MARGIN_MIN_90D"] = _roll(g, "margin", "90D", "min")
        f["MARGIN_MEAN_365D"] = _roll(g, "margin", "365D", "mean")
        f["ORBIT_NOW"] = g.rel_orbit.to_numpy()
        # wet->dry->wet switching rate: how ephemeral the site is
        sw = np.r_[0, np.abs(np.diff(w))]
        f["SWITCH_RATE_365D"] = (pd.Series(sw, index=g.date).rolling("365D", min_periods=1)
                                 .mean().to_numpy())
        out.append(f)
    return pd.concat(out, ignore_index=True)


def _orbit_history(s: pd.DataFrame) -> dict[int, pd.DataFrame]:
    """For each orbit, per-site as-of statistics of that orbit's acquisitions."""
    res = {}
    for o in ORBITS:
        so = s[s.rel_orbit == o]
        rows = []
        for sid, g in so.groupby("site_id", sort=False):
            f = pd.DataFrame({"site_id": sid, "date": g.date.to_numpy()})
            f["LAST_VV"] = g.vv_db.to_numpy()
            f["LAST_VH"] = g.vh_db.to_numpy()
            f["LAST_MARGIN"] = g.margin.to_numpy()
            f["LAST_WATER"] = g.water.to_numpy()
            f["WFREQ_90D"] = _roll(g, "water", "90D", "mean")
            f["WFREQ_365D"] = _roll(g, "water", "365D", "mean")
            f["WFREQ_ALL"] = np.cumsum(g.water.to_numpy()) / np.arange(1, len(g) + 1)
            f["VV_MEAN_365D"] = _roll(g, "vv_db", "365D", "mean")
            f["MARGIN_MIN_365D"] = _roll(g, "margin", "365D", "min")
            f["OBS_DATE"] = g.date.to_numpy()
            rows.append(f)
        res[o] = pd.concat(rows, ignore_index=True).sort_values("date")
    return res


def _asof(left: pd.DataFrame, right: pd.DataFrame, on_left: str = "date") -> pd.DataFrame:
    l = left.reset_index().rename(columns={"index": "_row"}).sort_values(on_left)
    r = right.sort_values("date")
    m = pd.merge_asof(l, r, left_on=on_left, right_on="date", by="site_id",
                      direction="backward", allow_exact_matches=True,
                      suffixes=("", "_r"))
    return m.sort_values("_row").set_index("_row")


def _target_acquisition(s: pd.DataFrame, table: pd.DataFrame, h: int, tol: int = 2) -> pd.DataFrame:
    """Orbit and date of the acquisition the label is read from (schedule only)."""
    out_orbit = np.full(len(table), np.nan)
    out_gap = np.full(len(table), np.nan)
    by_site = {sid: g for sid, g in s.groupby("site_id", sort=False)}
    for sid, idx in table.groupby("site_id").indices.items():
        g = by_site[sid]
        dates, orb = g.date.to_numpy(), g.rel_orbit.to_numpy()
        tgt = table["date"].to_numpy()[idx] + np.timedelta64(h, "D")
        diff = np.abs((dates[None, :] - tgt[:, None]) / np.timedelta64(1, "D"))
        j = diff.argmin(1)                       # same tie-break as labels._match_future
        ok = diff[np.arange(len(idx)), j] <= tol
        out_orbit[idx] = np.where(ok, orb[j], np.nan)
        out_gap[idx] = np.where(ok, (dates[j] - table["date"].to_numpy()[idx]) / np.timedelta64(1, "D"), np.nan)
    return pd.DataFrame({"ORBIT_TARGET": out_orbit, "TARGET_GAP_DAYS": out_gap}, index=table.index)


def _past_label_rate(table: pd.DataFrame, h: int, tol: int = 2) -> pd.DataFrame:
    """Site formation rate from outcomes already observed at t (t' + h + tol <= t)."""
    t = table[["site_id", "date", "y", "PAIR"]].copy()
    t["known_on"] = t["date"] + pd.Timedelta(days=h + tol)
    t = t.sort_values(["site_id", "known_on"])
    t["cum_y"] = t.groupby("site_id").y.cumsum()
    t["cum_n"] = t.groupby("site_id").cumcount() + 1
    t["pair_cum_y"] = t.groupby(["site_id", "PAIR"]).y.cumsum()
    t["pair_cum_n"] = t.groupby(["site_id", "PAIR"]).cumcount() + 1
    known = t[["site_id", "known_on", "cum_y", "cum_n"]].rename(columns={"known_on": "date"})
    m = _asof(table[["site_id", "date"]], known.drop_duplicates(["site_id", "date"], keep="last"))
    res = pd.DataFrame(index=table.index)
    # Beta(1, 30) prior shrinks sites with few known outcomes toward ~3%.
    res["PAST_FORM_N"] = m["cum_n"].fillna(0).to_numpy()
    res["PAST_FORM_RATE"] = ((m["cum_y"].fillna(0) + 1) / (m["cum_n"].fillna(0) + 31)).to_numpy()
    pair_rates = np.full(len(table), np.nan)
    for pair, sub in t.groupby("PAIR"):
        kn = sub[["site_id", "known_on", "pair_cum_y", "pair_cum_n"]].rename(columns={"known_on": "date"})
        kn = kn.drop_duplicates(["site_id", "date"], keep="last")
        rows = table.index[table["PAIR"].to_numpy() == pair]
        mm = _asof(table.loc[rows, ["site_id", "date"]], kn)
        pair_rates[table.index.get_indexer(rows)] = ((mm["pair_cum_y"].fillna(0) + 1)
                                                      / (mm["pair_cum_n"].fillna(0) + 31)).to_numpy()
    res["PAST_FORM_RATE_PAIR"] = pair_rates
    return res


def _neighbours(xy: pd.DataFrame, k: int = 8) -> dict[str, list[str]]:
    """k nearest sites from the fixed site register (static, no time dependence)."""
    lat0 = np.deg2rad(xy.latitude.mean())
    P = np.c_[xy.latitude.to_numpy() * 111.0, xy.longitude.to_numpy() * 111.0 * np.cos(lat0)]
    D = ((P[:, None, :] - P[None, :, :]) ** 2).sum(-1)
    np.fill_diagonal(D, np.inf)
    nn = np.argsort(D, 1)[:, :k]
    ids = xy.index.to_numpy()
    return {ids[i]: list(ids[nn[i]]) for i in range(len(ids))}


def _rain_slope_past(s: pd.DataFrame, meteo_rain3: pd.Series) -> pd.DataFrame:
    """Expanding (past-only) slope of VV change on 3-day rain, per site."""
    out = []
    for sid, g in s.groupby("site_id", sort=False):
        x = meteo_rain3.reindex(g.date).to_numpy()
        yv = g.vv_db.diff().to_numpy()
        ok = ~(np.isnan(x) | np.isnan(yv))
        x0, y0 = np.where(ok, x, 0), np.where(ok, yv, 0)
        n = np.cumsum(ok)
        sx, sy, sxy, sxx = np.cumsum(x0), np.cumsum(y0), np.cumsum(x0 * y0), np.cumsum(x0 * x0)
        var = sxx - sx ** 2 / np.maximum(n, 1)
        slope = np.where((n >= 8) & (var > 1e-6), (sxy - sx * sy / np.maximum(n, 1)) / np.where(var > 1e-6, var, 1), np.nan)
        out.append(pd.DataFrame({"site_id": sid, "date": g.date.to_numpy(), "RAIN_RESP_SLOPE_PAST": slope}))
    return pd.concat(out, ignore_index=True)


def add_history_features(table: pd.DataFrame, s: pd.DataFrame, h: int,
                         meteo_rain3: pd.Series | None = None,
                         sites: pd.DataFrame | None = None) -> tuple[pd.DataFrame, dict[str, list[str]]]:
    """Return table with new columns and a dict of feature-family -> columns."""
    t = table.copy()
    fam: dict[str, list[str]] = {}

    # ---- geometry
    ta = _target_acquisition(s, t, h)
    t = t.join(ta)
    cur = _asof(t[["site_id", "date"]], s[["site_id", "date", "rel_orbit"]])
    t["ORBIT_NOW"] = cur["rel_orbit"].to_numpy().astype(float)
    t["ORBIT_TO_123"] = (t.ORBIT_TARGET == 123).astype(float)
    t["ORBIT_CHANGE"] = (t.ORBIT_TARGET != t.ORBIT_NOW).astype(float)
    t["PAIR"] = (t.ORBIT_NOW.fillna(0).astype(int).astype(str) + ">"
                 + t.ORBIT_TARGET.fillna(0).astype(int).astype(str))
    fam["geometry"] = ["ORBIT_NOW", "ORBIT_TO_123", "ORBIT_CHANGE", "TARGET_GAP_DAYS"]

    # ---- site memory
    sh = _site_history(s)
    m = _asof(t[["site_id", "date"]], sh)
    mem = [c for c in sh.columns if c not in ("site_id", "date", "ORBIT_NOW")]
    for c in mem:
        t[c] = m[c].to_numpy()
    oh = _orbit_history(s)
    per = {o: _asof(t[["site_id", "date"]], oh[o]) for o in ORBITS}
    ocols = ["LAST_VV", "LAST_VH", "LAST_MARGIN", "LAST_WATER", "WFREQ_90D",
             "WFREQ_365D", "WFREQ_ALL", "VV_MEAN_365D", "MARGIN_MIN_365D"]
    tgt = t.ORBIT_TARGET.to_numpy()
    nowo = t.ORBIT_NOW.to_numpy()
    for c in ocols:
        a21, a123 = per[21][c].to_numpy(dtype=float), per[123][c].to_numpy(dtype=float)
        t[f"TO_{c}"] = np.where(tgt == 123, a123, np.where(tgt == 21, a21, np.nan))
        t[f"CO_{c}"] = np.where(nowo == 123, a123, np.where(nowo == 21, a21, np.nan))
    age21 = (t.date - per[21]["OBS_DATE"]).dt.days.to_numpy(dtype=float)
    age123 = (t.date - per[123]["OBS_DATE"]).dt.days.to_numpy(dtype=float)
    t["TO_LAST_AGE_DAYS"] = np.where(tgt == 123, age123, np.where(tgt == 21, age21, np.nan))
    # Expected backscatter under the target geometry: current look + site's orbit offset.
    t["ORBIT_OFFSET_VV"] = t.TO_VV_MEAN_365D - t.CO_VV_MEAN_365D
    t["EXPECTED_VV_TARGET"] = t.VV_DB + t.ORBIT_OFFSET_VV.fillna(0)
    t["EXPECTED_MARGIN_TARGET"] = t.MARGIN_NOW - t.CO_MARGIN_MIN_365D.fillna(0) + t.TO_MARGIN_MIN_365D.fillna(0)
    to_cols = [f"TO_{c}" for c in ocols] + ["TO_LAST_AGE_DAYS"]
    co_cols = [f"CO_{c}" for c in ocols if c not in ("LAST_VV", "LAST_VH", "LAST_MARGIN", "LAST_WATER")]
    fam["orbit_memory"] = to_cols + co_cols + ["ORBIT_OFFSET_VV", "EXPECTED_VV_TARGET", "EXPECTED_MARGIN_TARGET"]
    fam["site_memory"] = mem

    lab = _past_label_rate(t, h)
    t = t.join(lab)
    fam["past_outcomes"] = list(lab.columns)

    # ---- season
    doy = t.date.dt.dayofyear.to_numpy() + h
    t["DOY_SIN"], t["DOY_COS"] = np.sin(2 * np.pi * doy / 365.25), np.cos(2 * np.pi * doy / 365.25)
    fam["season"] = ["DOY_SIN", "DOY_COS"]

    # ---- neighbourhood (as of t)
    xy = (sites.set_index("site_id")[["latitude", "longitude"]] if sites is not None
          else t.groupby("site_id")[["latitude", "longitude"]].first())
    nb = _neighbours(xy)
    snap = sh[["site_id", "date", "WFREQ_90D"]].merge(s[["site_id", "date", "water", "margin"]], on=["site_id", "date"])
    rate = t[["site_id", "date", "PAST_FORM_RATE"]]
    nb_rows = []
    for sid, nbs in nb.items():
        rows = t.index[t.site_id.to_numpy() == sid]
        if len(rows) == 0:
            continue
        left = t.loc[rows, ["date"]].reset_index()
        acc = {"NB_WATER_NOW": [], "NB_WFREQ_90D": [], "NB_MARGIN_NOW": [], "NB_FORM_RATE": []}
        for n in nbs:
            sn = snap[snap.site_id == n].sort_values("date")
            mm = pd.merge_asof(left.sort_values("date"), sn, on="date", direction="backward").set_index("index").loc[left["index"]]
            acc["NB_WATER_NOW"].append(mm.water.to_numpy(float))
            acc["NB_WFREQ_90D"].append(mm.WFREQ_90D.to_numpy(float))
            acc["NB_MARGIN_NOW"].append(mm.margin.to_numpy(float))
            rn = rate[rate.site_id == n][["date", "PAST_FORM_RATE"]].sort_values("date")
            mr = pd.merge_asof(left.sort_values("date"), rn, on="date", direction="backward").set_index("index").loc[left["index"]]
            acc["NB_FORM_RATE"].append(mr.PAST_FORM_RATE.to_numpy(float))
        with np.errstate(all="ignore"), __import__("warnings").catch_warnings():
            __import__("warnings").simplefilter("ignore")
            f = pd.DataFrame({k: np.nanmean(np.vstack(v), 0) for k, v in acc.items()}, index=rows)
            f["NB_MARGIN_MIN"] = np.nanmin(np.vstack(acc["NB_MARGIN_NOW"]), 0)
        nb_rows.append(f)
    nbf = pd.concat(nb_rows)
    t = t.join(nbf)
    fam["neighbourhood"] = list(nbf.columns)

    if meteo_rain3 is not None:
        rs = _rain_slope_past(s, meteo_rain3)
        mr = _asof(t[["site_id", "date"]], rs)
        t["RAIN_RESP_SLOPE_PAST"] = mr["RAIN_RESP_SLOPE_PAST"].to_numpy()
        fam["rain_response_past"] = ["RAIN_RESP_SLOPE_PAST"]
    return t, fam

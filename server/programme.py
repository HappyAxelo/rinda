"""Programme uptake: monitoring and evaluation, weekly reporting, DHIS2-shaped
export and a message outbox.

Everything here is computed from records RINDA already holds (tasks, field
observations, reviews, interventions, alert actions, sign-ins). Nothing is
estimated or filled in: an indicator with no records says so instead of showing
zero. Malaria incidence is not measured here and is never inferred.

Demonstration records are excluded unless the deployment is a demonstration, in
which case every output is labelled as such.
"""
from __future__ import annotations

import csv
import html
import io
import json
import os
import statistics
from datetime import date, datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field

import ops
import security as S
from data import store
from db import now, tx
from engine import ROOT
from rules import forecast_meta, today
from security import User, require

router = APIRouter(tags=["programme"])
MAPPING_PATH = ROOT / "config" / "dhis2_mapping.json"
AREA = {"name": "Gikomero Sector pilot", "sector": "Gikomero", "district": "Gasabo", "province": "Kigali City"}


# ------------------------------------------------------------------ helpers
def _scope(mode: str | None) -> tuple[str, str]:
    """SQL filter on the demo flag, and a human label for it."""
    mode = mode or ("include" if S.DEMO else "exclude")
    if mode == "include":
        return "1=1", "Includes demonstration records" if S.DEMO else "All records"
    if mode == "only":
        return "demo=1", "Demonstration records only"
    return "demo=0", "Operational records only (demonstration records excluded)"


def _d(ts: str | None) -> date | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _dt(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        x = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return x if x.tzinfo else x.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _ratio(n: int, d: int) -> float | None:
    return round(n / d, 3) if d else None


def ind(iid: str, label: str, n: int | None = None, d: int | None = None, value=None, unit: str = "share",
        note: str = "") -> dict:
    """One indicator. With a denominator of zero the status is no_data, never 0%."""
    if value is None and n is not None and d is not None:
        value = _ratio(n, d)
    return {"id": iid, "label": label, "value": value, "numerator": n, "denominator": d, "unit": unit,
            "status": "measured" if value is not None else "no_data", "note": note}


def iso_week(d: date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def week_bounds(week: str | None) -> tuple[date, date, str]:
    """'2026-W45' -> (Monday, Sunday, '2026W45'). Default: the current ISO week."""
    if not week:
        week = iso_week(today())
    try:
        y, w = week.upper().split("-W")
        mon = date.fromisocalendar(int(y), int(w), 1)
    except (ValueError, TypeError):
        raise HTTPException(422, "week must look like 2026-W45.")
    return mon, mon + timedelta(days=6), f"{int(y)}W{int(w)}"


def _rows(sql: str, args: tuple = ()) -> list[dict]:
    with tx() as c:
        return [dict(r) for r in c.execute(sql, args)]


# --------------------------------------------------------------- M&E (RE-AIM)
@router.get("/me/indicators")
def me_indicators(weeks: int = Query(12, ge=1, le=104), demo: Literal["exclude", "include", "only"] | None = None,
                  u: User = Depends(require("read:me"))):
    """Monitoring-and-evaluation indicators grouped by RE-AIM dimension, computed only from records."""
    where, scope_label = _scope(demo)
    end = today()
    start = end - timedelta(days=7 * weeks - 1)
    s0 = start.isoformat()

    obs = _rows(f"SELECT o.*, t.alert_id, t.created_utc AS task_created, t.due_date FROM observations o "
                f"LEFT JOIN tasks t ON t.id=o.task_id WHERE {where.replace('demo', 'o.demo')} AND substr(o.observed_at,1,10) >= ?", (s0,))
    tasks = _rows(f"SELECT * FROM tasks WHERE {where} AND substr(created_utc,1,10) >= ?", (s0,))
    ivs = _rows(f"SELECT * FROM interventions WHERE status != 'rejected' AND {where} AND substr(authorized_utc,1,10) >= ?", (s0,))
    habitats = _rows(f"SELECT * FROM habitats WHERE {where} AND substr(created_utc,1,10) >= ?", (s0,))
    alerts = _rows("SELECT * FROM alert_states WHERE substr(ts_utc,1,10) >= ?", (s0,))
    users = _rows(f"SELECT id, role, active, last_login_utc FROM users WHERE {where} AND active=1")
    n_points = int(len(store().sites))

    # ---- Reach
    pts = {o["site_id"] for o in obs if (o["site_id"] or "").startswith("G")}
    reach = [
        ind("R1", "Monitoring points inspected at least once", len(pts), n_points,
            note="Distinct radar monitoring points with a field observation in the period."),
        ind("R2", "Habitats registered in the field (outside the radar points)", value=len(habitats), unit="count"),
        ind("R3", "High-priority alerts handled (assigned, acknowledged or dismissed)", value=len(alerts), unit="count",
            note="; ".join(f"{k}: {v}" for k, v in sorted(_count(alerts, "state").items())) or "No alert actions recorded."),
    ]

    # ---- Effectiveness (field evidence only)
    ver = [o for o in obs if o["status"] == "verified"]
    led = [o for o in ver if o["alert_id"]]
    other = [o for o in ver if not o["alert_id"]]
    sampled = lambda xs: [o for o in xs if o["larvae_present"] in ("yes", "no")]  # noqa: E731
    pos = lambda xs: sum(o["larvae_present"] == "yes" for o in xs)  # noqa: E731
    wet = lambda xs: sum(o["water_present"] == "yes" for o in xs)  # noqa: E731
    closed = [i for i in ivs if i["status"] == "closed"]
    eff = [
        ind("E1", "Forecast-led inspections that found standing water", wet(led), len(led),
            note="Verified observations from inspections assigned from a forecast alert."),
        ind("E2", "Other inspections that found standing water", wet(other), len(other),
            note="Routine or follow-up inspections. Compare with E1 only when both have enough records."),
        ind("E3", "Forecast-led sampled sites positive for larvae", pos(sampled(led)), len(sampled(led))),
        ind("E4", "Other sampled sites positive for larvae", pos(sampled(other)), len(sampled(other))),
        ind("E5", "Sampled sites positive for Anopheles larvae", sum(o["anopheles_present"] == "yes" for o in sampled(ver)),
            len(sampled(ver))),
        ind("E6", "Closed interventions with larvae absent or reduced at follow-up",
            sum(i["outcome"] in ("larvae_absent", "larvae_reduced") for i in closed), len(closed)),
    ]

    # ---- Adoption
    since_dt = datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc)
    active = [x for x in users if (_dt(x["last_login_utc"]) or since_dt - timedelta(days=1)) >= since_dt]
    by_role = {}
    for x in users:
        r = by_role.setdefault(x["role"], {"role": x["role"], "label": S.ROLE_LABEL.get(x["role"], x["role"]),
                                           "title": S.ROLE_TITLE.get(x["role"]), "accounts": 0, "signed_in": 0})
        r["accounts"] += 1
        r["signed_in"] += x in active
    actors = _rows("SELECT DISTINCT actor FROM audit WHERE substr(ts_utc,1,10) >= ? AND action NOT LIKE 'auth.%' "
                   "AND action NOT IN ('engine.ready','bulletin.render') AND actor NOT IN ('system','public','')", (s0,))
    adoption = [
        ind("A1", "Active accounts that signed in during the period", len(active), len(users)),
        ind("A2", "People who recorded at least one action", value=len(actors), unit="count",
            note="Distinct accounts in the audit trail (tasks, records, reviews, exports), sign-ins excluded."),
    ]

    # ---- Implementation (fidelity to the operating procedure)
    due = [t for t in _rows(f"SELECT * FROM tasks WHERE {where} AND due_date >= ? AND due_date <= ?",
                            (s0, end.isoformat())) if t["status"] != "cancelled"]
    done_by = {o["task_id"]: _d(o["observed_at"]) for o in obs if o["task_id"]}
    on_time = sum(1 for t in due if t["id"] in done_by and done_by[t["id"]] and done_by[t["id"]].isoformat() <= t["due_date"])
    lags = [(_dt(o["observed_at"]) - _dt(o["task_created"])).total_seconds() / 86400 for o in obs
            if o["task_created"] and _dt(o["observed_at"]) and _dt(o["task_created"])]
    reviewed = [o for o in obs if o["reviewed_utc"]]
    quick = sum(1 for o in reviewed if (_dt(o["reviewed_utc"]) - _dt(o["created_utc"])).days <= 3)
    elig = [o for o in ver if o["larvae_present"] == "yes" or (o["water_present"] == "yes" and o["larvae_present"] == "not_sampled")]
    authed = {i["observation_id"] for i in _rows(f"SELECT observation_id FROM interventions WHERE status != 'rejected' AND {where}")}
    gps_ok = sum(1 for o in obs if o["lat"] is not None and (o["gps_accuracy_m"] or 0) <= 30)
    performed = [i for i in ivs if i["status"] in ("performed", "closed")]
    impl = [
        ind("I1", "Inspections completed by their due date", on_time, len(due),
            note="Tasks due in the period (cancelled tasks excluded)."),
        ind("I2", "Median days from assignment to observation", value=round(statistics.median(lags), 1) if lags else None,
            unit="days"),
        ind("I3", "Observations with GPS within 30 m", gps_ok, len(obs)),
        ind("I4", "Observations reviewed within 3 days", quick, len(reviewed)),
        ind("I5", "Eligible verified findings with an intervention authorised",
            sum(o["id"] in authed for o in elig), len(elig)),
        ind("I6", "Treatments closed with a follow-up observation", len(closed), len(performed)),
        ind("I7", "Observations sent to review for a quality flag",
            sum(1 for o in obs if o["quality_flags"]), len(obs),
            note="Lower is better; flags are GPS distance or accuracy, missing GPS, species not identified, new habitat."),
    ]

    # ---- Maintenance: weekly activity
    series = {}
    for k in range(weeks):
        wk = iso_week(start + timedelta(days=7 * k))
        series[wk] = {"week": wk, "observations": 0, "tasks": 0, "treatments": 0, "habitats": 0}
    for o in obs:
        if (w := _d(o["observed_at"])) and iso_week(w) in series:
            series[iso_week(w)]["observations"] += 1
    for t in tasks:
        if (w := _d(t["created_utc"])) and iso_week(w) in series:
            series[iso_week(w)]["tasks"] += 1
    for i in ivs:
        if (w := _d(i["performed_at"])) and iso_week(w) in series:
            series[iso_week(w)]["treatments"] += 1
    for h in habitats:
        if (w := _d(h["created_utc"])) and iso_week(w) in series:
            series[iso_week(w)]["habitats"] += 1
    active_weeks = sum(1 for s in series.values() if s["observations"] or s["tasks"] or s["treatments"])
    maint = [ind("M1", "Weeks with field activity", active_weeks, len(series),
                 note="A week counts when at least one task, observation or treatment was recorded.")]

    return {
        "period": {"from": s0, "to": end.isoformat(), "weeks": weeks}, "scope": scope_label,
        "framework": "RE-AIM (Reach, Effectiveness, Adoption, Implementation, Maintenance)",
        "sections": [
            {"key": "reach", "title": "Reach", "question": "How much of the operating area does the work cover?", "indicators": reach},
            {"key": "effectiveness", "title": "Effectiveness (field evidence)",
             "question": "Do forecast-led inspections find water and larvae more often than other inspections?", "indicators": eff},
            {"key": "adoption", "title": "Adoption", "question": "Are the intended staff using it?", "indicators": adoption,
             "by_role": sorted(by_role.values(), key=lambda r: S.ROLES.index(r["role"]) if r["role"] in S.ROLES else 99)},
            {"key": "implementation", "title": "Implementation fidelity",
             "question": "Is the operating procedure followed as written?", "indicators": impl},
            {"key": "maintenance", "title": "Maintenance", "question": "Is use sustained week after week?", "indicators": maint},
        ],
        "weekly": list(series.values()),
        "not_measured": [
            "Malaria incidence or cases averted: comes from HMIS/DHIS2 case data and needs a comparison design; RINDA does not estimate it.",
            "Adult mosquito density or entomological inoculation rate: needs entomological surveillance.",
            "Cost per site treated or per case averted: Impact & Economics holds a planning model only.",
            "Acceptability and staff experience (CFIR constructs): needs interviews and questionnaires outside RINDA.",
        ],
    }


def _count(xs: list[dict], k: str) -> dict:
    out: dict = {}
    for x in xs:
        out[x[k]] = out.get(x[k], 0) + 1
    return out


# ---------------------------------------------------------- weekly report
def weekly(week: str | None, demo: str | None = None) -> dict:
    mon, sun, period = week_bounds(week)
    where, scope_label = _scope(demo)
    a, b = mon.isoformat(), sun.isoformat()
    in_wk = lambda col: f"substr({col},1,10) BETWEEN ? AND ?"  # noqa: E731
    obs = _rows(f"SELECT * FROM observations WHERE {where} AND {in_wk('observed_at')}", (a, b))
    t_new = _rows(f"SELECT * FROM tasks WHERE {where} AND {in_wk('created_utc')}", (a, b))
    t_open_end = _rows(f"SELECT * FROM tasks WHERE {where} AND status IN ('assigned','in_progress') AND due_date <= ? "
                       f"AND substr(created_utc,1,10) <= ?", (b, b))
    iv_auth = _rows(f"SELECT * FROM interventions WHERE status != 'rejected' AND {where} AND {in_wk('authorized_utc')}", (a, b))
    iv_perf = _rows(f"SELECT * FROM interventions WHERE status != 'rejected' AND {where} AND {in_wk('performed_at')}", (a, b))
    iv_closed = _rows(f"SELECT * FROM interventions WHERE status != 'rejected' AND {where} AND {in_wk('closed_utc')}", (a, b))
    reviews = _rows(f"SELECT status FROM observations WHERE {where} AND {in_wk('reviewed_utc')}", (a, b))
    habitats = _rows(f"SELECT * FROM habitats WHERE {where} AND {in_wk('created_utc')}", (a, b))
    alerts = _rows(f"SELECT * FROM alert_states WHERE {in_wk('ts_utc')}", (a, b))
    sampled = [o for o in obs if o["larvae_present"] in ("yes", "no")]
    positive = [o for o in sampled if o["larvae_present"] == "yes"]
    dens = [o["larvae_count"] / o["dips"] for o in positive if o["dips"] and o["larvae_count"] is not None]
    latest = store().dates(7)[-1]
    fm = forecast_meta(7, latest)
    counts = {
        "lsm_inspections_completed": len(obs),
        "lsm_sites_inspected": len({o["site_id"] for o in obs}),
        "lsm_sites_with_water": len({o["site_id"] for o in obs if o["water_present"] == "yes"}),
        "lsm_sites_sampled": len({o["site_id"] for o in sampled}),
        "lsm_sites_larvae_positive": len({o["site_id"] for o in positive}),
        "lsm_sites_anopheles_positive": len({o["site_id"] for o in positive if o["anopheles_present"] == "yes"}),
        "lsm_dips_total": sum(o["dips"] or 0 for o in sampled),
        "lsm_new_habitats": len(habitats),
        "lsm_sites_treated": len({i["site_id"] for i in iv_perf}),
        "lsm_followups_larvae_absent_or_reduced": sum(i["outcome"] in ("larvae_absent", "larvae_reduced") for i in iv_closed),
    }
    return {
        "week": f"{mon.isocalendar()[0]}-W{mon.isocalendar()[1]:02d}", "period": period, "from": a, "to": b,
        "area": AREA, "scope": scope_label, "generated_utc": now(), "demonstration": S.DEMO,
        "counts": counts,
        "inspections": {"assigned": len(t_new), "forecast_led": sum(1 for t in t_new if t["alert_id"]),
                        "open_and_overdue_at_week_end": len(t_open_end)},
        "observations": {"total": len(obs), "sampled": len(sampled), "larvae_positive": len(positive),
                         "anopheles_positive": sum(o["anopheles_present"] == "yes" for o in positive),
                         "pupae_positive": sum(o["pupae_present"] == "yes" for o in obs),
                         "mean_larvae_per_dip_positive": round(sum(dens) / len(dens), 2) if dens else None,
                         "not_sampled": sum(o["larvae_present"] == "not_sampled" for o in obs)},
        "reviews": _count(reviews, "status"),
        "interventions": {"authorised": len(iv_auth), "performed": len(iv_perf), "closed": len(iv_closed),
                          "outcomes": _count(iv_closed, "outcome"), "methods": _count(iv_perf, "method")},
        "alerts": _count(alerts, "state"),
        "forecast": {"latest_issue": latest, "status": fm["status"], "age_days": fm["age_days"]},
        "statement": "Field records only. RINDA is ready for controlled testing and is not validated for intervention "
                     "decisions; this report does not measure malaria incidence.",
    }


@router.get("/reports/weekly")
def weekly_json(week: str | None = None, demo: Literal["exclude", "include", "only"] | None = None,
                u: User = Depends(require("export:report"))):
    return weekly(week, demo)


@router.get("/reports/weekly.html", response_class=HTMLResponse)
def weekly_html(week: str | None = None, lang: Literal["en", "rw"] = "en",
                demo: Literal["exclude", "include", "only"] | None = None, u: User = Depends(require("export:report"))):
    r = weekly(week, demo)
    ops.audit(u.email, "report.weekly", r["week"])
    return render_weekly(r, lang)


# Draft Kinyarwanda labels: to be reviewed in terminology sessions with RBC before use.
WK = {
    "en": {"title": "Weekly larval source management report", "week": "Epidemiological week", "area": "Operating area",
           "scope": "Records", "core": "Core counts (HMIS-ready)", "insp": "Inspections", "obs": "Field observations",
           "iv": "Interventions", "fc": "Forecast status", "sign": "Prepared by", "check": "Checked by (District Vector Control Officer)",
           "assigned": "Assigned this week", "forecast_led": "of which from forecast alerts", "overdue": "Open and overdue at week end",
           "sampled": "Sampled by dipping", "notsampled": "Not sampled", "pos": "Larvae positive", "anoph": "Anopheles positive",
           "pupae": "Pupae positive", "density": "Mean larvae per dip (positive sites)", "authorised": "Authorised",
           "performed": "Performed", "closed": "Closed with follow-up", "latest": "Latest forecast issue",
           "draft": ""},
    "rw": {"title": "Raporo y'icyumweru yo kurwanya indiri z'imibu", "week": "Icyumweru", "area": "Agace gakorerwamo",
           "scope": "Inyandiko", "core": "Imibare y'ingenzi (HMIS)", "insp": "Igenzura", "obs": "Ibyabonetse mu murima",
           "iv": "Ibikorwa byo gutera umuti", "fc": "Uko iteganyagihe rihagaze", "sign": "Byateguwe na",
           "check": "Byagenzuwe na (Ushinzwe kurwanya imibu mu karere)", "assigned": "Yatanzwe muri iki cyumweru",
           "forecast_led": "muri yo, iturutse ku miburo y'iteganyagihe", "overdue": "Itarakorwa kandi yarengeje igihe",
           "sampled": "Hafashwe ibipimo", "notsampled": "Ntihafashwe ibipimo", "pos": "Habonetse larves",
           "anoph": "Habonetse Anopheles", "pupae": "Habonetse pupes", "density": "Impuzandengo ya larves ku gipimo",
           "authorised": "Byemejwe", "performed": "Byakozwe", "closed": "Byasojwe hakurikiranwa",
           "latest": "Iteganyagihe riheruka", "draft": "UBUSOBANURO BW'AGATEGANYO: amagambo y'Ikinyarwanda ategereje kwemezwa na RBC."},
}
LABELS = {
    "lsm_inspections_completed": "Inspections completed", "lsm_sites_inspected": "Sites / habitats inspected",
    "lsm_sites_with_water": "Sites with standing water", "lsm_sites_sampled": "Sites sampled by dipping",
    "lsm_sites_larvae_positive": "Sites positive for larvae", "lsm_sites_anopheles_positive": "Sites positive for Anopheles",
    "lsm_dips_total": "Dips taken", "lsm_new_habitats": "New habitats registered", "lsm_sites_treated": "Sites treated",
    "lsm_followups_larvae_absent_or_reduced": "Follow-ups: larvae absent or reduced",
}


def render_weekly(r: dict, lang: str = "en") -> str:
    t = WK.get(lang, WK["en"])
    e = html.escape
    row = lambda k, v: f"<tr><th>{e(str(k))}</th><td>{'–' if v is None else e(str(v))}</td></tr>"  # noqa: E731
    o, i, n = r["observations"], r["interventions"], r["inspections"]
    stale = r["forecast"]["status"] == "stale"
    demo = ('<div class="stamp">DEMONSTRATION DATA. Not for reporting.</div>' if r["demonstration"] else "")
    draft = f'<p class="draft">{e(t["draft"])}</p>' if t["draft"] else ""
    return f"""<!doctype html><html lang="{lang}"><head><meta charset="utf-8"><title>{e(t['title'])} {r['week']}</title>
<style>
@page {{ size: A4; margin: 14mm; }}
body {{ font: 12px/1.45 'IBM Plex Sans', Arial, sans-serif; color:#16202a; max-width: 182mm; margin: 0 auto; padding: 10px; }}
h1 {{ font-size: 18px; margin: 0 0 2px; }} h2 {{ font-size: 13px; margin: 16px 0 6px; border-bottom: 1px solid #ccd; padding-bottom: 3px; }}
.sub {{ color:#55606b; margin: 0 0 8px; }} table {{ border-collapse: collapse; width: 100%; }}
th, td {{ text-align: left; padding: 3px 6px; border-bottom: 1px solid #e3e7ea; }} td {{ text-align: right; font-variant-numeric: tabular-nums; }}
.grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 0 18px; }}
.stamp {{ border: 2px solid #c2410c; color: #c2410c; font-weight: 700; padding: 6px 10px; margin: 8px 0; }}
.note {{ background: #f4f6f8; padding: 8px 10px; border-left: 3px solid #2a6fb8; margin: 10px 0; }}
.draft {{ color:#8a5a00; font-style: italic; }} .sig {{ display:grid; grid-template-columns:1fr 1fr; gap: 24px; margin-top: 28px; }}
.sig div {{ border-top: 1px solid #333; padding-top: 4px; }} .no-print {{ margin: 10px 0; }}
@media print {{ .no-print {{ display: none; }} }}
</style></head><body>
<div class="no-print"><button onclick="window.print()">Print / save as PDF</button></div>
{demo}
<h1>RINDA · {e(t['title'])}</h1>
<p class="sub">{e(t['week'])} <b>{r['week']}</b> ({r['from']} to {r['to']}) · {e(t['area'])}: {e(r['area']['name'])}, {e(r['area']['district'])} ·
{e(t['scope'])}: {e(r['scope'])}</p>
{draft}
<div class="note">{e(r['statement'])}</div>
<h2>{e(t['core'])}</h2>
<table>{''.join(row(LABELS[k], v) for k, v in r['counts'].items())}</table>
<div class="grid">
<div><h2>{e(t['insp'])}</h2><table>{row(t['assigned'], n['assigned'])}{row(t['forecast_led'], n['forecast_led'])}{row(t['overdue'], n['open_and_overdue_at_week_end'])}</table></div>
<div><h2>{e(t['obs'])}</h2><table>{row(t['sampled'], o['sampled'])}{row(t['notsampled'], o['not_sampled'])}{row(t['pos'], o['larvae_positive'])}{row(t['anoph'], o['anopheles_positive'])}{row(t['pupae'], o['pupae_positive'])}{row(t['density'], o['mean_larvae_per_dip_positive'])}</table></div>
<div><h2>{e(t['iv'])}</h2><table>{row(t['authorised'], i['authorised'])}{row(t['performed'], i['performed'])}{row(t['closed'], i['closed'])}{''.join(row(k.replace('_', ' '), v) for k, v in i['outcomes'].items())}</table></div>
<div><h2>{e(t['fc'])}</h2><table>{row(t['latest'], r['forecast']['latest_issue'])}{row('Status', ('STALE · ' if stale else '') + str(r['forecast']['age_days']) + ' days old')}</table></div>
</div>
<div class="sig"><div>{e(t['sign'])}</div><div>{e(t['check'])}</div></div>
<p class="sub" style="margin-top:14px">Generated {r['generated_utc']} by RINDA. Counts are distinct sites or habitats unless stated. Source: field records in RINDA.</p>
</body></html>"""


# ------------------------------------------------------------------- DHIS2
def mapping() -> dict:
    return json.loads(MAPPING_PATH.read_text(encoding="utf-8"))


@router.get("/reports/dhis2")
def dhis2(week: str | None = None, format: Literal["json", "csv"] = "json", u: User = Depends(require("export:report"))):
    """Weekly counts as a DHIS2 dataValueSet. Never pushed to a server: the file is for the HMIS team to check
    and import. Unmapped identifiers are left as UNMAPPED so an import fails rather than lands in the wrong place."""
    r = weekly(week, "include" if S.DEMO else "exclude")
    m = mapping()
    unmapped = [k for k, v in m["dataElements"].items() if v["uid"] == "UNMAPPED"]
    org = m["orgUnits"].get(AREA["name"], "UNMAPPED")
    pending = bool(unmapped) or org == "UNMAPPED" or m["dataSet"] == "UNMAPPED"
    comment = "DEMONSTRATION DATA - DO NOT IMPORT" if S.DEMO else "RINDA field records"
    values = [{"dataElement": m["dataElements"][k]["uid"] if m["dataElements"][k]["uid"] != "UNMAPPED" else f"UNMAPPED:{k}",
               "categoryOptionCombo": m["categoryOptionCombo"], "period": r["period"], "orgUnit": org,
               "value": str(v), "comment": comment} for k, v in r["counts"].items() if k in m["dataElements"]]
    ops.audit(u.email, "report.dhis2", f"{r['period']} {format}")
    if format == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["dataelement", "period", "orgunit", "catoptcombo", "attroptcombo", "value", "storedby",
                    "lastupdated", "comment", "followup"])
        for v in values:
            w.writerow([v["dataElement"], v["period"], v["orgUnit"], v["categoryOptionCombo"], "", v["value"],
                        u.email or "rinda", r["generated_utc"], v["comment"], "false"])
        return Response(buf.getvalue(), media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="rinda_dhis2_{r["period"]}.csv"'})
    return {"dataSet": m["dataSet"], "period": r["period"], "orgUnit": org, "completeDate": None,
            "dataValues": values,
            "_rinda": {"mapping_version": m["version"], "mapping_status": "pending" if pending else "mapped",
                       "unmapped_elements": unmapped, "scope": r["scope"], "demonstration": S.DEMO,
                       "note": "Not sent anywhere. Import into DHIS2 only after the HMIS team has filled "
                               "config/dhis2_mapping.json and checked a test import."}}


# ------------------------------------------------------------------ outbox
PROVIDER = os.environ.get("RINDA_SMS_PROVIDER", "").strip() or None
CHANNELS = ["sms", "whatsapp"]

# English texts are final; Kinyarwanda texts are DRAFT and must be reviewed with RBC before field use.
TEMPLATES = {
    "task_notice": {
        "en": "RINDA: inspection T{tid} at {site} is assigned to you, due {due}. Dip and record what you find, "
              "including when no larvae are found.",
        "rw": "RINDA: Wahawe igenzura T{tid} kuri {site}, rigomba gukorwa bitarenze {due}. Fata ibipimo wandike "
              "ibyo wabonye, n'iyo nta larves zabonetse.",
    },
    "weekly_digest": {
        "en": "RINDA {week} {area}: inspections {obs}, larvae found {pos}, Anopheles {anoph}, sites treated {treated}, "
              "tasks overdue {overdue}. Forecast {fresh}. Not validated for treatment decisions.",
        "rw": "RINDA {week} {area}: igenzura {obs}, {pos} harimo larves, {anoph} Anopheles, {treated} hatewe umuti, "
              "{overdue} byarengeje igihe. Iteganyagihe: {fresh}. Ntiryemejwe ku byemezo byo gutera umuti.",
    },
}


def _mask(phone: str | None) -> str | None:
    if not phone:
        return None
    p = phone.replace(" ", "")
    return p[:4] + "*" * max(0, len(p) - 7) + p[-3:] if len(p) > 7 else "***"


def _draft(kind: str, lang: str, body: str, uid: int | None, label: str, actor: User, ref: str,
           channel: str = "sms") -> int:
    status, note = ("queued", f"Provider {PROVIDER}") if PROVIDER else \
        ("draft", "No SMS or WhatsApp provider is connected. Copy the text, send it yourself, then mark it as sent.")
    with tx() as c:
        cur = c.execute("INSERT INTO outbox (created_utc, kind, channel, language, recipient_user_id, recipient_label, "
                        "body, status, status_note, created_by, ref, demo) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (now(), kind, channel, lang, uid, label, body, status, note, actor.id, ref, int(bool(actor.demo))))
        return int(cur.lastrowid)


def draft_task_notice(tid: int, actor: User) -> int | None:
    """Called when a task is assigned: prepares the notice in the assignee's language."""
    with tx() as c:
        t = c.execute("SELECT t.*, u.name, u.language, u.phone FROM tasks t JOIN users u ON u.id=t.assignee_id "
                      "WHERE t.id=?", (tid,)).fetchone()
    if not t:
        return None
    lang = t["language"] if t["language"] in ("en", "rw") else "en"
    body = TEMPLATES["task_notice"][lang].format(tid=tid, site=t["site_id"], due=t["due_date"])
    return _draft("task_notice", lang, body, t["assignee_id"], t["name"], actor, f"T{tid}")


class DigestIn(BaseModel):
    week: str | None = None
    channel: Literal["sms", "whatsapp"] = "sms"


@router.post("/messages/digest", status_code=201)
def draft_digest(b: DigestIn, u: User = Depends(require("write:outbox"))):
    """Weekly digest for every active supervisor and programme manager, in each person's language."""
    r = weekly(b.week)
    with tx() as c:
        people = [dict(x) for x in c.execute(
            "SELECT id, name, language FROM users WHERE active=1 AND role IN ('supervisor','programme_manager') AND demo=?",
            (int(bool(u.demo)),))]
    ids = []
    for p in people:
        lang = p["language"] if p["language"] in ("en", "rw") else "en"
        fresh = {"en": "STALE" if r["forecast"]["status"] == "stale" else "current",
                 "rw": "RYARENZE IGIHE" if r["forecast"]["status"] == "stale" else "rigezweho"}[lang]
        body = TEMPLATES["weekly_digest"][lang].format(
            week=r["week"], area=AREA["sector"], obs=r["observations"]["total"], pos=r["observations"]["larvae_positive"],
            anoph=r["observations"]["anopheles_positive"], treated=r["counts"]["lsm_sites_treated"],
            overdue=r["inspections"]["open_and_overdue_at_week_end"], fresh=fresh)
        ids.append(_draft("weekly_digest", lang, body, p["id"], p["name"], u, r["week"], b.channel))
    ops.audit(u.email, "outbox.digest", f"{r['week']} n={len(ids)}")
    return {"drafted": len(ids), "ids": ids}


@router.get("/messages")
def list_messages(u: User = Depends(require("read:outbox"))):
    with tx() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT m.*, u.phone FROM outbox m LEFT JOIN users u ON u.id=m.recipient_user_id ORDER BY m.id DESC LIMIT 300")]
    for r in rows:
        r["phone"] = _mask(r["phone"])
        r["chars"] = len(r["body"])
        r["sms_parts"] = 1 if len(r["body"]) <= 160 else -(-len(r["body"]) // 153)
    return {"provider": PROVIDER, "provider_status": "connected" if PROVIDER else "not_connected", "messages": rows}


class MessageMark(BaseModel):
    status: Literal["sent_manually", "cancelled"]
    note: str = Field(default="", max_length=300)


@router.post("/messages/{mid}")
def mark_message(mid: int, b: MessageMark, u: User = Depends(require("write:outbox"))):
    with tx() as c:
        m = c.execute("SELECT status FROM outbox WHERE id=?", (mid,)).fetchone()
        if not m:
            raise HTTPException(404, "Unknown message.")
        if m["status"] not in ("draft", "queued"):
            raise HTTPException(409, f"Message is already {m['status']}.")
        c.execute("UPDATE outbox SET status=?, status_note=? WHERE id=?",
                  (b.status, (b.note or f"by {u.email}")[:300], mid))
    ops.audit(u.email, f"outbox.{b.status}", f"M{mid}")
    return {"ok": True}

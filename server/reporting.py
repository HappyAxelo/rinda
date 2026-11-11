"""Programme reporting in four groups, computed only from records.

An indicator with no underlying records has status "no_data" and a null value.
Costs need programme cost records, which RINDA does not hold yet, so they say so
instead of borrowing planning estimates. Nothing here measures malaria.
"""
from __future__ import annotations

import csv
import io
import json
from collections import Counter, defaultdict
from datetime import datetime

from fastapi import APIRouter, Depends
from fastapi.responses import Response

import connectors as C
import ops
import surveillance as V
from data import store
from db import tx
from programme import _scope, ind
from security import User, require

router = APIRouter(tags=["reporting"])
CLAIM = "Records of programme activity. Larval reduction is not a measured reduction in malaria incidence, and RINDA does not measure malaria."


def _days(a: str | None, b: str | None) -> float | None:
    try:
        return (datetime.fromisoformat(b.replace("Z", "+00:00")).replace(tzinfo=None) - datetime.fromisoformat(a.replace("Z", "+00:00")).replace(tzinfo=None)).total_seconds() / 86400
    except (AttributeError, TypeError, ValueError):
        return None


def _median(xs: list[float]) -> float | None:
    xs = sorted(x for x in xs if x is not None)
    return round(xs[len(xs) // 2], 2) if xs else None


def _flight_hours() -> tuple[float | None, int]:
    with tx() as c:
        rows = c.execute("SELECT payload FROM flight_logs WHERE provenance='FIELD'").fetchall()
    total, n = 0.0, 0
    for r in rows:
        spans = defaultdict(list)
        for e in json.loads(r["payload"]):
            spans[e.get("sortie_id")].append(e.get("ts_utc"))
        for ts in spans.values():
            ts = sorted(t for t in ts if t)
            if len(ts) > 1:
                total += (_days(ts[0], ts[-1]) or 0) * 24
        n += 1
    return (round(total, 2) if n else None), n


def indicators(start: str | None = None, end: str | None = None) -> dict:
    idx = V.field_index(start, end)
    obs, ivs, tasks = idx["obs"], idx["ivs"], idx["tasks"]
    st = store()
    done = [t for t in tasks if t["status"] == "completed"]
    sampled = [o for o in obs if o["larvae_present"] in ("yes", "no")]
    verified = [o for o in obs if o["status"] == "verified"]
    pos = [o for o in sampled if o["larvae_present"] == "yes"]
    ivok = [i for i in ivs if i["status"] != "rejected"]
    perf = [i for i in ivok if i["status"] in ("performed", "closed")]
    closed = [i for i in ivok if i["status"] == "closed"]
    tby = {}
    for o in obs:
        if o["task_id"]:
            tby[o["task_id"]] = o
    insp_days = [_days(t["created_utc"], tby[t["id"]]["observed_at"]) for t in done if t["id"] in tby]
    oby = {o["id"]: o for o in obs}
    treat_days = [_days(oby[i["observation_id"]]["observed_at"], i["performed_at"]) for i in perf if i["observation_id"] in oby]
    operational = [
        ind("hab_reg", "Habitats registered in the field", value=len(idx["habs"]) or None, unit="count"),
        ind("hab_insp", "Habitats inspected", value=len({o["site_id"] for o in obs}) or None, unit="count"),
        ind("insp_done", "Inspections completed", len(done), len(tasks)) if tasks else ind("insp_done", "Inspections completed"),
        ind("positive", "Larvae-positive habitats", value=len({o["site_id"] for o in pos}) or None if sampled else None, unit="count"),
        ind("auth", "Interventions authorised", value=len(ivok) or None, unit="count"),
        ind("completed", "Interventions completed", len(perf), len(ivok)) if ivok else ind("completed", "Interventions completed"),
        ind("followup", "Follow-up completion", len(closed), len(perf)) if perf else ind("followup", "Follow-up completion"),
        ind("t_insp", "Median inspection turnaround", value=_median(insp_days), unit="days"),
        ind("t_treat", "Median treatment turnaround", value=_median(treat_days), unit="days", note="From field finding to recorded treatment."),
    ]

    thr = st.water_thresholds
    s = st.s1.copy()
    if start:
        s = s[s.date >= start]
    if end:
        s = s[s.date <= end]
    s["wet"] = (s.vv_db <= thr["vv_db_max"]) & (s.vh_db <= thr["vh_db_max"])
    s = s.sort_values(["site_id", "date"])
    prev = s.groupby("site_id", observed=True)["wet"].shift(1)
    s["onset"] = s["wet"] & (prev == False)  # noqa: E712
    m = st.meteo
    if start:
        m = m[m.date >= start]
    if end:
        m = m[m.date <= end]
    months = sorted(set(s.date.dt.strftime("%Y-%m")))[-36:]
    onset_m = s.groupby(s.date.dt.strftime("%Y-%m")).onset.sum().to_dict() if len(s) else {}
    rain_m = m.groupby(m.date.dt.strftime("%Y-%m")).rainfall_mm.sum().to_dict() if len(m) else {}
    fr = C.freshness()
    with tx() as c:
        node_packets = c.execute("SELECT COUNT(DISTINCT station_id) FROM telemetry").fetchone()[0]
    environmental = [
        ind("sw_change", "Observed dry-to-wet radar transitions", value=int(s.onset.sum()) if len(s) else None, unit="count", note="Satellite-observed. Sentinel-1C is not harmonised, so recent counts are inflated."),
        ind("rain", "Rainfall recorded at the station", value=round(float(m.rainfall_mm.sum()), 1) if len(m) else None, unit="mm", note="Gikomero station. Record ends 15 January 2026."),
        ind("persist", "Radar passes classified wet", int(s.wet.sum()), int(len(s))) if len(s) else ind("persist", "Radar passes classified wet"),
        ind("iot_cov", "IoT nodes reporting", value=node_packets or None, unit="count", note="" if node_packets else "No field node has reported yet."),
        ind("avail", "Data sources holding records", sum(x["availability"] == "available" for x in fr), len(fr)),
        ind("connect", "Sensor connectivity", note="Needs commissioned nodes."),
    ]

    with tx() as c:
        where = _scope(None)[0]
        res = c.execute(f"SELECT COUNT(*) FROM entomological_samples WHERE resistance_finding IS NOT NULL AND {where}").fetchone()[0]
    species = Counter(sp for o in obs for sp in (o["species"] or "").split(",") if sp)
    dens = [o["larvae_count"] / o["dips"] for o in pos if o["dips"] and o["larvae_count"] is not None]
    pupae = [o for o in obs if o["pupae_present"] in ("yes", "no")]
    per_site = Counter(o["site_id"] for o in pos)
    entomological = [
        ind("lp", "Larvae-positive proportion", len(pos), len(sampled)) if sampled else ind("lp", "Larvae-positive proportion"),
        ind("density", "Mean larvae per dip, positive habitats", value=round(sum(dens) / len(dens), 2) if dens else None, unit="per dip", note="All larvae counted, unless the Anopheles count was recorded."),
        ind("pp", "Pupae-positive proportion", sum(o["pupae_present"] == "yes" for o in pupae), len(pupae)) if pupae else ind("pp", "Pupae-positive proportion"),
        ind("repeat", "Habitats positive more than once", value=sum(v > 1 for v in per_site.values()) or None if pos else None, unit="count"),
        ind("res", "Resistance surveillance findings", value=res or None, unit="count", note="" if res else "No resistance data has been supplied."),
    ]
    fh, nlogs = _flight_hours()
    with tx() as c:
        qty = c.execute(f"SELECT COUNT(*) FROM interventions WHERE quantity_used IS NOT NULL AND {_scope(None)[0]}").fetchone()[0]
    cost_note = "Needs recorded programme costs. Planning estimates are kept on Impact & Economics and are not used here."
    programme_perf = [
        ind("district", "Sectors with operational coverage", value=1, unit="sectors", note="Gikomero pilot only."),
        ind("cost_insp", "Cost per inspected habitat", unit="RWF", note=cost_note),
        ind("cost_treat", "Cost per treated habitat", unit="RWF", note=cost_note),
        ind("hours", "Drone flight hours", value=fh, unit="hours", note="From FIELD flight logs only." if nlogs else "No field flight logs recorded."),
        ind("product", "Treatments with product use recorded", value=qty or None, unit="count", note="Quantities are free text until a unit standard is agreed."),
        ind("coverage", "Intervention coverage of positive habitats", len({i["site_id"] for i in perf}), len({o["site_id"] for o in pos})) if pos else ind("coverage", "Intervention coverage of positive habitats"),
        ind("fu2", "Follow-up completion", len(closed), len(perf)) if perf else ind("fu2", "Follow-up completion"),
    ]
    return {"scope": _scope(None)[1], "claim": CLAIM, "start": start, "end": end, "no_data_label": V.NODATA,
            "groups": [{"key": "operational", "label": "Operational performance", "indicators": operational},
                       {"key": "environmental", "label": "Environmental surveillance", "indicators": environmental},
                       {"key": "entomological", "label": "Entomological surveillance", "indicators": entomological},
                       {"key": "programme", "label": "Programme performance", "indicators": programme_perf}],
            "trends": {"months": months, "radar_onsets": [int(onset_m.get(x, 0)) for x in months], "rainfall_mm": [round(float(rain_m[x]), 1) if x in rain_m else None for x in months]},
            "species": dict(species), "by_cell": _by_cell(obs)}


def _by_cell(obs: list[dict]) -> dict:
    out: dict = defaultdict(lambda: {"inspections": 0, "positive": 0})
    for o in obs:
        ar = V.site_area(o["site_id"])
        k = ar["cell"] or ("Gikomero Sector (cell boundaries not loaded)" if ar["in_sector"] else "Outside the sector")
        out[k]["inspections"] += 1
        out[k]["positive"] += o["larvae_present"] == "yes"
    return out


@router.get("/reports/indicators")
def report(start: str | None = None, end: str | None = None, u: User = Depends(require("read:me"))):
    return indicators(start, end)


def _fmt(i: dict) -> str:
    if i["value"] is None:
        return V.NODATA
    return f"{i['value'] * 100:.1f}%" if i["unit"] == "share" else f"{i['value']} {i['unit']}"


@router.get("/reports/indicators.csv")
def report_csv(start: str | None = None, end: str | None = None, u: User = Depends(require("export:report"))):
    r = indicators(start, end)
    b = io.StringIO()
    w = csv.writer(b)
    w.writerow(["group", "indicator", "value", "unit", "numerator", "denominator", "status", "note", "scope"])
    for g in r["groups"]:
        for i in g["indicators"]:
            w.writerow([g["label"], i["label"], i["value"] if i["value"] is not None else V.NODATA, i["unit"], i["numerator"], i["denominator"], i["status"], i["note"], r["scope"]])
    ops.audit(u.email, "export.report", "indicators.csv")
    return Response(b.getvalue(), media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="rinda_indicators.csv"'})


def _pdf(lines: list[tuple[str, int, bool]]) -> bytes:
    """A small text-only PDF writer, so reports need no extra dependency."""
    esc = lambda s: s.encode("latin-1", "replace").decode("latin-1").replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")  # noqa: E731
    pages, cur, y = [], [], 800
    for text, size, bold in lines:
        if y < 50:
            pages.append(cur)
            cur, y = [], 800
        cur.append(f"BT /{'F2' if bold else 'F1'} {size} Tf 48 {y} Td ({esc(text)}) Tj ET")
        y -= size + 6
    pages.append(cur)
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", None, "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>", "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>"]
    kids = []
    for p in pages:
        s = "\n".join(p)
        objs.append(f"<< /Length {len(s)} >>\nstream\n{s}\nendstream")
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> /Contents {len(objs)} 0 R >>")
        kids.append(f"{len(objs)} 0 R")
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>"
    out, offs = "%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n"
    x = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n" + "".join(f"{o:010d} 00000 n \n" for o in offs)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{x}\n%%EOF\n"
    return out.encode("latin-1")


@router.get("/reports/indicators.pdf")
def report_pdf(start: str | None = None, end: str | None = None, u: User = Depends(require("export:report"))):
    r = indicators(start, end)
    lines = [("RINDA programme report", 18, True), (f"Gikomero Sector pilot. Period: {start or 'all records'} to {end or 'latest'}.", 10, False), (r["scope"], 10, False),
             (V.AUDIT["title"], 10, True), (V.AUDIT["summary"][:110], 9, False), (r["claim"][:110], 9, False), ("", 8, False)]
    for g in r["groups"]:
        lines.append((g["label"], 13, True))
        for i in g["indicators"]:
            lines.append((f"{i['label']}: {_fmt(i)}", 10, False))
        lines.append(("", 6, False))
    ops.audit(u.email, "export.report", "indicators.pdf")
    return Response(_pdf(lines), media_type="application/pdf", headers={"Content-Disposition": 'attachment; filename="rinda_indicators.pdf"'})

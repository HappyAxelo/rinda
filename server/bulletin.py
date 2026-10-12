"""Printable operational bulletin (A4). Self-contained HTML, no external assets
except fonts, so it can be saved as PDF from any browser or emailed as a file."""
from __future__ import annotations

import html
import math
from datetime import datetime, timezone

from data import STATION

CAT_COL = {"high": "#c2410c", "moderate": "#b7791f", "insufficient": "#2a6fb8", "lower": "#b9c3cc"}
CAT_TXT = {"high": "High priority for verification", "moderate": "Moderate priority for verification",
           "insufficient": "Insufficient evidence to classify", "lower": "Lower current priority"}
CAT_SHORT = {"high": "High", "moderate": "Moderate", "insufficient": "Insufficient evidence", "lower": "Lower"}


def _svg_map(rows: list[dict], plan: dict, station: dict, w: int = 520, hgt: int = 400) -> str:
    lats = [r["latitude"] for r in rows] + [station["lat"]]
    lons = [r["longitude"] for r in rows] + [station["lon"]]
    la0, la1, lo0, lo1 = min(lats), max(lats), min(lons), max(lons)
    pad = 18
    kx = (w - 2 * pad) / (lo1 - lo0) / math.cos(math.radians((la0 + la1) / 2))
    ky = (hgt - 2 * pad) / (la1 - la0)
    k = min(kx, ky)
    cx = math.cos(math.radians((la0 + la1) / 2))

    def xy(lat, lon):
        return pad + (lon - lo0) * k * cx, hgt - pad - (lat - la0) * k

    parts = [f'<svg viewBox="0 0 {w} {hgt}" width="100%" xmlns="http://www.w3.org/2000/svg" role="img" '
             f'aria-label="Map of monitored sites, alerts and planned sorties">',
             f'<rect width="{w}" height="{hgt}" fill="#f6f7f8" rx="4"/>']
    for r in sorted(rows, key=lambda r: (r["category"] == "high", r["probability"])):
        x, y = xy(r["latitude"], r["longitude"])
        c = r["category"]
        if c == "high":
            parts.append(f'<path d="M{x:.1f} {y-5:.1f} l4.5 8 h-9 z" fill="{CAT_COL[c]}" stroke="#222" stroke-width=".6"/>')
        elif c in ("insufficient", "moderate"):
            parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.2" fill="#fff" stroke="{CAT_COL[c]}" stroke-width="1.4" stroke-dasharray="2 1"/>')
        else:
            parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="1.8" fill="{CAT_COL[c]}"/>')
    base = plan.get("base") or (station["lat"], station["lon"])
    for s in plan.get("sorties", []):
        pts = [xy(*base)] + [xy(wp["lat"], wp["lon"]) for wp in s["waypoints"]] + [xy(*base)]
        d = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
        parts.append(f'<polyline points="{d}" fill="none" stroke="#1d3b53" stroke-width="1.1" '
                     f'stroke-dasharray="3 2" opacity="0.8"/>')
    bx, by = xy(*base)
    parts.append(f'<rect x="{bx-5:.1f}" y="{by-5:.1f}" width="10" height="10" fill="#1d3b53"/>'
                 f'<text x="{bx+8:.1f}" y="{by+4:.1f}" font-size="10" fill="#1d3b53">Launch / AWS</text>')
    # scale bar: 1 km
    km_px = 1000 / 111_320 * k
    parts.append(f'<line x1="{pad}" y1="{hgt-8}" x2="{pad+km_px:.1f}" y2="{hgt-8}" stroke="#333" stroke-width="2"/>'
                 f'<text x="{pad}" y="{hgt-12}" font-size="9" fill="#333">1 km</text>')
    parts.append(f'<text x="{w-pad}" y="{pad+4}" font-size="11" text-anchor="end" fill="#333">N &#8593;</text>')
    parts.append("</svg>")
    return "".join(parts)


def render_bulletin(fc: dict, plan: dict, st, h: int, card: dict, area: dict, lang: str = "en") -> str:
    s = fc["summary"]
    rows = fc["rows"]
    meta = fc["meta"]
    alerts = [r for r in rows if r["category"] == "high"]
    unc = [r for r in rows if r["category"] == "insufficient"]
    top = sorted([r for r in rows if r["category"] != "lower"], key=lambda r: (r["category"] != "high", -(r["priority_score"] or 0)))[:20]
    t = plan.get("totals", {})
    m = card["horizons"][h]
    gen = datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC")
    issue = datetime.strptime(s["issue_date"], "%Y-%m-%d").strftime("%d %B %Y")
    target = datetime.strptime(s["target_date"], "%Y-%m-%d").strftime("%d %B %Y")
    ref = f"RINDA-BUL-{s['issue_date'].replace('-', '')}-H{h:02d}"
    risk_rows = "".join(
        f'<tr><td><span class="sw" style="background:{CAT_COL[k]}"></span>{CAT_TXT[k]}</td>'
        f'<td class="n">{s["categories"].get(k, 0) if k != "moderate" or s["categories"].get(k) else "not configured"}</td></tr>'
        for k in ("high", "moderate", "insufficient", "lower"))
    trows = "".join(
        f'<tr><td class="n">{i}</td><td class="mono">{html.escape(r["site_id"])}</td>'
        f'<td class="mono">{r["latitude"]:.5f}, {r["longitude"]:.5f}</td>'
        f'<td class="n">{r["probability"]:.2f}</td>'
        f'<td><span class="sw" style="background:{CAT_COL[r["category"]]}"></span>{CAT_SHORT[r["category"]]}</td>'
        f'<td>{r["uncertainty_band"]}</td>'
        f'<td class="n">{(r["priority_score"] or 0):.3f}</td></tr>'
        for i, r in enumerate(top, 1))
    srows = "".join(
        f'<tr><td class="mono">{x["id"]}</td><td class="n">{x["n_targets"]}</td><td class="n">{x["n_treat"]}</td>'
        f'<td class="n">{x["n_verify_only"]}</td><td class="n">{x["distance_m"]/1000:.2f}</td>'
        f'<td class="n">{x["flight_s"]/60:.1f}</td><td class="n">{x["spray_l"]:.1f}</td></tr>'
        for x in plan.get("sorties", []))
    outcome = ""
    if "outcome" in s:
        o = s["outcome"]
        outcome = (f'<div class="kpi"><b>{o["hits"]}</b><span>alerts confirmed by later radar pass</span></div>'
                   f'<div class="kpi"><b>{o["misses"]}</b><span>formations not alerted</span></div>')
    sc = m["test_scores"]
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Habitat Formation Bulletin {s['issue_date']}</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
@page {{ size: A4; margin: 14mm 13mm; }}
* {{ box-sizing: border-box; }}
body {{ font-family: 'IBM Plex Sans', system-ui, sans-serif; color: #1a1f24; margin: 0; background: #e9ecef; font-size: 10.5pt; line-height: 1.45; }}
.page {{ width: 210mm; min-height: 297mm; margin: 12px auto; background: #fff; padding: 14mm 13mm; box-shadow: 0 1px 4px rgba(0,0,0,.15); }}
@media print {{ body {{ background: #fff; }} .page {{ margin: 0; box-shadow: none; padding: 0; width: auto; min-height: 0; }} .noprint {{ display: none; }} }}
header {{ display: flex; justify-content: space-between; align-items: flex-end; border-bottom: 2px solid #1d3b53; padding-bottom: 8px; }}
.brand {{ font-weight: 600; letter-spacing: .14em; font-size: 9pt; color: #1d3b53; }}
h1 {{ font-size: 17pt; margin: 4px 0 0; font-weight: 600; letter-spacing: -.01em; }}
.meta {{ text-align: right; font-size: 8.5pt; color: #4a5560; }}
.mono {{ font-family: 'IBM Plex Mono', monospace; font-size: 8.5pt; }}
.band {{ background: #f3f5f7; border-left: 3px solid #e0691a; padding: 7px 10px; margin: 12px 0; font-size: 9.5pt; }}
.kpis {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; margin: 12px 0; }}
.kpi {{ border: 1px solid #d5dbe0; padding: 8px 10px; border-radius: 3px; }}
.kpi b {{ display: block; font-size: 17pt; font-weight: 600; font-variant-numeric: tabular-nums; }}
.kpi span {{ font-size: 8pt; color: #4a5560; }}
h2 {{ font-size: 10.5pt; text-transform: uppercase; letter-spacing: .08em; color: #1d3b53; margin: 16px 0 6px; border-bottom: 1px solid #d5dbe0; padding-bottom: 3px; }}
table {{ width: 100%; border-collapse: collapse; font-size: 8.8pt; }}
th {{ text-align: left; font-weight: 500; color: #4a5560; border-bottom: 1px solid #b9c2ca; padding: 4px 5px; }}
td {{ border-bottom: 1px solid #eceff1; padding: 3.5px 5px; }}
td.n, th.n {{ text-align: right; font-variant-numeric: tabular-nums; }}
.sw {{ display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px; vertical-align: 0; }}
.grid2 {{ display: grid; grid-template-columns: 1.45fr 1fr; gap: 14px; align-items: start; }}
.small {{ font-size: 8pt; color: #4a5560; }}
footer {{ margin-top: 18px; border-top: 1px solid #d5dbe0; padding-top: 6px; font-size: 7.8pt; color: #4a5560; display: flex; justify-content: space-between; }}
.sig {{ display: grid; grid-template-columns: 1fr 1fr; gap: 24px; margin-top: 18px; font-size: 8.5pt; }}
.sig div {{ border-top: 1px solid #1a1f24; padding-top: 4px; margin-top: 26px; }}
.bar {{ position: fixed; top: 10px; right: 14px; }}
.bar button {{ font: inherit; padding: 6px 12px; border: 1px solid #1d3b53; background: #1d3b53; color: #fff; border-radius: 3px; cursor: pointer; }}
</style></head><body>
<div class="bar noprint"><button onclick="window.print()">Print / save PDF</button></div>
<div class="page">
<header><div><div class="brand">RINDA &middot; VECTOR HABITAT INTELLIGENCE</div>
<h1>Habitat Formation Bulletin &mdash; {h}-day outlook</h1>
<div class="small">Gikomero Sector, Gasabo District, Kigali City</div></div>
<div class="meta"><div class="mono">{ref}</div><div>Issued for {issue}</div><div>Valid to {target}</div><div>Generated {gen}</div></div></header>

{'<div class="band" style="border-left-color:#b3261e;background:#fbecea"><b>HISTORICAL FORECAST.</b> Issued ' + str(meta["age_days"]) + ' days ago. It describes the past and must not be used to plan current work.</div>' if meta["status"] == "stale" else ''}
<div class="band"><b>What this bulletin forecasts.</b> The appearance of new surface water, seen by Sentinel-1 radar, at 50 m monitoring
points that are dry today. It does not confirm mosquito larvae, vector presence or malaria risk.
Points marked <i>Insufficient evidence</i> cannot be classified by the model; only an inspection can.
<b>Model status: research stage.</b> A simple persistence rule currently scores as well as the model, and outcomes are not yet
validated in the field. Use this bulletin to prioritise inspections; a forecast never authorises treatment.
Mode: <b>hindcast replay</b> (no numerical weather forecast connected).</div>

<div class="kpis">
<div class="kpi"><b>{s['n_sites']}</b><span>sites assessed</span></div>
<div class="kpi"><b>{len(alerts)}</b><span>high priority for verification</span></div>
<div class="kpi"><b>{len(unc)}</b><span>insufficient evidence: inspect to classify</span></div>
<div class="kpi"><b>{t.get('sorties', 0)}</b><span>sorties planned, {t.get('flight_min', 0):.0f} min total flight</span></div>
{outcome}
</div>

<div class="grid2"><div><h2>Forecast map</h2>{_svg_map(rows, plan, STATION)}
<div class="small">Triangles: high priority. Dashed rings: insufficient evidence. Dots: lower priority. Dashed lines: candidate sortie routes (flight requires supervisor approval).</div></div>
<div><h2>Verification priority</h2><table><tr><th>Class</th><th class="n">Sites</th></tr>{risk_rows}</table>
<h2>Mission summary</h2><table>
<tr><td>Targets routed</td><td class="n">{t.get('targets', 0)}</td></tr>
<tr><td>Verify, treat only if authorised</td><td class="n">{t.get('treat', 0)}</td></tr>
<tr><td>Verify only</td><td class="n">{t.get('verify_only', 0)}</td></tr>
<tr><td>Route distance</td><td class="n">{t.get('distance_km', 0):.1f} km</td></tr>
<tr><td>Spray volume (max.)</td><td class="n">{t.get('spray_l', 0):.1f} L</td></tr>
<tr><td>Bti product (max.)</td><td class="n">{t.get('bti_kg', 0):.2f} kg</td></tr>
<tr><td>Full-zone coverage instead</td><td class="n">{plan['baseline']['wet_prone_zone']['flight_min']:.0f} min</td></tr>
</table></div></div>

<h2>Points to inspect first</h2>
<table><tr><th class="n">#</th><th>Site</th><th>Position (lat, lon)</th><th class="n">Prob.</th><th>Priority</th><th>Uncertainty</th><th class="n">Priority</th></tr>{trows}</table>

<h2>Sorties</h2>
<table><tr><th>Sortie</th><th class="n">Targets</th><th class="n">Treat</th><th class="n">Verify</th><th class="n">km</th><th class="n">min</th><th class="n">Spray L</th></tr>{srows}</table>

<h2>Forecast reliability</h2>
<p class="small">Model fitted on 2023-03 to 2024-12, calibrated on 2025-01 to 2025-08 and tested once on 2025-09 onward.
On that untouched test window the {h}-day model reached PR-AUC {sc['pr_auc']:.3f} against a base rate of {sc['base_rate']:.3f}
(skill {sc['pr_auc']/sc['base_rate']:.1f}&times; chance), recall {sc['recall']:.2f} and precision {sc['precision']:.2f} at the operating threshold.
Accuracy is not reported because predicting &ldquo;no formation&rdquo; everywhere would already exceed 96%.
Weather inputs come from a single station, so spatial differences come from radar alone.</p>

<div class="sig"><div>Reviewed by (District Vector Control)</div><div>Approved for flight (Programme Manager)</div></div>
<footer><span>Model {m['fingerprint']} &middot; engine {card['engine_version']} &middot; research stage &middot; valid {meta['valid_window'][0]} to {meta['valid_window'][1]}</span><span>{ref} &middot; page 1 of 1</span></footer>
</div></body></html>"""

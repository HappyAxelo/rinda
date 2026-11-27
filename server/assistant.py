"""Ask the map: questions about RINDA's map answered from live data, with links that open the answer on the map.

A free chat model answers through read-only tools that call RINDA's own map functions as the signed-in user, so it sees
exactly what that user may see. Each tool result carries ready-made map links; the panel renders only links into RINDA
(#/...). The model is reached through the OpenAI-style chat interface that Groq, Google Gemini and Ollama all offer, so
the provider can be changed by setting, with no code change. Conversations are kept on the server for an hour.

Off by default, because questions and map data are sent to the chosen provider. The operator sets, on the server:
  RINDA_ASSISTANT=on                      explicit opt-in
  RINDA_LLM_PROVIDER=groq                 groq (default), gemini, or ollama (a model running on this computer); or the file ~/.rinda/llm_provider
  RINDA_LLM_API_KEY=<key>                 from the provider's free plan (or the file ~/.rinda/llm_api_key); not needed for ollama
  RINDA_LLM_MODEL=<model id>              optional; each provider has a default below
  RINDA_ASSISTANT_HOURLY=40               optional: questions per user per hour
Free plans are rate-limited (Groq: about 30 requests a minute and 8,000 tokens a minute on the default model), so tool
results are kept short and only the last few questions are resent. A key from a free account with no payment method
cannot be charged.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from collections import defaultdict, deque
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

import ops
import surveillance as SV
from security import User, require

router = APIRouter(tags=["assistant"])
MAX_ROUNDS = 5          # tool rounds per question
MAX_TURNS = 20          # questions per conversation; then a new one starts
HISTORY_TURNS = 4       # earlier questions and answers resent as plain text (keeps within free token limits)
TOOL_CHARS = 6000       # cap on one tool result sent to the model
TTL_S = 3600
_CONV: dict[str, dict] = {}
_RATE: dict[str, deque] = defaultdict(deque)
_LOCK = threading.Lock()

PROVIDERS = {
    # Groq's free limits apply per model (8,000 tokens a minute each in Nov 2026), so a model at its limit hands over to the next
    "groq": {"name": "Groq (free plan)", "base": "https://api.groq.com/openai/v1", "model": "openai/gpt-oss-120b", "needs_key": True,
             "fallbacks": ["openai/gpt-oss-20b", "qwen/qwen3.8-27b"]},
    "gemini": {"name": "Google Gemini (free tier)", "base": "https://generativelanguage.googleapis.com/v1beta/openai", "model": "gemini-flash-latest",
               "needs_key": True},
    "ollama": {"name": "Ollama on this computer", "base": "http://localhost:11434/v1", "model": "qwen2.5:7b", "needs_key": False},
}


KEY_FILE = os.path.join(os.path.expanduser("~"), ".rinda", "llm_api_key")   # outside the repository and OneDrive


def _api_key() -> str:
    k = os.environ.get("RINDA_LLM_API_KEY", "").strip()
    if not k and os.path.isfile(KEY_FILE):
        with open(KEY_FILE, encoding="utf-8-sig") as f:
            k = f.read().strip()
    return k


def provider() -> dict:
    key = os.environ.get("RINDA_LLM_PROVIDER", "").strip().lower()
    pf = os.path.join(os.path.dirname(KEY_FILE), "llm_provider")
    if not key and os.path.isfile(pf):
        with open(pf, encoding="utf-8-sig") as f:
            key = f.read().strip().lower()
    key = key or "groq"
    p = PROVIDERS.get(key, PROVIDERS["groq"]) | {"key": key if key in PROVIDERS else "groq"}
    p["model"] = os.environ.get("RINDA_LLM_MODEL", "").strip() or p["model"]
    p["base"] = os.environ.get("RINDA_LLM_BASE_URL", "").strip() or p["base"]
    return p


def settings() -> dict:
    opted_in = os.environ.get("RINDA_ASSISTANT", "").strip().lower() in ("on", "1", "true")
    p = provider()
    has_key = bool(_api_key()) or not p["needs_key"]
    reason = None
    if not opted_in:
        reason = "Ask the map is switched off on this installation (RINDA_ASSISTANT is not set to on)."
    elif not has_key:
        reason = f"No API key is configured for {p['name']} (RINDA_LLM_API_KEY or ~/.rinda/llm_api_key)."
    return {"enabled": reason is None, "reason": reason, "provider": p["key"], "model": p["model"],
            "sends_to": p["name"] if opted_in else None,
            "note": "Answers come only from RINDA's own records, read with your account's permissions."}


# ------------------------------------------------------------------ links
def map_link(layers: str = "grid", **kw) -> str:
    """#/map?layers=...&date=...&day=d9&earth=new_signal&insector=1&focus=G0442&highlight=a,b"""
    parts = [f"layers={layers}"] + [f"{k}={quote(str(v), safe=',-')}" for k, v in kw.items() if v not in (None, "", False)]
    return "#/map?" + "&".join(parts)


def dossier_link(sid: str) -> str:
    return f"#/dossier?id={quote(sid)}"


# ------------------------------------------------------------------ tools (read-only; called as the signed-in user)
def t_situation(u: User, a: dict) -> dict:
    from datetime import date
    ol = SV.outlook(u)
    g = SV.map_grid20(u)
    fr = SV.freshness(u)
    cands = g.get("candidates", {}).get("features", []) if g.get("available") else []
    sites = []
    for h in ol.get("horizons", []):
        f = SV.map_forecast(h=h["horizon"], date=None, cell=None, model=None, u=u)
        rows = sorted(f.get("rows", []), key=lambda x: -x["probability"])
        hi = [x["site_id"] for x in rows if x["category"] == "high"][:50]
        ins = [x["site_id"] for x in rows if x["category"] == "insufficient"][:50]
        sites.append({"horizon_days": h["horizon"], "issue_date": h["issue_date"], "check_by": h["target_date"], "state": h["state"],
                      "new_water_signal": h["potential_signal"], "of_which_in_sector": h["potential_signal_in_sector"],
                      "cannot_classify": h["evidence_insufficient"],
                      "link_signal_sites": map_link("forecast", h=h["horizon"], highlight=",".join(hi) or None),
                      "link_unclassified_sites": map_link("forecast", h=h["horizon"], highlight=",".join(ins) or None)})
    return {
        "today": date.today().isoformat(),
        "daily_outlook": None if not g.get("available") else {
            "issue_date": g["issue_date"], "radar_pass": g["last_pass"], "state": g["state"], "places": len(cands),
            "inland_places": sum(1 for f in cands if f["properties"]["zone"] != "lake shore"), "link": map_link("grid")},
        "site_forecast": sites,
        "what_to_do": [{"action": x["action"], "count": x["n"], "text": x["text"][:180]} for x in ol.get("guidance", [])],
        "rain_next_7d_mm": (ol.get("weather") or {}).get("rain_7d_mm"),
        "data_freshness": {s_["name"]: f"{s_.get('availability')}, last {s_.get('last_observation') or 'none'}" for s_ in fr["sources"]},
    }


def t_daily_outlook(u: User, a: dict) -> dict:
    g = SV.map_grid20(u)
    if not g.get("available"):
        return {"available": False, "message": g.get("message")}
    days = g["days"]
    ahead = a.get("day_ahead")
    sel = next((d for d in days if d["ahead"] == ahead), None) if ahead else None
    feats = [f["properties"] for f in g["candidates"]["features"]]
    if sel:
        i = days.index(sel)
        feats = [p for p in feats if (p["daily"][i] if i < len(p["daily"]) else 0) >= sel["alert_line"]]
    if a.get("in_sector_only"):
        feats = [p for p in feats if p.get("in_sector")]
    if a.get("zone") in ("lake shore", "inland"):
        feats = [p for p in feats if (p["zone"] == "lake shore") == (a["zone"] == "lake shore")]
    feats.sort(key=lambda p: -p["p_max"])
    lim = min(int(a.get("limit") or 5), 25)
    day = f"d{sel['ahead']}" if sel else None
    return {"summary": f"Outlook issued {g['issue_date']} from the radar pass of {g['last_pass']} (that pass is {g['data_age_days']} days old).",
            "issue_date": g["issue_date"], "radar_pass": g["last_pass"], "radar_pass_age_days": g["data_age_days"], "state": g["state"],
            "day": sel and {"ahead": sel["ahead"], "date": sel["date"]}, "matching_places": len(feats),
            "places": [{"id": p["id"], "chance_max": p["p_max"], "peak_date": p["peak_date"], "first_alert_date": p["first_alert_date"],
                        "area_m2": p["area_m2"], "zone": p["zone"], "in_sector": p["in_sector"], "lat": p["lat"], "lon": p["lon"],
                        "link": map_link("grid", day=day, focus=p["id"])} for p in feats[:lim]],
            "all_link": map_link("grid", day=day, insector=1 if a.get("in_sector_only") else None,
                                 highlight=",".join(p["id"] for p in feats[:lim]) or None),
            "skill_note": "Tested Sep 2025 to Nov 2026: beats the recent-water rule at every lead; inland places are leads to check on foot (Sentinel-2 has not confirmed inland radar water).",
            "caution": g["caution"]}


def t_site_forecast(u: User, a: dict) -> dict:
    h = 14 if a.get("horizon") == 14 else 7
    r = SV.map_forecast(h=h, date=a.get("date"), cell=None, model=None, u=u)
    if not r.get("available"):
        return {"available": False, "message": r.get("message")}
    rows = r["rows"]
    if a.get("in_sector_only"):
        rows = [x for x in rows if x.get("in_sector")]
    counts: dict = defaultdict(int)
    for x in rows:
        counts[x["label"]] += 1
    if a.get("category"):
        rows = [x for x in rows if x["category"] == a["category"]]
    rows = sorted(rows, key=lambda x: -x["probability"])
    lim = min(int(a.get("limit") or 5), 25)
    top = rows[:lim]
    return {"issue_date": r["issue_date"], "target_date": r["target_date"], "horizon_days": h, "state": r["state"], "age_days": r["age_days"],
            "counts_by_label": dict(counts), "matching": len(rows),
            "locations": [{"id": x["site_id"], "probability": x["probability"], "label": x["label"], "in_sector": x["in_sector"],
                           "radar_outcome_if_known": x["observed"], "link": map_link("forecast", h=h, date=r["issue_date"], focus=x["site_id"]),
                           "dossier": dossier_link(x["site_id"])} for x in top],
            "all_link": map_link("forecast", h=h, date=r["issue_date"], highlight=",".join(x["site_id"] for x in top) or None)}


def t_location(u: User, a: dict) -> dict:
    sid = str(a.get("id", "")).strip().upper()
    if sid.startswith("G20-"):
        g = SV.map_grid20(u)
        p = next((f["properties"] for f in g.get("candidates", {}).get("features", []) if f["properties"]["id"] == sid), None)
        if not p:
            return {"found": False, "message": f"{sid} is not in the current daily outlook (issued {g.get('issue_date')})."}
        return {"found": True, "kind": "daily outlook candidate (unverified)", **{k: p[k] for k in ("id", "p_max", "peak_date", "first_alert_date",
                "area_m2", "zone", "in_sector", "lat", "lon", "daily", "dist_permanent_water_m", "status")},
                "daily_dates": [d["date"] for d in g["days"]], "link": map_link("grid", focus=sid)}
    try:
        d = SV.dossier(sid, u)
    except HTTPException as e:
        return {"found": False, "message": e.detail}
    sat = d.get("satellite") or {}
    radar = sat.get("radar") or []
    fc = d.get("forecasts") or {}
    last = lambda k: (fc.get(k) or fc.get(int(k)) or [None])[-1]  # noqa: E731
    out = {"found": True, "id": d["id"], "kind": d["kind"], "lat": d["lat"], "lon": d["lon"], "administrative": d["administrative"],
           "status": d["status_label"], "evidence_tier": d["evidence_tier"],
           "radar": {"passes": sat.get("n_acquisitions"), "share_of_passes_wet": sat.get("wet_fraction"),
                     "last_passes": [{k: p.get(k) for k in ("date", "vv_db", "vh_db", "wet")} for p in radar[-5:]]},
           "latest_forecast_7d": last("7"), "latest_forecast_14d": last("14"),
           "recent_events": d.get("timeline", [])[:5],
           "field_observations": len(d.get("observations") or []) if not d.get("restricted") else "restricted for this account",
           "link": map_link("grid,forecast,earth", focus=d["id"]), "dossier": dossier_link(d["id"])}
    return out


def t_satellite_water(u: User, a: dict) -> dict:
    r = SV.map_earth(date=a.get("date"), compare=None, cell=None, u=u)
    rows = r["rows"]
    scope = "all 600 monitoring locations (inside and around Gikomero Sector)"
    if a.get("in_sector_only"):
        sa = SV.G.site_areas()
        rows = [x for x in rows if sa.get(x["site_id"], {}).get("in_sector")]
        scope = f"the {len(rows)} monitoring locations inside Gikomero Sector"
    wet = [x for x in rows if x.get("wet")]
    new = [x for x in wet if not x.get("previous_wet")]
    lim = min(int(a.get("limit") or 8), 25)
    prev = new[0]["previous_pass"] if new else (rows[0]["previous_pass"] if rows else None)
    from datetime import date as _d
    age = (_d.today() - _d.fromisoformat(r["date"])).days
    return {"summary": f"On the Sentinel-1 pass of {r['date']} ({age} days before today, {_d.today().isoformat()}), {len(wet)} of {scope} showed a "
                       f"radar water signal; {len(new)} of these were new since the previous pass ({prev}).",
            "pass_date": r["date"], "pass_age_days": age, "previous_pass": prev, "scope": scope, "locations_in_scope": len(rows),
            "locations_with_water_signal": len(wet), "locations_with_new_water_signal": len(new),
            "new_signal_examples": [{"id": x["site_id"], "vv_db": x["vv_db"], "link": map_link("earth", date=r["date"], earth="new_signal", focus=x["site_id"])}
                                    for x in new[:lim]],
            "link_water_signal": map_link("earth", date=r["date"], earth="wet"),
            "link_new_signal": map_link("earth", date=r["date"], earth="new_signal", highlight=",".join(x["site_id"] for x in new[:25]) or None),
            "caution": "A radar water signal is not confirmed water on the ground and not mosquito breeding."}


def t_weather(u: User, a: dict) -> dict:
    m = SV.map_meteo(date=None, u=u)
    ol = SV.outlook(u)
    return {"station_record": {k: m.get(k) for k in ("available", "state", "observed_date", "record_ends", "rainfall_mm", "rain_7d_mm", "rain_14d_mm",
                                                     "rain_30d_mm", "temp_mean_c", "humidity_pct")} | {"last_7_days": (m.get("series") or [])[-7:]},
            "rain_forecast": ol.get("weather"), "link": map_link("meteo"),
            "note": "Rainfall added no measurable skill to the water forecast; it is context only."}


def t_field_and_operations(u: User, a: dict) -> dict:
    e = SV.map_entomology(start=a.get("start"), end=a.get("end"), u=u)
    o = SV.map_operations(u)
    if e.get("restricted") and o.get("restricted"):
        return {"restricted": True, "message": e.get("message") or o.get("message")}
    pts = e.get("points") or []
    kinds: dict = defaultdict(int)
    for p in pts:
        kinds[p.get("kind") or p.get("status") or "record"] += 1
    return {"entomology": {"records": len(pts), "by_kind": dict(kinds), "by_cell": e.get("by_cell"), "link": map_link("ento")},
            "operations": {"status_counts": o.get("status_counts"), "points": len(o.get("points") or []), "link": map_link("ops")},
            "note": "Field records are what officers entered; demonstration records are labelled as such."}


def t_sensors(u: User, a: dict) -> dict:
    r = SV.map_iot(u)
    return {"nodes": [{k: n.get(k) for k in ("node_id", "status", "lat", "lon", "last_received_utc", "packets")} for n in r["nodes"]],
            "note": r.get("note"), "link": map_link("iot")}


def t_search(u: User, a: dict) -> dict:
    q = str(a.get("query", ""))[:40]
    if not q:
        return {"results": []}
    hits = SV.search(q=q, u=u)[:15]
    return {"results": [h | {"link": map_link("grid,forecast", focus=h["id"]) if h.get("type") == "site" else None} for h in hits]}


def t_reliability(u: User, a: dict) -> dict:
    au = SV.model_audit(u)
    g = SV.map_grid20(u)
    sk = (g.get("skill") or {}).get("7") or {}
    def line(zone):
        z = sk.get(zone) or {}
        if not z:
            return None
        return (f"{zone}: ranking skill PR-AUC {z.get('pr_auc_model')} against {z.get('pr_auc_B1')} for the 'recently wet' rule; at the alert line "
                f"{round(100 * (z.get('alert_precision') or 0))}% of alerted places became wet and {round(100 * (z.get('alert_recall') or 0))}% of new water was alerted.")
    return {"daily_outlook_7_days": [x for x in (line("all cells"), line("inside sector"), line("over 500 m")) if x],
            "daily_outlook_inland": "Away from the lake, Sentinel-2 has not confirmed the radar water signal, so inland places are leads to check on foot."
                                    if not g.get("inland_forecast_demonstrated") else "Inland skill was also shown.",
            "site_forecast_audit": f"{au.get('title', '')}: {au.get('summary', '')}"[:700],
            "rules": "Forecasts are research-only leads for inspection. Water is not breeding: larvae must be found by dipping, and treatment needs a "
                     "suitability assessment and a supervisor's authorisation.",
            "links": {"model_assurance_page": "#/assurance", "research_alignment_page": "#/research", "daily_outlook_on_map": map_link("grid")}}


def _obj(props: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props, "required": required or [], "additionalProperties": False}


TOOLS = [
    ("situation", "Today's picture: daily outlook, 7/14-day site forecast counts with links, what to do, data freshness. Use first for general questions.",
     _obj({}), t_situation),
    ("daily_outlook", "Daily 20 m outlook: PLACES with ids G20-... where new water may form 7-14 days ahead, ranked. Not the monitoring locations. "
     "Filters: day_ahead, in_sector_only, zone.",
     _obj({"day_ahead": {"type": "integer", "description": "7 to 14"}, "in_sector_only": {"type": "boolean"},
           "zone": {"type": "string", "enum": ["lake shore", "inland"]}, "limit": {"type": "integer"}}), t_daily_outlook),
    ("site_forecast", "Site forecast at the 600 MONITORING LOCATIONS (ids G0000-G0599), 7 or 14 days ahead; these are the sites in situation's "
     "what_to_do. Category: high = new-water signal, insufficient = cannot classify. Use in_sector_only for 'inside the sector'.",
     _obj({"horizon": {"type": "integer", "enum": [7, 14]}, "date": {"type": "string", "description": "YYYY-MM-DD"},
           "category": {"type": "string", "enum": ["high", "insufficient", "moderate", "lower"]}, "in_sector_only": {"type": "boolean"},
           "limit": {"type": "integer"}}), t_site_forecast),
    ("location", "One place: monitoring location G0000-G0599, habitat H0001.., or outlook place G20-...", _obj({"id": {"type": "string"}}, ["id"]), t_location),
    ("satellite_water", "Radar water signal on the latest (or given) pass: wet locations and new water since the previous pass.",
     _obj({"date": {"type": "string", "description": "YYYY-MM-DD"}, "in_sector_only": {"type": "boolean"}, "limit": {"type": "integer"}}), t_satellite_water),
    ("weather", "Weather station record and rain forecast.", _obj({}), t_weather),
    ("field_and_operations", "Field inspections, larval samples, treatment decisions.", _obj({"start": {"type": "string"}, "end": {"type": "string"}}),
     t_field_and_operations),
    ("sensors", "IoT node status and readings.", _obj({}), t_sensors),
    ("search", "Find a location, habitat or area by code or name.", _obj({"query": {"type": "string"}}, ["query"]), t_search),
    ("reliability", "How trustworthy the forecasts are: audit, measured skill, rules of use.", _obj({}), t_reliability),
]
TOOL_FN = {n: f for n, _, _, f in TOOLS}
STATUS = {"situation": "Reading today's situation", "daily_outlook": "Checking the daily new-water outlook", "site_forecast": "Checking the site forecast",
          "location": "Opening the location record", "satellite_water": "Reading the latest radar pass", "weather": "Reading the weather record",
          "field_and_operations": "Reading field and operations records", "sensors": "Checking the sensor nodes", "search": "Searching",
          "reliability": "Checking forecast reliability"}

SYSTEM = """You answer questions about RINDA's surveillance map for the Gikomero Sector pilot (Gasabo District, Rwanda) for malaria larval source management staff.

How to answer
- Use the tools for every fact. Never invent a number, date, place or status; if a tool does not have it, say so plainly.
- Be brief: lead with the answer in one or two sentences, then at most a short bulleted list. No tables (the pane is narrow).
  Plain language, no jargon without a gloss.
- Give dates as written in the tool results and say how old the data is when it matters (issue date, radar pass date).
- End with one to three links, always as Markdown [short label](#/...). Copy link values exactly as the tools return them; never invent
  or add parameters. If no tool gave a fitting link, give none.
- RINDA has two different forecast products. The site forecast covers 600 monitoring locations (ids G0000-G0599, tool site_forecast);
  the daily outlook covers 20 m places (ids G20-..., tool daily_outlook). Keep follow-up questions on the product the conversation is about.
- Quote counts exactly as the tool states them, with what they count (a tool's "summary" sentence can be used as written).
- Never work out dates or ages yourself; use the dates and *_age_days values the tools give.

Rules that always hold
- Forecasts and outlook places are research-only leads for inspection. They are not breeding sites and never authorise treatment.
- Water is not breeding: a place counts as an Anopheles habitat only when larvae are found by dipping. Treatment needs a larval sample, a suitability assessment and a supervisor's authorisation.
- Inland (away from Lake Muhazi), forecast water has not been confirmed by Sentinel-2; say "check on foot" for inland places.
- If data is stale or missing, say so instead of guessing. Do not give medical advice.
- Questions outside the map, the data or larval source management: say briefly that you answer questions about RINDA's map and data."""


# ------------------------------------------------------------------ model loop (OpenAI-style chat completions, streamed)
class ProviderError(Exception):
    def __init__(self, status: int, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.status, self.retry_after = status, retry_after


def _chat_stream(messages: list[dict], tools: list[dict], model: str | None = None):
    """POST /chat/completions with stream=true. Yields ("text", str) as text arrives, then ("final", {content, tool_calls})."""
    import httpx
    p = provider()
    headers = {"content-type": "application/json"}
    key = _api_key()
    if key:
        headers["authorization"] = f"Bearer {key}"
    model = model or p["model"]
    body = {"model": model, "messages": messages, "tools": tools, "tool_choice": "auto", "stream": True, "max_tokens": 900}
    if p["key"] == "groq" and model.startswith("openai/gpt-oss"):
        body["reasoning_effort"] = "low"          # accepted by Groq for gpt-oss; cuts hidden reasoning tokens
    content, calls, finish = [], {}, None
    with httpx.Client(timeout=httpx.Timeout(60.0, connect=10.0)) as http:
        with http.stream("POST", f"{p['base']}/chat/completions", json=body, headers=headers) as r:
            if r.status_code != 200:
                txt = r.read().decode("utf-8", "replace")[:300]
                try:
                    ra = float(r.headers.get("retry-after", ""))
                except ValueError:
                    ra = None
                raise ProviderError(r.status_code, txt, ra)
            for line in r.iter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                ch = json.loads(data)
                for c in ch.get("choices", []):
                    d = c.get("delta") or {}
                    if d.get("content"):
                        content.append(d["content"])
                        yield "text", d["content"]
                    for tc in d.get("tool_calls") or []:
                        slot = calls.setdefault(tc.get("index", len(calls)), {"id": None, "name": "", "arguments": ""})
                        slot["id"] = tc.get("id") or slot["id"]
                        fn = tc.get("function") or {}
                        slot["name"] += fn.get("name") or ""
                        slot["arguments"] += fn.get("arguments") or ""
                    finish = c.get("finish_reason") or finish
    yield "final", {"content": "".join(content), "tool_calls": [calls[k] for k in sorted(calls)], "finish": finish}


TOOL_SPECS = [{"type": "function", "function": {"name": n, "description": d, "parameters": s}} for n, d, s, _ in TOOLS]


def _run_tool(name: str, args, u: User) -> tuple[str, bool]:
    fn = TOOL_FN.get(name)
    if fn is None:
        return json.dumps({"error": f"unknown tool {name}"}), True
    if not isinstance(args, dict):
        return json.dumps({"INVALID_JSON": json.dumps(args, default=str)}), True
    props = next(s for n, _, s, _ in TOOLS if n == name)["properties"]
    bad = [k for k in args if k not in props]
    pytype = {"integer": int, "boolean": bool, "string": str}
    bad += [k for k, v in args.items() if k in props and v is not None and not (isinstance(v, pytype[props[k]["type"]])
                                                                                and not (props[k]["type"] == "integer" and isinstance(v, bool)))]
    bad += [k for k, v in args.items() if k in props and "enum" in props[k] and v is not None and v not in props[k]["enum"]]
    if bad:
        return json.dumps({"INVALID_INPUT": bad, "received": args}, default=str), True
    if "limit" in args and args["limit"] is not None:
        args = {**args, "limit": max(1, min(25, args["limit"]))}
    if "day_ahead" in args and args["day_ahead"] is not None and not 7 <= args["day_ahead"] <= 14:
        return json.dumps({"error": "day_ahead must be 7 to 14"}), True
    try:
        return json.dumps(fn(u, args), default=str, separators=(",", ":")), False
    except HTTPException as e:
        return json.dumps({"error": e.detail}), True
    except Exception as e:  # report the failure to the model, never invent data
        return json.dumps({"error": f"{type(e).__name__}: {str(e)[:200]}"}), True


def _rate_ok(uid: str) -> bool:
    limit = int(os.environ.get("RINDA_ASSISTANT_HOURLY", "40"))
    now = time.time()
    with _LOCK:
        q = _RATE[uid]
        while q and now - q[0] > 3600:
            q.popleft()
        if len(q) >= limit:
            return False
        q.append(now)
        return True


def _conversation(cid: str | None, uid: str) -> tuple[str, dict]:
    now = time.time()
    with _LOCK:
        for k in [k for k, v in _CONV.items() if now - v["t"] > TTL_S]:
            _CONV.pop(k, None)
        c = _CONV.get(cid or "")
        if not c or c["uid"] != uid or c["turns"] >= MAX_TURNS:
            cid = uuid.uuid4().hex
            c = _CONV[cid] = {"uid": uid, "turns_text": [], "turns": 0, "t": now}
        c["t"] = now
        return cid, c


def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj)}\n\n"


def answer_stream(question: str, cid: str | None, u: User):
    cid, conv = _conversation(cid, str(u.id))
    yield _sse({"type": "start", "conversation_id": cid})
    # earlier turns as plain text only; this turn's tool calls stay in `work`
    history = []
    for q, a in conv["turns_text"][-HISTORY_TURNS:]:
        history += [{"role": "user", "content": q}, {"role": "assistant", "content": a}]
    work = [{"role": "system", "content": SYSTEM}, *history, {"role": "user", "content": question}]
    answer = ""
    try:
        for _ in range(MAX_ROUNDS):
            final = None
            p = provider()
            models = [p["model"], *[m for m in p.get("fallbacks", []) if m != p["model"]]]
            waited = False
            while final is None:
                limited = []
                for m in models:
                    started = False
                    try:
                        for kind, val in _chat_stream(work, TOOL_SPECS, m):
                            if kind == "text":
                                started = True
                                answer += val
                                yield _sse({"type": "text", "text": val})
                            else:
                                final = val
                        break
                    except ProviderError as e:
                        if e.status != 429 or started:
                            raise
                        limited.append(e)
                if final is not None:
                    break
                wait = min((e.retry_after or 60) for e in limited)
                if waited or wait > 20:
                    raise limited[0]
                waited = True
                yield _sse({"type": "status", "text": f"Free plan busy, waiting {int(wait) + 1} s"})
                time.sleep(wait + 0.5)
            calls = [c for c in final["tool_calls"] if c["name"]]
            if not calls:
                break
            work.append({"role": "assistant", "content": final["content"] or None,
                         "tool_calls": [{"id": c["id"] or f"call_{i}", "type": "function", "function": {"name": c["name"], "arguments": c["arguments"] or "{}"}}
                                        for i, c in enumerate(calls)]})
            for i, c in enumerate(calls):
                yield _sse({"type": "status", "text": STATUS.get(c["name"], "Looking up records")})
                try:
                    args = json.loads(c["arguments"] or "{}")
                except json.JSONDecodeError:
                    out, err = json.dumps({"INVALID_JSON": (c["arguments"] or "")[:200]}), True
                else:
                    out, err = _run_tool(c["name"], args, u)
                work.append({"role": "tool", "tool_call_id": c["id"] or f"call_{i}", "content": out[:TOOL_CHARS]})
        else:
            yield _sse({"type": "error", "message": "The question needed too many look-ups. Please ask something narrower."})
        if answer.strip():
            conv["turns_text"].append((question, answer))
        conv["turns"] += 1
        ops.audit(u.email, "assistant.ask", f"conversation {cid[:8]} turn {conv['turns']}")
        yield _sse({"type": "done", "conversation_id": cid, "turns_left": MAX_TURNS - conv["turns"]})
    except ProviderError as e:
        msg = ("The answer service rejected the API key." if e.status in (401, 403) else
               f"The free plan's limit was reached on every model. Please ask again in about {int(e.retry_after or 60) + 1} seconds."
               if e.status == 429 else
               f"The answer service returned an error ({e.status}).")
        yield _sse({"type": "error", "message": msg})
    except Exception as e:  # network or malformed stream: say so, never invent an answer
        yield _sse({"type": "error", "message": f"The server could not reach the answer service ({type(e).__name__})."})


# ------------------------------------------------------------------ routes
@router.get("/assistant/status")
def status(u: User = Depends(require("read:forecast"))):
    return settings() | {"signed_in": u.id is not None}


class AskIn(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    conversation_id: str | None = Field(default=None, max_length=64)


@router.post("/assistant/ask")
def ask(b: AskIn, u: User = Depends(require("read:forecast"))):
    st = settings()
    if not st["enabled"]:
        raise HTTPException(409, st["reason"])
    if u.id is None:
        raise HTTPException(401, "Sign in to ask the map.")
    if not _rate_ok(str(u.id)):
        raise HTTPException(429, "You have reached the hourly limit for questions. Please try again later.")
    return StreamingResponse(answer_stream(b.question.strip(), b.conversation_id, u), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

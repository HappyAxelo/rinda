"""RINDA Vector Habitat Intelligence: API and console server.

    uvicorn --app-dir server main:app --port 8740

The documented API lives under /api/v1. /api is a compatibility alias with the
same handlers (hidden from the schema) so existing clients keep working.
"""
from __future__ import annotations

import hmac
import io
import json
import logging
import math
import os
import time
import traceback
import uuid
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from dataclasses import asdict, fields

import numpy as np
import pandas as pd
from fastapi import APIRouter, Body, Depends, FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

import db
import field as F
import ops
import planner as P
import rules as R
import security as S
from bulletin import render_bulletin
from data import CLAIM, STATION, records, store
from engine import CFG, ENGINE, ENGINE_VERSION, ROOT, RUN_SUMMARY, V3_CARD, VAR
from security import User, current_user, require

WEB = ROOT / "web"
STATION_KEYS_FILE = VAR / "station_keys.json"
MAX_UPLOAD = 20 * 1024 * 1024
STARTED = time.time()

log = logging.getLogger("rinda")
logging.basicConfig(level=logging.INFO, format="%(message)s")


def jlog(**kv) -> None:
    log.info(json.dumps({"ts": db.now(), **kv}, default=str))


@asynccontextmanager
async def lifespan(_app):
    store()
    ENGINE.load()
    db.conn().close()
    where = S.bootstrap_admin()
    if where:
        jlog(event="admin.bootstrap", credential_location=where if where == "environment" else "var/initial_admin.txt")
    S.ensure_demo_users()
    ops.audit("system", "engine.ready", f"engine {ENGINE_VERSION} schema {db.schema_version()}")
    jlog(event="startup", engine=ENGINE_VERSION, demo=S.DEMO, public_read=S.PUBLIC_READ)
    yield


app = FastAPI(
    title="RINDA Vector Habitat Intelligence API", version=ENGINE_VERSION, lifespan=lifespan,
    docs_url="/api/docs", openapi_url="/api/openapi.json",
    description="Forecasts of new surface-water formation at monitoring points, with provenance, "
                "verification priority, field surveillance and intervention records. Research-stage model: "
                "outputs prioritise inspection; they never authorise treatment.")

# --------------------------------------------------------------- middleware
_lat: dict[str, deque] = defaultdict(lambda: deque(maxlen=500))
_counts: dict[str, int] = defaultdict(int)
_errors: dict[str, int] = defaultdict(int)

CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
       "font-src 'self' https://fonts.gstatic.com data:; img-src 'self' data: blob: https://server.arcgisonline.com "
       "https://*.basemaps.cartocdn.com; connect-src 'self'; frame-src 'self'; frame-ancestors 'self'; "
       "base-uri 'self'; form-action 'self'")


def _group(path: str) -> str:
    p = path.replace("/api/v1/", "/api/")
    parts = p.split("/")
    return "/".join(parts[:3]) if p.startswith("/api/") else "static"


@app.middleware("http")
async def observe(request: Request, call_next):
    rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
    t0 = time.perf_counter()
    if request.method in ("POST", "PUT", "PATCH", "DELETE") and request.url.path.startswith("/api/") \
            and "/telemetry/" not in request.url.path:
        # cookies are SameSite=Strict; this header blocks form-based cross-site posts as well
        if request.headers.get("x-rinda") != "1":
            return JSONResponse({"error": {"code": "csrf", "message": "Missing X-RINDA header."}}, status_code=403)
        lim = 10 if request.url.path.endswith(("/auth/login", "/auth/demo")) else 120
        try:
            S.rate_limit(f"{S.client_ip(request)}:{lim}", lim)
        except HTTPException as e:
            return JSONResponse({"error": {"code": "rate_limited", "message": e.detail}}, status_code=429)
        cl = request.headers.get("content-length")
        if cl and int(cl) > MAX_UPLOAD:
            return JSONResponse({"error": {"code": "too_large", "message": "Uploads are limited to 20 MB."}}, status_code=413)
    try:
        resp = await call_next(request)
    except Exception as e:  # pragma: no cover - last-resort guard
        # traceback frames only: no request bodies, headers or query values
        jlog(event="unhandled", rid=rid, path=request.url.path, error=type(e).__name__,
             trace=traceback.format_exception(type(e), e, e.__traceback__, limit=-6))
        resp = JSONResponse({"error": {"code": "internal", "message": "Unexpected server error.", "request_id": rid}},
                            status_code=500)
    ms = (time.perf_counter() - t0) * 1000
    g = _group(request.url.path)
    _counts[g] += 1
    _lat[g].append(ms)
    if resp.status_code >= 500:
        _errors[g] += 1
    if g != "static":
        jlog(event="request", rid=rid, method=request.method, path=request.url.path, status=resp.status_code,
             ms=round(ms, 1))
    resp.headers["X-Request-ID"] = rid
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    resp.headers["X-Frame-Options"] = "SAMEORIGIN"
    resp.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(self)"
    if not request.url.path.startswith("/api/docs"):
        resp.headers["Content-Security-Policy"] = CSP
    if S.flag("RINDA_SECURE_COOKIES"):
        resp.headers["Strict-Transport-Security"] = "max-age=31536000"
    if request.url.path.startswith(("/js/", "/css/")):
        resp.headers["Cache-Control"] = "no-cache"
    elif request.url.path.startswith("/api/"):
        resp.headers.setdefault("Cache-Control", "no-store")
    return resp


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException):
    code = {400: "bad_request", 401: "unauthenticated", 403: "forbidden", 404: "not_found", 409: "conflict",
            413: "too_large", 422: "invalid", 429: "rate_limited"}.get(exc.status_code, "error")
    return JSONResponse({"error": {"code": code, "message": str(exc.detail)}, "detail": exc.detail},
                        status_code=exc.status_code)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    msgs = [f"{'.'.join(str(x) for x in e['loc'][1:])}: {e['msg']}" for e in exc.errors()]
    return JSONResponse({"error": {"code": "invalid", "message": "; ".join(msgs)}, "detail": msgs}, status_code=422)


api = APIRouter()


def _area() -> dict:
    s = store().sites
    hull = P.hull_area_ha(s.latitude.to_numpy(), s.longitude.to_numpy())
    wet = s[s.water_freq > 0]
    zone = len(wet) * math.pi * 50 ** 2 / 10_000
    return {"sector_hull_ha": hull, "wet_prone_zone_ha": zone, "n_wet_prone_sites": int(len(wet))}


def _sources() -> list[dict]:
    meta = {
        "meteo": ("Meteorology", "Automatic weather station, daily", "Gikomero AWS"),
        "sentinel1": ("Sentinel-1 SAR", "C-band VV/VH backscatter, descending pass", "Copernicus via Earth Engine"),
        "sentinel2": ("Sentinel-2 optical", "NDVI, NDWI, MNDWI, EVI, NDMI", "Copernicus via Earth Engine"),
        "terrain": ("Terrain", "Elevation, slope, TWI, flow accumulation", "SRTM / Copernicus DEM"),
        "entomology": ("Larval surveys", "Dipping records with Anopheles confirmation", "RBC entomology teams"),
        "nwp_forecast": ("NWP rainfall forecast", "Lead 1-14 days", "Meteo Rwanda"),
    }
    out = []
    for k, (name, desc, origin) in meta.items():
        present = bool(CFG["sources"][k]["present"])
        unlocks = {"sentinel2": "Target B: potentially suitable habitat",
                   "terrain": "Terrain features; ablation arms G and H",
                   "entomology": "Target C: confirmed Anopheles habitat",
                   "nwp_forecast": "Operational forecast mode (replaces hindcast)"}.get(k)
        out.append({"key": k, "name": name, "description": desc, "origin": origin,
                    "present": present, "unlocks": unlocks})
    return out


# ---------------------------------------------------------------- health
@app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
def index():
    return FileResponse(WEB / "index.html", headers={"Cache-Control": "no-store"})


@api.api_route("/health", methods=["GET", "HEAD"], tags=["system"])
def health():
    """Liveness: the process is serving requests. Says nothing about forecast validity."""
    return {"status": "ok", "uptime_s": int(time.time() - STARTED)}


@api.get("/ready", tags=["system"])
def ready():
    """Readiness: models loaded and database reachable. 503 until both hold.
    Forecast freshness is reported separately because a stale archive is still servable."""
    ok_engine = ENGINE.status == "ready"
    try:
        db.schema_version()
        ok_db = True
    except Exception:
        ok_db = False
    latest = store().dates(7)[-1] if ok_engine else None
    body = {"status": "ready" if ok_engine and ok_db else "starting", "engine": ok_engine, "database": ok_db,
            "latest_issue": latest,
            "forecast_freshness": R.forecast_meta(7, latest)["status"] if latest else "unknown"}
    return JSONResponse(body, status_code=200 if ok_engine and ok_db else 503)


# ------------------------------------------------------------------ auth
class LoginIn(BaseModel):
    email: str = Field(max_length=200)
    password: str = Field(max_length=200)


@api.post("/auth/login", tags=["auth"])
def auth_login(b: LoginIn, response: Response):
    u = S.login(b.email, b.password)
    if not u:
        ops.audit(b.email[:80], "auth.failed", "")
        raise HTTPException(401, "Email or password is incorrect.")
    S.start_session(response, u.id)
    ops.audit(u.email, "auth.login", u.role)
    return u.public()


class DemoIn(BaseModel):
    role: str


@api.post("/auth/demo", tags=["auth"])
def auth_demo(b: DemoIn, response: Response):
    u = S.demo_user(b.role)
    if not u:
        raise HTTPException(404, "Demonstration sign-in is not enabled on this deployment.")
    S.start_session(response, u.id)
    ops.audit(u.email, "auth.demo", u.role)
    return u.public()


@api.post("/auth/logout", tags=["auth"])
def auth_logout(request: Request, response: Response):
    S.end_session(request, response)
    return {"ok": True}


@api.get("/auth/me", tags=["auth"])
def auth_me(u: User = Depends(current_user)):
    return u.public() | {"demo_enabled": S.DEMO, "public_read": S.PUBLIC_READ,
                         "roles": [{"role": r, "label": S.ROLE_LABEL[r]} for r in S.ROLES]}


class PasswordIn(BaseModel):
    current: str = Field(max_length=200)
    new: str = Field(max_length=200)


@api.post("/auth/password", tags=["auth"])
def auth_password(b: PasswordIn, u: User = Depends(require("read:forecast"))):
    if u.id is None or u.demo:
        raise HTTPException(403, "Demonstration and public accounts have no password.")
    with db.tx() as c:
        r = c.execute("SELECT pw_hash FROM users WHERE id=?", (u.id,)).fetchone()
    if not S.verify_password(b.current, r["pw_hash"]):
        raise HTTPException(401, "Current password is incorrect.")
    if (p := S.password_problem(b.new)):
        raise HTTPException(422, p)
    with db.tx() as c:
        c.execute("UPDATE users SET pw_hash=? WHERE id=?", (S.hash_password(b.new), u.id))
    ops.audit(u.email, "auth.password_changed", "")
    return {"ok": True}


# ----------------------------------------------------------------- admin
class UserIn(BaseModel):
    email: str = Field(min_length=5, max_length=200, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    name: str = Field(min_length=2, max_length=120)
    role: str
    organisation: str = Field(default="", max_length=200)
    password: str = Field(max_length=200)


@api.get("/admin/users", tags=["admin"])
def admin_users(u: User = Depends(require("admin:users"))):
    return S.list_users()


@api.post("/admin/users", status_code=201, tags=["admin"])
def admin_create_user(b: UserIn, u: User = Depends(require("admin:users"))):
    if b.role not in S.ROLES:
        raise HTTPException(422, "Unknown role.")
    if (p := S.password_problem(b.password)):
        raise HTTPException(422, p)
    try:
        uid = S.create_user(b.email, b.name, b.role, b.password, b.organisation, demo=u.demo)
    except Exception:
        raise HTTPException(409, "A user with that email already exists.")
    ops.audit(u.email, "user.create", f"{b.email} role={b.role}")
    return {"id": uid}


class UserPatch(BaseModel):
    role: str | None = None
    active: bool | None = None


@api.patch("/admin/users/{uid}", tags=["admin"])
def admin_update_user(uid: int, b: UserPatch, u: User = Depends(require("admin:users"))):
    if uid == u.id and (b.active is False or (b.role and b.role != "admin")):
        raise HTTPException(409, "You cannot deactivate or demote your own account.")
    with db.tx() as c:
        if b.role:
            if b.role not in S.ROLES:
                raise HTTPException(422, "Unknown role.")
            c.execute("UPDATE users SET role=? WHERE id=?", (b.role, uid))
        if b.active is not None:
            c.execute("UPDATE users SET active=? WHERE id=?", (int(b.active), uid))
            if not b.active:
                c.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
    ops.audit(u.email, "user.update", f"id={uid} {b.model_dump(exclude_none=True)}")
    return {"ok": True}


@api.get("/admin/status", tags=["admin"])
def admin_status(u: User = Depends(require("admin:system"))):
    groups = []
    for g in sorted(_counts):
        lat = sorted(_lat[g])
        q = lambda p: round(lat[min(len(lat) - 1, int(p * len(lat)))], 1) if lat else None  # noqa: E731
        groups.append({"group": g, "requests": _counts[g], "errors_5xx": _errors[g], "p50_ms": q(.5), "p95_ms": q(.95)})
    dbsize = db.DB.stat().st_size if db.DB.exists() else 0
    backups = sorted((p.name, p.stat().st_size) for p in (VAR / "backups").glob("*.db")) if (VAR / "backups").exists() else []
    latest = store().dates(7)[-1]
    meta = R.forecast_meta(7, latest)
    return {"uptime_s": int(time.time() - STARTED), "engine": ENGINE.status, "engine_version": ENGINE_VERSION,
            "schema_version": db.schema_version(), "demo": S.DEMO, "public_read": S.PUBLIC_READ,
            "forecast": {"latest_issue": latest, "age_days": meta["age_days"], "status": meta["status"]},
            "inputs": meta["inputs"], "database_bytes": dbsize,
            "backups": [{"file": n, "bytes": b} for n, b in backups[-10:]], "requests": groups,
            "rules_version": R.rules()["version"], "rules_approved_by": R.rules()["approved_by"]}


@api.post("/admin/backup", tags=["admin"])
def admin_backup(u: User = Depends(require("admin:system"))):
    d = VAR / "backups"
    d.mkdir(exist_ok=True)
    name = f"operations-{db.now().replace(':', '').replace('-', '')}.db"
    db.backup(d / name)
    ops.audit(u.email, "system.backup", name)
    return {"file": name, "bytes": (d / name).stat().st_size}


@api.get("/admin/backup/{name}", tags=["admin"])
def admin_backup_download(name: str, u: User = Depends(require("admin:system"))):
    p = (VAR / "backups" / name).resolve()
    if p.parent != (VAR / "backups").resolve() or not p.exists() or p.suffix != ".db":
        raise HTTPException(404, "Unknown backup.")
    ops.audit(u.email, "system.backup_download", name)
    return FileResponse(p, filename=name, media_type="application/octet-stream")


@api.get("/admin/audit", tags=["admin"])
def admin_audit(limit: int = Query(200, ge=1, le=2000), u: User = Depends(require("read:audit"))):
    return ops.audit_trail(limit)


# ------------------------------------------------------------- bootstrap
@api.get("/bootstrap", tags=["forecast"])
def bootstrap(u: User = Depends(require("read:forecast"))):
    st = store()
    return {
        "product": {"name": "RINDA", "edition": "Vector Habitat Intelligence", "version": ENGINE_VERSION},
        "tenant": {"name": "Gikomero Sector pilot", "district": "Gasabo", "province": "Kigali City"},
        "mode": RUN_SUMMARY["provenance"]["mode"], "claim": CLAIM, "target": R.TARGET,
        "spatial_resolution": R.RESOLUTION,
        "horizons": {h: {"dates": st.dates(h), "threshold": st.thr[h], "model_status": R.model_status(h)} for h in (7, 14)},
        "categories": R.category_labels(), "rules_version": R.rules()["version"],
        "rules_approved_by": R.rules()["approved_by"],
        "sites": records(st.sites), "station": STATION, "area": _area(),
        "sources": _sources(), "model": ENGINE.card(),
        "planner_defaults": P.defaults(), "econ_defaults": ops.DEFAULT_ECON,
        "s1_last": st.s1_dates[-1] if hasattr(st, "s1_dates") else str(st.s1.date.max().date()),
        "meteo_last": str(st.meteo.date.max().date()), "server_date": R.today().isoformat(),
        "demo": S.DEMO,
    }


# -------------------------------------------------------------- forecast
FC_COLS = ["site_id", "latitude", "longitude", "probability", "risk_class", "uncertainty_std",
           "uncertainty_band", "conformal_decision", "priority_score", "WATER_PERSISTENCE_PASSES", "y",
           "alert", "stratum", "water_freq"]


@api.get("/forecast", tags=["forecast"])
def forecast(h: int = Query(7, description="Horizon in days: 7 or 14"), date: str | None = None,
             u: User = Depends(require("read:forecast"))):
    if h not in (7, 14):
        raise HTTPException(422, "h must be 7 or 14.")
    st = store()
    d, f = st.forecast(h, date)
    f2 = f[FC_COLS].copy()
    f2["priority_rank"] = np.arange(1, len(f2) + 1)  # rank within this issue, not the archive
    f2["category"] = [R.category(c, p) for c, p in zip(f2.conformal_decision, f2.probability)]
    f2["target_date"] = (pd.Timestamp(d) + pd.Timedelta(days=h)).strftime("%Y-%m-%d")
    cats = f2.category.value_counts().to_dict()
    summ = {
        "issue_date": d, "target_date": f2["target_date"].iloc[0] if len(f2) else None,
        "n_sites": int(len(f2)), "alerts": int(f2.alert.sum()),
        "risk": f2.risk_class.value_counts().to_dict(),
        "decisions": f2.conformal_decision.value_counts().to_dict(),
        "categories": {k: int(cats.get(k, 0)) for k in ("high", "moderate", "insufficient", "lower")},
        "mean_probability": float(f2.probability.mean()), "threshold": st.thr[h],
    }
    if f2.y.notna().any():
        y = f2.y.fillna(0).astype(int)
        call = f2.category == "high"
        summ["outcome"] = {"observed": int(y.sum()), "hits": int((call & (y == 1)).sum()),
                           "false_alarms": int((call & (y == 0)).sum()),
                           "misses": int((~call & (y == 1)).sum())}
    return {"meta": R.forecast_meta(h, d), "summary": summ, "rows": records(f2), "claim": CLAIM}


@api.get("/forecast/meta", tags=["forecast"])
def forecast_meta(h: int = 7, date: str | None = None, u: User = Depends(require("read:forecast"))):
    d = store().nearest_date(h, date)
    return R.forecast_meta(h, d)


@api.get("/verification", tags=["validation"])
def verification(h: int = 7, u: User = Depends(require("read:forecast"))):
    return records(store().verification(h))


@api.get("/site/{site_id}", tags=["forecast"])
def site(site_id: str, u: User = Depends(require("read:forecast"))):
    s = store().site(site_id)
    if not s:
        raise HTTPException(404, "Unknown site.")
    if u.can("read:field"):
        s["field"] = F.site_field_history(site_id)
    else:
        with db.tx() as c:
            n = c.execute("SELECT COUNT(*) FROM observations WHERE site_id=?", (site_id,)).fetchone()[0]
        s["field"] = {"restricted": True, "n_observations": n}
    for h, rows in s["forecasts"].items():
        for r in rows:
            r["category"] = R.category(r["conformal_decision"], r["probability"])
    return s


@api.get("/explain", tags=["forecast"])
def explain(site: str, date: str, h: int = 7, u: User = Depends(require("read:forecast"))):
    out = ENGINE.explain(h, site, date)
    if out.get("available"):
        out["category"] = R.category(out["decision"], out["probability"])
    return out


@api.get("/scenario", tags=["forecast"])
def scenario(site: str, date: str, h: int = 7, rain: float = 1.0, temp: float = 0.0,
             u: User = Depends(require("read:forecast"))):
    return ENGINE.scenario(h, site, date, max(0.0, min(rain, 3.0)), max(-4.0, min(temp, 4.0)))


@api.post("/score", tags=["forecast"])
async def score(h: int = Query(7), file: UploadFile = File(...), u: User = Depends(require("score:batch"))):
    raw = await file.read(MAX_UPLOAD + 1)
    if len(raw) > MAX_UPLOAD:
        raise HTTPException(413, "Uploads are limited to 20 MB.")
    try:
        X = pd.read_csv(io.BytesIO(raw))
    except Exception:
        raise HTTPException(422, "File is not a readable CSV.")
    if len(X) > 200_000:
        raise HTTPException(413, "Batch limited to 200 000 rows.")
    out = ENGINE.score_batch(h, X)
    ops.audit(u.email, "model.batch_score", f"h={h} rows={len(X)}")
    return Response(out.to_csv(index=False), media_type="text/csv", headers={
        "Content-Disposition": f'attachment; filename="scored_h{h}.csv"',
        "X-Missing-Features": str(len(out.attrs.get("missing_features", [])))})


@api.get("/score/template", tags=["forecast"])
def score_template(h: int = 7, u: User = Depends(require("read:forecast"))):
    m = ENGINE.models[h]
    ex = ENGINE.tables[h].tail(3)[["site_id", "date"] + m.fcols]
    return PlainTextResponse(ex.to_csv(index=False), media_type="text/csv", headers={
        "Content-Disposition": f'attachment; filename="rinda_feature_template_h{h}.csv"'})


# ------------------------------------------------------------ validation
def _replay_rows(h: int) -> pd.DataFrame:
    p = store().product[h].dropna(subset=["y"]).copy()
    p["category"] = [R.category(c, pr) for c, pr in zip(p.conformal_decision, p.probability)]
    p["call"] = p.category == "high"
    p["y"] = p.y.astype(int)
    return p


def _confusion(p: pd.DataFrame) -> dict:
    hits = int((p.call & (p.y == 1)).sum())
    fa = int((p.call & (p.y == 0)).sum())
    miss_unc = int(((p.category == "insufficient") & (p.y == 1)).sum())
    miss_low = int(((p.category.isin(["lower", "moderate"])) & (p.y == 1)).sum())
    cn = int(((p.category.isin(["lower", "moderate"])) & (p.y == 0)).sum())
    obs = int(p.y.sum())
    return {"sites": int(len(p)), "observed_formation": obs, "high_calls": int(p.call.sum()), "hits": hits,
            "false_alarms": fa, "missed_while_insufficient": miss_unc, "missed_while_lower": miss_low,
            "correct_lower": cn, "insufficient": int((p.category == "insufficient").sum()),
            "precision_of_calls": hits / (hits + fa) if hits + fa else None,
            "detection_rate": hits / obs if obs else None,
            "missed_detection_rate": (obs - hits) / obs if obs else None,
            "detected_or_flagged_for_verification": (hits + miss_unc) / obs if obs else None}


LEAKAGE_NOTE = (
    "v3 rolling hindcast: the model is refitted on a fixed cadence on outcomes observable at the refit date "
    "(date + horizon + 2 days); calibration, alert line and conformal pool come from earlier forecasts only. "
    "Inputs for each issue are dated on or before the issue date." if V3_CARD else
    "Model fitted on data to 2024-12-31, calibrated to 2025-08-31; replay issues start 2025-09-03. "
    "Inputs for each issue are dated on or before the issue date (leakage guard passed).")


@api.get("/replay", tags=["validation"])
def replay(h: int = 7, date: str | None = None, u: User = Depends(require("read:forecast"))):
    """What the forecast said on an issue date, which inputs it had, and what the
    radar later saw. Every issue comes from a model fitted only on outcomes that
    were observable before that issue date."""
    st = store()
    d = st.nearest_date(h, date)
    p = _replay_rows(h)
    one = p[p.date == pd.Timestamp(d)]
    per = []
    for dt, g in p.groupby("date"):
        c = _confusion(g)
        per.append({"date": dt.strftime("%Y-%m-%d"), **c})
    other = 14 if h == 7 else 7
    return {"meta": R.forecast_meta(h, d), "issue": _confusion(one) if len(one) else None,
            "overall": {7: _confusion(_replay_rows(7)), 14: _confusion(_replay_rows(14))},
            "per_issue": per, "other_horizon": other,
            "definitions": {
                "high_call": "Category 'High priority for verification' (conformal set {formation}).",
                "hit": "High call and the next radar pass within ±2 days of the target date found new water.",
                "false_alarm": "High call; radar found the site still dry.",
                "missed_while_insufficient": "Water formed at a site the model sent to 'insufficient evidence'. A verification visit would still have found it.",
                "missed_while_lower": "Water formed at a site the model placed at lower priority: a genuine miss.",
                "outcome_source": "Radar-detected surface water only. Not larval presence."},
            "leakage_note": LEAKAGE_NOTE}


@api.get("/replay/export.csv", tags=["validation"])
def replay_export(h: int = 7, u: User = Depends(require("export:validation"))):
    rows = replay(h, None, u)["per_issue"]
    ops.audit(u.email, "export.validation", f"h={h}")
    df = pd.DataFrame(rows)
    df.insert(0, "horizon_days", h)
    df.insert(1, "model_status", "research_stage")
    return Response(df.to_csv(index=False), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="rinda_validation_h{h}.csv"'})


# ---------------------------------------------------------------- meteo
@api.get("/meteo", tags=["inputs"])
def meteo(start: str | None = None, end: str | None = None, u: User = Depends(require("read:forecast"))):
    return store().meteo_series(start, end)


@api.get("/meteo/summary", tags=["inputs"])
def meteo_summary(u: User = Depends(require("read:forecast"))):
    return store().meteo_summary()


# ------------------------------------------------------------ assurance
@api.get("/assurance", tags=["validation"])
def assurance(u: User = Depends(require("read:forecast"))):
    a = dict(store().assurance())
    a["engine"] = ENGINE.card()
    a["model_status"] = {7: R.model_status(7), 14: R.model_status(14)}
    return a


def _o1():
    if not V3_CARD:
        return ("Holdout PR-AUC 0.581 at 7 d vs base rate 0.037; 0.310 at 14 d vs 0.023. A persistence baseline scores higher at both horizons.",
                "Beat the persistence baseline; field validation; a second station and site set")
    e = {h: V3_CARD["evaluation"][h]["full"] for h in ("7", "14")}
    txt = "; ".join(f"{h} d PR-AUC {e[h]['pr_auc']:.3f} vs persistence {e[h]['B1_pr_auc']:.3f}, alert precision "
                    f"{e[h]['precision_threshold']['precision']:.2f}" for h in ("7", "14"))
    return (f"v3 rolling out-of-sample forecasts {e['7']['date_from']} to {e['7']['date_to']}: {txt}. "
            "The model now beats the persistence baseline at both horizons.",
            "An untouched test window for v3; field validation; a second site set")


O1_EVIDENCE, O1_OPEN = _o1()


@api.get("/evidence", tags=["programme"])
def evidence(u: User = Depends(require("read:forecast"))):
    ev = store().evidence()
    tc = ev.get("target_categories", {})
    steps = [
        ("A", "Environmental observation", "Meteorology and Sentinel-1 ingested; field IoT ingest endpoint live", "operational", ["Field Network", "Data & Provenance"]),
        ("B", "Habitat mapping", "SAR water classification for 600 monitoring points", "operational", ["Habitat Forecast"]),
        ("C", "Habitat suitability modelling", "Target B rules ready; waits on Sentinel-2 and terrain exports", "blocked", ["Data & Provenance"]),
        ("D", "Larval habitat prediction", "Target C ready; waits on verified larval observations", "blocked", ["Field Surveillance"]),
        ("E", "Productive habitat prediction", "Needs larval density data from RBC dipping protocol", "blocked", ["Field Surveillance"]),
        ("F", "Adult vector prediction", "Outside thesis scope (MAPLE partner)", "out_of_scope", []),
        ("G", "Transmission-related prediction", "Outside thesis scope (MAPLE partner)", "out_of_scope", []),
        ("H", "Prospective forecasting", "7- and 14-day formation forecast; research stage, hindcast until NWP feed connected", "partial", ["Habitat Forecast", "Replay & Validation"]),
        ("I", "Target verification", "Inspection tasks, observations with supervisor review; onboard log analytics", "awaiting_field", ["Field Surveillance", "Flight Operations"]),
        ("J", "Targeted intervention", "Human-authorised interventions linked to verified observations; mission planner", "awaiting_field", ["Field Surveillance", "Mission Planning"]),
        ("K", "Intervention outcome", "Follow-up observations close each intervention; cost comparator", "awaiting_field", ["Field Surveillance", "Impact & Economics"]),
    ]
    chain = []
    for code, name, deliv, status, mods in steps:
        key = next((k for k in tc if k.startswith(code + " ")), None)
        chain.append({"code": code, "name": name, "studies": tc.get(key) if key else None,
                      "platform": deliv, "status": status, "modules": mods})
    objectives = [
        {"id": "O1", "paper": "Paper 1", "title": "Forecast habitat formation 7-14 days ahead from EO, meteorological and IoT data",
         "status": "partial", "evidence": O1_EVIDENCE,
         "modules": ["Habitat Forecast", "Replay & Validation", "Model Assurance"],
         "open": O1_OPEN},
        {"id": "O2", "paper": "Papers 2 and 3", "title": "Convert risk maps into flight plans, confirm targets onboard, measure latency, energy and compute",
         "status": "awaiting_field", "evidence": "Planner, timing model (L <= d/v), energy model and log analytics built; measured values arrive with flight logs",
         "modules": ["Mission Planning", "Flight Operations"], "open": "Instrumented flights with Ampere Vision Rwanda airframe"},
        {"id": "O3", "paper": "Side paper with RBC / MAPLE", "title": "Effectiveness and cost-effectiveness against manual LSM; national integration framework",
         "status": "awaiting_field", "evidence": "Field workflow and comparator built; no field outcomes recorded yet",
         "modules": ["Field Surveillance", "Impact & Economics", "Bulletins"],
         "open": "Matched-plot entomological outcomes and programme cost records"},
    ]
    rqs = [
        ("RQ1", "Onboard habitat recognition accuracy", "Flight Operations", "awaiting_field"),
        ("RQ2", "Accuracy vs latency/memory/energy under compression", "Flight Operations", "awaiting_field"),
        ("RQ3", "Loop meets L <= d/v in flight; deadline-miss ratio", "Flight Operations", "awaiting_field"),
        ("RQ4", "Spray-targeting accuracy of confidence-gated actuation", "Flight Operations", "awaiting_field"),
        ("RQ5", "Generalisation across seasons and sites", "Model Assurance", "partial"),
        ("RQ6", "Energy per treated hectare vs blanket application", "Mission Planning", "partial"),
        ("RQ-C", "Flight time saved by forecast routing vs lawnmower", "Mission Planning", "partial"),
        ("RQ-D", "Field IoT link reliability and forecast sensitivity", "Field Network", "awaiting_field"),
    ]
    return {"review": {"n": ev.get("n"), "target_categories": tc, "strength": ev.get("strength", {}),
                       "intervention_linkage_field_trap": ev.get("intervention_linkage_field_trap"),
                       "H_with_temporal_separation": ev.get("H_with_temporal_separation")},
            "chain": chain, "objectives": objectives,
            "rqs": [{"id": a, "question": b, "module": c, "status": d} for a, b, c, d in rqs]}


@api.get("/governance", tags=["programme"])
def governance(u: User = Depends(require("read:forecast"))):
    a = store().audit()
    return {"sources": _sources(), "provenance": RUN_SUMMARY["provenance"],
            "water_thresholds": RUN_SUMMARY["water_thresholds"], "audit": a,
            "trail": ops.audit_trail(50) if u.can("read:audit") else None, "engine": ENGINE.card(),
            "rules": R.rules(),
            "leakage": {h: r["manifest"]["table"].get("leakage_checks") for h, r in
                        ((r["horizon"], r) for r in RUN_SUMMARY["results"])}}


# -------------------------------------------------------------- planning
def _aircraft(d: dict | None) -> P.Aircraft:
    names = {f.name for f in fields(P.Aircraft)}
    out = {}
    for k, v in (d or {}).items():
        if k in names and k != "name":
            try:
                v = float(v)
            except (TypeError, ValueError):
                raise HTTPException(422, f"aircraft.{k} must be a number.")
            if not math.isfinite(v) or v < 0 or v > 1e6:
                raise HTTPException(422, f"aircraft.{k} is out of range.")
            out[k] = v
    a = P.Aircraft(**out)
    if a.work_speed_ms <= 0 or a.cruise_speed_ms <= 0 or a.swath_m <= 0 or a.endurance_min <= 0:
        raise HTTPException(422, "Speeds, swath and endurance must be positive.")
    return a


def _treatment(d: dict | None) -> P.Treatment:
    names = {f.name for f in fields(P.Treatment)}
    return P.Treatment(**{k: float(v) for k, v in (d or {}).items() if k in names})


def _select_targets(f: pd.DataFrame, thr: float, pol: dict) -> pd.DataFrame:
    treat = f[(f.conformal_decision == "formation")].assign(action="VERIFY_AND_TREAT")
    parts = [treat]
    if pol.get("include_uncertain", True):
        unc = f[(f.conformal_decision == "uncertain") & ~f.site_id.isin(treat.site_id)]
        unc = unc[unc.probability >= float(pol.get("min_uncertain_probability", 0.05))]
        parts.append(unc.assign(action="VERIFY_ONLY"))
    sel = pd.concat(parts)
    sel["_rank"] = (sel.action == "VERIFY_AND_TREAT").astype(int)
    sel = sel.sort_values(["_rank", "priority_score"], ascending=False).drop(columns="_rank")
    if pol.get("exclude"):
        sel = sel[~sel.site_id.isin(pol["exclude"])]
    return sel.head(max(1, min(int(pol.get("max_targets", 40)), 200)))


def _plan(body: dict) -> dict:
    st = store()
    h = int(body.get("h", 7))
    d, f = st.forecast(h, body.get("date"))
    a, t = _aircraft(body.get("aircraft")), _treatment(body.get("treatment"))
    base = tuple(body.get("base") or (STATION["lat"], STATION["lon"]))
    tg = _select_targets(f, st.thr[h], body.get("policy", {}))
    lc = body.get("launch") or {}
    res = P.plan(tg, a, t, base, launch=lc.get("mode", "mobile"),
                 zone_radius_m=float(lc.get("zone_radius_m", 900)),
                 road_factor=float(lc.get("road_factor", 1.4)), vehicle_kmh=float(lc.get("vehicle_kmh", 25)))
    area = _area()
    res["baseline"] = {"wet_prone_zone": P.lawnmower(area["wet_prone_zone_ha"], a, t),
                       "sector": P.lawnmower(area["sector_hull_ha"], a, t)}
    res["timing"] = P.timing_budget(a)
    res["energy"] = P.energy_per_ha(a)
    res["issue_date"], res["horizon"] = d, h
    res["window"] = {"issue": d, "formation_expected_by": (pd.Timestamp(d) + pd.Timedelta(days=h)).strftime("%Y-%m-%d")}
    res["meta"] = R.forecast_meta(h, d)
    res["authorization_note"] = ("Treatment at any stop requires supervisor approval of this mission and onboard "
                                 "confirmation; the forecast alone never authorises spraying.")
    return res, a, t, lc, base, d, h


@api.post("/missions/plan", tags=["missions"])
def plan_mission(body: dict = Body(...), u: User = Depends(require("read:forecast"))):
    """Planning preview is open to every reader; committing needs plan:mission."""
    res, a, t, lc, base, d, h = _plan(body)
    if body.get("save"):
        if not u.can("plan:mission"):
            raise HTTPException(403 if u.id else 401, "Your role cannot commit missions.")
        mid = f"M-{d.replace('-', '')}-H{h:02d}-{db.now()[11:19].replace(':', '')}"
        res["id"] = mid
        ops.save_mission(mid, d, h, u.name, {"aircraft": asdict(a), "treatment": asdict(t),
                                              "policy": body.get("policy", {}), "base": base, "launch": lc}, res)
        with db.tx() as c:
            c.execute("UPDATE missions SET demo=? WHERE id=?", (int(u.demo), mid))
        ops.audit(u.email, "mission.create", f"{mid}: {res['totals'].get('sorties', 0)} sorties, {res['totals'].get('targets', 0)} targets")
    return res


@api.get("/missions", tags=["missions"])
def missions(u: User = Depends(require("read:forecast"))):
    out = ops.list_missions()
    with db.tx() as c:
        demo = {r["id"]: r["demo"] for r in c.execute("SELECT id, demo FROM missions")}
    for m in out:
        m["demo"] = bool(demo.get(m["id"]))
    return out


@api.get("/missions/{mid}", tags=["missions"])
def mission(mid: str, u: User = Depends(require("read:forecast"))):
    m = ops.get_mission(mid)
    if not m:
        raise HTTPException(404, "Unknown mission.")
    return m


TRANSITIONS = {"PLANNED": {"APPROVED", "CANCELLED"}, "APPROVED": {"IN_FLIGHT", "CANCELLED"},
               "IN_FLIGHT": {"COMPLETED"}, "COMPLETED": set(), "CANCELLED": set()}


@api.post("/missions/{mid}/status", tags=["missions"])
def mission_status(mid: str, body: dict = Body(...), u: User = Depends(require("plan:mission"))):
    s = body.get("status", "")
    m = ops.get_mission(mid)
    if not m:
        raise HTTPException(404, "Unknown mission.")
    if s not in TRANSITIONS.get(m["status"], set()):
        raise HTTPException(409, f"Cannot move a {m['status']} mission to {s}.")
    if s == "APPROVED" and not u.can("approve:mission"):
        raise HTTPException(403, "Only a supervisor can approve a mission for flight.")
    ops.set_status(mid, s)
    if s == "APPROVED":
        with db.tx() as c:
            c.execute("UPDATE missions SET approved_by=?, approved_utc=? WHERE id=?", (u.id, db.now(), mid))
    ops.audit(u.email, "mission.status", f"{mid} {m['status']} -> {s}")
    return {"ok": True}


@api.get("/missions/{mid}/export", tags=["missions"])
def mission_export(mid: str, fmt: str = "plan", sortie: str | None = None, u: User = Depends(require("plan:mission"))):
    m = ops.get_mission(mid)
    if not m:
        raise HTTPException(404, "Unknown mission.")
    res, a = m["result"], _aircraft(m["params"]["aircraft"])
    if fmt in ("plan", "wpl") and m["status"] not in ("APPROVED", "IN_FLIGHT", "COMPLETED"):
        raise HTTPException(409, "Flight-controller files are issued only for missions a supervisor has approved.")
    ops.audit(u.email, "mission.export", f"{mid} {fmt} {sortie or 'all'}")
    if fmt == "geojson":
        return Response(json.dumps(P.to_geojson(res), indent=1), media_type="application/geo+json",
                        headers={"Content-Disposition": f'attachment; filename="{mid}.geojson"'})
    if fmt == "csv":
        rows = [{"sortie": s["id"], **w} for s in res["sorties"] for w in s["waypoints"]]
        return Response(pd.DataFrame(rows).to_csv(index=False), media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="{mid}_targets.csv"'})
    s = next((x for x in res["sorties"] if x["id"] == sortie), res["sorties"][0] if res["sorties"] else None)
    if s is None:
        raise HTTPException(400, "Mission has no sorties.")
    payload = {"base": res["base"], "sortie": s}
    if fmt == "wpl":
        return Response(P.to_wpl(payload, a), media_type="text/plain",
                        headers={"Content-Disposition": f'attachment; filename="{mid}_{s["id"]}.waypoints"'})
    return Response(json.dumps(P.to_qgc_plan(payload, a), indent=2), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{mid}_{s["id"]}.plan"'})


@api.post("/timing", tags=["missions"])
def timing(body: dict = Body(default={}), u: User = Depends(require("read:forecast"))):
    a = _aircraft(body)
    curve = []
    for v in np.round(np.arange(1.0, 10.01, 0.5), 2):
        b = P.timing_budget(P.Aircraft(**(asdict(a) | {"work_speed_ms": float(v)})))
        curve.append({"speed": float(v), "deadline_ms": b["deadline_ms"], "latency_ms": b["latency_ms"]})
    return {"budget": P.timing_budget(a), "energy": P.energy_per_ha(a), "curve": curve}


# ------------------------------------------------------------ flight logs
@api.get("/flightlogs/template", tags=["flight logs"])
def flightlog_template(u: User = Depends(require("read:forecast"))):
    return PlainTextResponse(ops.flight_log_template(), media_type="text/csv", headers={
        "Content-Disposition": 'attachment; filename="rinda_flight_log_template.csv"'})


@api.get("/flightlogs/schema", tags=["flight logs"])
def flightlog_schema(u: User = Depends(require("read:forecast"))):
    return [{"column": c, "description": d, "example": e} for c, d, e in ops.FLIGHT_LOG_COLUMNS]


@api.post("/flightlogs", tags=["flight logs"])
async def flightlog_upload(file: UploadFile = File(...), provenance: str = Form("FIELD"),
                           u: User = Depends(require("upload:flightlog"))):
    raw = await file.read(MAX_UPLOAD + 1)
    if len(raw) > MAX_UPLOAD:
        raise HTTPException(413, "Uploads are limited to 20 MB.")
    if provenance not in {"FIELD", "BENCH", "SYNTHETIC"}:
        raise HTTPException(422, "provenance must be FIELD, BENCH or SYNTHETIC.")
    if u.demo and provenance == "FIELD":
        provenance = "SYNTHETIC"  # demonstration accounts cannot create field records
    try:
        r = ops.ingest_flight_log(raw, (file.filename or "upload.csv")[:120], provenance)
    except Exception:
        raise HTTPException(422, "File is not a readable flight-log CSV.")
    if not r.get("ok"):
        raise HTTPException(422, r.get("error"))
    ops.audit(u.email, "flightlog.ingest", f"{provenance} rows={r['rows']}")
    return r


@api.post("/missions/{mid}/synthetic-log", tags=["flight logs"])
def flightlog_synthetic(mid: str, u: User = Depends(require("upload:flightlog"))):
    m = ops.get_mission(mid)
    if not m:
        raise HTTPException(404, "Unknown mission.")
    raw = ops.synthetic_flight_log(m, m["params"]["aircraft"])
    r = ops.ingest_flight_log(raw, f"{mid}_SYNTHETIC.csv", "SYNTHETIC")
    ops.audit(u.email, "flightlog.synthetic", f"{mid} rows={r.get('rows')}")
    return r


@api.get("/flightlogs", tags=["flight logs"])
def flightlog_list(u: User = Depends(require("read:forecast"))):
    return ops.flight_logs()


@api.delete("/flightlogs/{log_id}", tags=["flight logs"])
def flightlog_delete(log_id: int, u: User = Depends(require("delete:flightlog"))):
    ops.delete_flight_log(log_id)
    ops.audit(u.email, "flightlog.delete", str(log_id))
    return {"ok": True}


@api.get("/flightlogs/analysis", tags=["flight logs"])
def flightlog_analysis(id: int | None = None, u: User = Depends(require("read:forecast"))):
    return ops.analyse_flight_logs(id)


# -------------------------------------------------------------- telemetry
def _station_keys() -> dict:
    """Per-node keys from RINDA_STATION_KEYS (JSON) or var/station_keys.json.
    Placeholder keys are registered for display but can never authenticate."""
    env = os.environ.get("RINDA_STATION_KEYS")
    if env:
        return json.loads(env)
    if not STATION_KEYS_FILE.exists():
        STATION_KEYS_FILE.write_text(json.dumps({"ESP32-GKM-01": "replace-me-01",
                                                 "ESP32-GKM-02": "replace-me-02",
                                                 "ESP32-GKM-03": "replace-me-03"}, indent=2))
    return json.loads(STATION_KEYS_FILE.read_text())


@api.post("/telemetry/{station_id}", tags=["inputs"])
def telemetry_ingest(station_id: str, request: Request, body: dict = Body(...), x_station_key: str = Header(None)):
    S.rate_limit(f"tel:{station_id}", 120)
    keys = _station_keys()
    key = keys.get(station_id)
    if not key or key.startswith("replace-me") or not x_station_key or not hmac.compare_digest(key, x_station_key):
        raise HTTPException(401, "Unknown station or bad key.")
    if len(json.dumps(body)) > 4096:
        raise HTTPException(413, "Telemetry record too large.")
    return ops.ingest_telemetry(station_id, body)


@api.get("/telemetry", tags=["inputs"])
def telemetry(u: User = Depends(require("read:forecast"))):
    keys = _station_keys()
    seen = {r["station_id"]: r for r in ops.telemetry_summary()}
    return [{"station_id": k, "registered": True, **seen.get(k, {"packets": 0, "pdr": None, "last_received_utc": None})}
            for k in keys]


# -------------------------------------------------------------- economics
@api.post("/economics", tags=["programme"])
def economics(body: dict = Body(default={}), u: User = Depends(require("read:forecast"))):
    st = store()
    h = int(body.get("h", 7))
    a, t = _aircraft(body.get("aircraft")), _treatment(body.get("treatment"))
    ver = st.verification(h)
    alerts_per_issue = float(ver.formation_calls.mean())
    unc_per_issue = float(ver.uncertain.mean())
    issues_per_week = len(ver) / max(1, (pd.Timestamp(ver.date.max()) - pd.Timestamp(ver.date.min())).days / 7)
    targets_week = (alerts_per_issue + min(unc_per_issue, 30)) * issues_per_week
    treated_ha_week = alerts_per_issue * issues_per_week * t.patch_area_m2 / 10_000
    sample, *_ = _plan({"h": h, "aircraft": body.get("aircraft"), "treatment": body.get("treatment"),
                        "policy": {"max_targets": 40}})
    per_target_h = (sample["totals"]["field_day_min"] / 60 / max(1, sample["totals"]["targets"])) if sample["totals"] else 0.05
    guided_h_week = targets_week * per_target_h
    area = _area()["wet_prone_zone_ha"]
    blanket = P.lawnmower(area, a, t)
    inputs = {k: v for k, v in (body.get("inputs") or {}).items() if k in ops.DEFAULT_ECON}
    res = ops.economics(inputs, area, targets_week, treated_ha_week, guided_h_week,
                        blanket["flight_min"] / 60, 10_000 / (a.swath_m * a.work_speed_ms) / 3600)
    res["workload"] = {"issues_per_week": issues_per_week, "alerts_per_issue": alerts_per_issue,
                       "uncertain_per_issue": unc_per_issue, "targets_per_week": targets_week,
                       "guided_flight_h_per_week": guided_h_week, "blanket_flight_h_per_week": blanket["flight_min"] / 60}
    return res


# --------------------------------------------------------------- bulletin
@api.get("/bulletin", response_class=HTMLResponse, tags=["programme"])
def bulletin(h: int = 7, date: str | None = None, u: User = Depends(require("read:forecast"))):
    st = store()
    d = st.nearest_date(h, date)
    fc = forecast(h, d, u)
    plan, *_ = _plan({"h": h, "date": d, "policy": {"max_targets": 40}})
    ops.audit(u.email or "public", "bulletin.render", f"h={h} {d}")
    return render_bulletin(fc, plan, st, h, ENGINE.card(), _area(), "en")


# ------------------------------------------------------------------ mount
api.include_router(F.router)
app.include_router(api, prefix="/api/v1")
app.include_router(api, prefix="/api", include_in_schema=False)


@app.get("/api/{rest:path}", include_in_schema=False)
def api_404(rest: str):
    raise HTTPException(404, "Unknown API endpoint. See /api/docs.")


app.mount("/", StaticFiles(directory=WEB, html=False), name="web")

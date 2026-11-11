"""Field surveillance: forecast → inspection task → observation → review →
authorised intervention → follow-up → outcome.

Rules enforced here, not only in the interface:
  * a forecast never authorises an intervention: authorisation needs a verified
    field observation that meets the eligibility rule in operational_rules.json;
  * a missing observation is never a negative finding: "not_sampled" is distinct
    from "no";
  * reviewers cannot verify their own observations;
  * resubmitting the same client_uuid (offline sync) returns the stored record;
  * habitats found away from the 600 radar points are registered with their own
    code (H0001, ...) from the officer's GPS, so they can be reviewed, treated and
    followed up like any monitoring point. They carry no forecast.
"""
from __future__ import annotations

import io
import json
import math
import os
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Literal

import pandas as pd
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field, field_validator

import ops
import programme
from data import haversine, store
from db import now, tx
from engine import ROOT, VAR
from rules import alerts_for, category, rules
from security import User, current_user, require, user_names

router = APIRouter(tags=["field surveillance"])

YesNoUnknown = Literal["yes", "no", "unknown"]
Sampled = Literal["yes", "no", "not_sampled"]
HABITATS = ["rice_paddy", "marsh_swamp", "pond", "puddle", "ditch_drain", "borrow_pit", "stream_edge",
            "container", "other"]
SPECIES = ["an_gambiae_sl", "an_arabiensis", "an_funestus", "an_other", "culicine", "not_identified"]
STAGES = ["early_instar", "late_instar"]
PERMANENCE = ["permanent", "semi_permanent", "temporary"]
SUN = ["sunlit", "partly_shaded", "shaded"]
PROTOCOL_PATH = ROOT / "config" / "protocol_settings.json"
PHOTOS = VAR / "photos"
PHOTO_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
PHOTO_MAGIC = {".jpg": bytes([0xFF, 0xD8, 0xFF]), ".png": bytes([0x89]) + b"PNG", ".webp": b"RIFF"}
MAX_PHOTO = 5 * 1024 * 1024


def protocol() -> dict:
    """Protocol settings awaiting RBC approval, read on each call."""
    return json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))


def protocol_public() -> dict:
    p = protocol()
    return {"status": p["status"], "approved_by": p["approved_by"], "approved_protocols": p["approved_protocols"],
            "intervention": p["intervention"], "habitat_suitability_options": p["habitat_suitability_options"],
            "sampling_methods": p["sampling_methods"],
            "label": "Awaiting approval" if p["status"] != "approved" else "Approved"}


def _demo(u: User) -> int:
    return int(bool(u.demo))


def _site(site_id: str):
    """A radar monitoring point (G0001...) or a field-registered habitat (H0001...)."""
    if site_id and site_id.startswith("H"):
        with tx() as c:
            h = c.execute("SELECT * FROM habitats WHERE code=?", (site_id,)).fetchone()
        if not h:
            raise HTTPException(404, f"Unknown habitat {site_id}.")
        return SimpleNamespace(site_id=h["code"], latitude=h["lat"], longitude=h["lon"], habitat=True)
    s = store().sites
    r = s[s.site_id == site_id]
    if r.empty:
        raise HTTPException(404, f"Unknown site {site_id}.")
    return r.iloc[0]


# ------------------------------------------------------------------- tasks
class TaskIn(BaseModel):
    site_id: str
    issue_date: str | None = None
    horizon: int | None = None
    reason: str = Field(default="", max_length=500)
    priority: Literal["high", "moderate", "insufficient", "lower", "routine"] = "routine"
    due_date: str
    assignee_id: int
    alert_id: str | None = None

    @field_validator("due_date")
    @classmethod
    def _d(cls, v):
        date.fromisoformat(v)
        return v


@router.post("/tasks", status_code=201)
def create_task(t: TaskIn, u: User = Depends(require("assign:task"))):
    _site(t.site_id)
    with tx() as c:
        a = c.execute("SELECT role FROM users WHERE id=? AND active=1", (t.assignee_id,)).fetchone()
        if not a or a["role"] not in ("field_officer", "supervisor"):
            raise HTTPException(422, "Assign tasks to an active field officer or supervisor.")
        cur = c.execute("INSERT INTO tasks (site_id, issue_date, horizon, reason, priority, status, due_date, "
                        "assignee_id, created_by, created_utc, updated_utc, alert_id, demo) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (t.site_id, t.issue_date, t.horizon, t.reason, t.priority, "assigned", t.due_date,
                         t.assignee_id, u.id, now(), now(), t.alert_id, _demo(u)))
        tid = cur.lastrowid
        if t.alert_id:
            c.execute("INSERT OR REPLACE INTO alert_states VALUES (?,?,?,?,?)",
                      (t.alert_id, "actioned", u.id, now(), f"inspection task T{tid}"))
    ops.audit(u.email, "task.create", f"T{tid} site={t.site_id} assignee={t.assignee_id}")
    mid = programme.draft_task_notice(tid, u)
    return {"id": tid, "message_id": mid}


@router.get("/field/assignees")
def assignees(u: User = Depends(require("assign:task"))):
    """People a supervisor can assign inspections to (names and roles only)."""
    with tx() as c:
        rows = c.execute("SELECT id, name, role, demo FROM users WHERE active=1 AND role IN ('field_officer','supervisor') "
                         "AND demo=? ORDER BY name", (_demo(u),)).fetchall()
    return [dict(r) for r in rows]


class TaskStatus(BaseModel):
    status: Literal["in_progress", "completed", "cancelled"]
    note: str = Field(default="", max_length=500)


@router.post("/tasks/{tid}/status")
def task_status(tid: int, b: TaskStatus, u: User = Depends(require("update:own_task"))):
    with tx() as c:
        t = c.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
        if not t:
            raise HTTPException(404, "Unknown task.")
        if t["assignee_id"] != u.id and not u.can("assign:task"):
            raise HTTPException(403, "Only the assignee or a supervisor can update this task.")
        if b.status == "cancelled" and not u.can("assign:task"):
            raise HTTPException(403, "Only a supervisor can cancel a task.")
        if t["status"] in ("completed", "cancelled"):
            raise HTTPException(409, f"Task is already {t['status']}.")
        c.execute("UPDATE tasks SET status=?, updated_utc=? WHERE id=?", (b.status, now(), tid))
    ops.audit(u.email, "task.status", f"T{tid} -> {b.status} {b.note}")
    return {"ok": True}


@router.get("/tasks")
def list_tasks(status: str | None = None, mine: bool = False, u: User = Depends(require("read:field"))):
    q, args = "SELECT * FROM tasks WHERE 1=1", []
    if status:
        q += " AND status=?"
        args.append(status)
    if mine or not u.can("assign:task") and not u.can("plan:mission"):
        q += " AND assignee_id=?"
        args.append(u.id)
    q += " ORDER BY CASE status WHEN 'assigned' THEN 0 WHEN 'in_progress' THEN 1 ELSE 2 END, due_date"
    names = user_names()
    with tx() as c:
        rows = [dict(r) for r in c.execute(q, args)]
    today = datetime.now(timezone.utc).date().isoformat()
    for r in rows:
        r["assignee"] = names.get(r["assignee_id"])
        r["overdue"] = r["status"] in ("assigned", "in_progress") and r["due_date"] < today
    return rows


# ------------------------------------------------------------ observations
class ObservationIn(BaseModel):
    client_uuid: str = Field(min_length=8, max_length=64)
    task_id: int | None = None
    site_id: str | None = Field(default=None, max_length=16)
    new_habitat: bool = False
    habitat_description: str = Field(default="", max_length=300)
    lat: float | None = Field(default=None, ge=-90, le=90)
    lon: float | None = Field(default=None, ge=-180, le=180)
    gps_accuracy_m: float | None = Field(default=None, ge=0, le=10_000)
    observed_at: str
    water_present: YesNoUnknown
    habitat_type: str | None = None
    larvae_present: Sampled
    dips: int | None = Field(default=None, ge=0, le=500)
    larvae_count: int | None = Field(default=None, ge=0, le=100_000)
    anopheles_larvae_count: int | None = Field(default=None, ge=0, le=100_000)
    sampling_method: str | None = Field(default=None, max_length=40)
    anopheles_present: Literal["yes", "no", "not_identified"] = "not_identified"
    species: list[str] = []
    stages: list[str] = []
    pupae_present: Sampled = "not_sampled"
    pupae_count: int | None = Field(default=None, ge=0, le=100_000)
    water_depth_cm: float | None = Field(default=None, ge=0, le=500)
    vegetation_cover_pct: float | None = Field(default=None, ge=0, le=100)
    habitat_permanence: Literal["permanent", "semi_permanent", "temporary"] | None = None
    sun_exposure: Literal["sunlit", "partly_shaded", "shaded"] | None = None
    distance_to_house_m: float | None = Field(default=None, ge=0, le=100_000)
    notes: str = Field(default="", max_length=2000)


def _quality(o: ObservationIn, site) -> tuple[list[str], list[str]]:
    """Returns (errors, flags). Errors reject the record; flags send it to review."""
    q = rules()["observation_quality"]
    errors, flags = [], []
    try:
        ts = datetime.fromisoformat(o.observed_at.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts > datetime.now(timezone.utc) + timedelta(minutes=q["max_future_minutes"]):
            errors.append("Observation time is in the future.")
    except ValueError:
        errors.append("observed_at must be an ISO 8601 date-time.")
    if o.habitat_type and o.habitat_type not in HABITATS:
        errors.append(f"habitat_type must be one of {', '.join(HABITATS)}.")
    if any(s not in SPECIES for s in o.species):
        errors.append(f"species must be drawn from {', '.join(SPECIES)}.")
    if any(s not in STAGES for s in o.stages):
        errors.append(f"stages must be drawn from {', '.join(STAGES)}.")
    if o.larvae_present in ("yes", "no") and not o.dips:
        errors.append("Record the number of dips when larvae were sampled.")
    if o.larvae_present != "yes" and (o.larvae_count or 0) > 0:
        errors.append("A larval count above zero needs larvae_present = yes.")
    if o.anopheles_present == "yes" and o.larvae_present != "yes":
        errors.append("Anopheles presence needs larvae_present = yes.")
    if o.water_present == "no" and o.larvae_present == "yes":
        errors.append("Larvae recorded where no water was present.")
    if o.sampling_method and o.sampling_method not in protocol()["sampling_methods"]:
        errors.append(f"sampling_method must be one of {', '.join(protocol()['sampling_methods'])}.")
    if o.anopheles_larvae_count is not None and o.larvae_count is not None and o.anopheles_larvae_count > o.larvae_count:
        errors.append("Anopheles larvae cannot outnumber all larvae counted.")
    if (o.anopheles_larvae_count or 0) > 0 and o.anopheles_present != "yes":
        errors.append("An Anopheles count above zero needs Anopheles = present.")
    if o.pupae_present != "yes" and (o.pupae_count or 0) > 0:
        errors.append("A pupal count above zero needs pupae_present = yes.")
    if o.lat is None or o.lon is None:
        flags.append("no_gps")
    elif o.new_habitat:
        pass  # the habitat is registered at this very position
    else:
        d = float(haversine(o.lat, o.lon, site.latitude, site.longitude))
        if d > q["max_distance_from_site_m"]:
            flags.append(f"gps_{int(d)}m_from_site")
    if o.gps_accuracy_m is not None and o.gps_accuracy_m > q["max_gps_accuracy_m"]:
        flags.append("gps_low_accuracy")
    if o.larvae_present == "yes" and o.anopheles_present == "not_identified":
        flags.append("species_not_identified")
    return errors, flags


@router.post("/observations", status_code=201)
def create_observation(o: ObservationIn, u: User = Depends(require("write:observation"))):
    with tx() as c:
        dup = c.execute("SELECT id, status FROM observations WHERE client_uuid=?", (o.client_uuid,)).fetchone()
    if dup:  # offline client resubmitting: idempotent
        return {"id": dup["id"], "status": dup["status"], "duplicate": True}
    if o.new_habitat:
        if o.lat is None or o.lon is None:
            raise HTTPException(422, "A new habitat needs a GPS position: use the device GPS at the water edge.")
        if o.task_id:
            raise HTTPException(422, "A new habitat cannot complete a task for another point; leave the task number empty.")
        site = SimpleNamespace(site_id=None, latitude=o.lat, longitude=o.lon, habitat=True)
    else:
        if not o.site_id:
            raise HTTPException(422, "Give a monitoring point or habitat code, or mark the record as a new habitat.")
        site = _site(o.site_id)
    errors, flags = _quality(o, site)
    if errors:
        raise HTTPException(422, " ".join(errors))
    if o.new_habitat:
        flags.append("new_habitat")
    status = "needs_review" if flags else "submitted"
    with tx() as c:
        if o.new_habitat:
            n = c.execute("SELECT COUNT(*) FROM habitats").fetchone()[0]
            code = f"H{n + 1:04d}"
            c.execute("INSERT INTO habitats (code, lat, lon, habitat_type, permanence, description, created_by, "
                      "created_utc, demo) VALUES (?,?,?,?,?,?,?,?,?)",
                      (code, o.lat, o.lon, o.habitat_type, o.habitat_permanence, o.habitat_description, u.id,
                       now(), _demo(u)))
            o.site_id = code
        if o.task_id:
            t = c.execute("SELECT * FROM tasks WHERE id=?", (o.task_id,)).fetchone()
            if not t or t["site_id"] != o.site_id:
                raise HTTPException(422, "Task does not exist or is for a different site.")
        cur = c.execute(
            "INSERT INTO observations (client_uuid, task_id, site_id, lat, lon, gps_accuracy_m, observed_at, "
            "observer_id, water_present, habitat_type, larvae_present, dips, larvae_count, anopheles_present, "
            "species, stages, pupae_present, pupae_count, water_depth_cm, vegetation_cover_pct, notes, "
            "quality_flags, status, created_utc, demo, habitat_permanence, sun_exposure, distance_to_house_m, "
            "sampling_method, anopheles_larvae_count) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (o.client_uuid, o.task_id, o.site_id, o.lat, o.lon, o.gps_accuracy_m, o.observed_at, u.id,
             o.water_present, o.habitat_type, o.larvae_present, o.dips, o.larvae_count, o.anopheles_present,
             ",".join(o.species), ",".join(o.stages), o.pupae_present, o.pupae_count, o.water_depth_cm,
             o.vegetation_cover_pct, o.notes, ",".join(flags), status, now(), _demo(u),
             o.habitat_permanence, o.sun_exposure, o.distance_to_house_m, o.sampling_method, o.anopheles_larvae_count))
        oid = cur.lastrowid
        if o.new_habitat:
            c.execute("UPDATE habitats SET first_observation_id=? WHERE code=?", (oid, o.site_id))
        if o.task_id:
            c.execute("UPDATE tasks SET status='completed', updated_utc=? WHERE id=? AND status IN ('assigned','in_progress')",
                      (now(), o.task_id))
    ops.audit(u.email, "observation.create", f"O{oid} site={o.site_id} status={status} flags={','.join(flags)}")
    return {"id": oid, "status": status, "quality_flags": flags, "site_id": o.site_id,
            "habitat_code": o.site_id if o.new_habitat else None}


class ReviewIn(BaseModel):
    decision: Literal["verified", "needs_review", "returned", "rejected"]
    comment: str = Field(default="", max_length=1000)


@router.post("/observations/{oid}/review")
def review_observation(oid: int, b: ReviewIn, u: User = Depends(require("review:observation"))):
    with tx() as c:
        o = c.execute("SELECT * FROM observations WHERE id=?", (oid,)).fetchone()
        if not o:
            raise HTTPException(404, "Unknown observation.")
        if o["observer_id"] == u.id:
            raise HTTPException(403, "Observations must be reviewed by someone other than the observer.")
        if b.decision != "verified" and not b.comment.strip():
            raise HTTPException(422, "Give a reason when an observation is not verified.")
        c.execute("UPDATE observations SET status=?, reviewer_id=?, review_comment=?, reviewed_utc=? WHERE id=?",
                  (b.decision, u.id, b.comment, now(), oid))
        if b.decision == "verified" and o["larvae_present"] in ("yes", "no"):
            # one standard sample record per verified dipping visit, linked to its observation and task
            names = user_names()
            c.execute("INSERT OR IGNORE INTO entomological_samples (source, observation_id, site_id, task_id, lat, lon, sample_date, collector, "
                      "sampling_method, dips, water_present, larvae_present, anopheles_larvae_count, pupae_count, species, sample_type, provenance, demo, dedupe_key) "
                      "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      ("rinda", oid, o["site_id"], o["task_id"], o["lat"], o["lon"], o["observed_at"][:10], names.get(o["observer_id"]),
                       o["sampling_method"], o["dips"], o["water_present"], o["larvae_present"], o["anopheles_larvae_count"], o["pupae_count"],
                       o["species"], "larval", "DEMONSTRATION" if o["demo"] else "FIELD", o["demo"], f"rinda-obs-{oid}"))
    ops.audit(u.email, "observation.review", f"O{oid} site={o['site_id']} -> {b.decision}")
    return {"ok": True}


def _obs_rows(where: str = "1=1", args: tuple = ()) -> list[dict]:
    names = user_names()
    with tx() as c:
        rows = [dict(r) for r in c.execute(f"SELECT * FROM observations WHERE {where} ORDER BY observed_at DESC", args)]
    for r in rows:
        r["observer"] = names.get(r["observer_id"])
        r["reviewer"] = names.get(r["reviewer_id"])
        r["species"] = [s for s in (r["species"] or "").split(",") if s]
        r["stages"] = [s for s in (r["stages"] or "").split(",") if s]
        r["quality_flags"] = [s for s in (r["quality_flags"] or "").split(",") if s]
        # larval density as larval surveys report it: larvae per dip (derived, never stored)
        r["larvae_per_dip"] = (round(r["larvae_count"] / r["dips"], 2)
                               if r["dips"] and r["larvae_count"] is not None and r["larvae_present"] == "yes"
                               else (0.0 if r["larvae_present"] == "no" else None))
        r["anopheles_per_dip"] = (round(r["anopheles_larvae_count"] / r["dips"], 2)
                                  if r["dips"] and r.get("anopheles_larvae_count") is not None else None)
    return rows


@router.get("/observations")
def list_observations(site_id: str | None = None, status: str | None = None,
                      u: User = Depends(require("read:field"))):
    where, args = ["1=1"], []
    if site_id:
        where.append("site_id=?")
        args.append(site_id)
    if status:
        where.append("status=?")
        args.append(status)
    if not (u.can("review:observation") or u.can("plan:mission")):
        where.append("observer_id=?")
        args.append(u.id)
    return _obs_rows(" AND ".join(where), tuple(args))


# ------------------------------------------------------------ interventions
class AuthoriseIn(BaseModel):
    observation_id: int
    method: str
    product: str = Field(default="", max_length=200)
    planned_dose: str = Field(default="", max_length=200)
    protocol_ref: str = Field(min_length=3, max_length=200)
    habitat_suitability: str | None = None


def _eligible(o) -> bool:
    """Evidence needed before a supervisor may decide. Settings are pending RBC approval."""
    p = protocol()["intervention"]
    if o["status"] != "verified":
        return False
    if o["larvae_present"] == "yes":
        if p["require_anopheles_confirmation"] and o["anopheles_present"] != "yes":
            return False
        thr = p["minimum_larvae_per_dip"]
        if thr is not None and (not o["dips"] or (o["larvae_count"] or 0) / o["dips"] < float(thr)):
            return False
        return True
    return bool(p["accept_water_without_larvae_sample"]) and o["water_present"] == "yes" and o["larvae_present"] == "not_sampled"


@router.post("/interventions", status_code=201)
def authorise(b: AuthoriseIn, u: User = Depends(require("authorize:intervention"))):
    ir = rules()["intervention"]
    if b.method not in ir["methods"]:
        raise HTTPException(422, f"method must be one of {', '.join(ir['methods'])}.")
    with tx() as c:
        o = c.execute("SELECT * FROM observations WHERE id=?", (b.observation_id,)).fetchone()
        if not o:
            raise HTTPException(404, "Unknown observation.")
        if not _eligible(o):
            raise HTTPException(409, "Interventions need a verified field observation with larvae found. "
                                     "A forecast alone cannot authorise treatment.")
        opts = protocol()["habitat_suitability_options"]
        if b.habitat_suitability not in opts:
            raise HTTPException(422, f"Record the habitat suitability assessment: one of {', '.join(opts)}.")
        if b.habitat_suitability in ("not_suitable", "needs_second_visit"):
            raise HTTPException(409, "This habitat was assessed as not suitable for treatment now. Record the decision as rejected instead.")
        if b.method == "bti_drone" and b.habitat_suitability != "suitable_for_drone":
            raise HTTPException(409, "Drone application needs a habitat assessed as suitable for drone treatment.")
        if c.execute("SELECT 1 FROM interventions WHERE observation_id=? AND status IN ('authorized','performed')", (o["id"],)).fetchone():
            raise HTTPException(409, "This observation already has an open intervention.")
        cur = c.execute("INSERT INTO interventions (site_id, observation_id, method, product, planned_dose, protocol_ref, "
                        "status, authorized_by, authorized_utc, demo, habitat_suitability, protocol_status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (o["site_id"], o["id"], b.method, b.product, b.planned_dose, b.protocol_ref, "authorized",
                         u.id, now(), _demo(u), b.habitat_suitability, protocol()["status"]))
        iid = cur.lastrowid
    ops.audit(u.email, "intervention.authorize", f"I{iid} site={o['site_id']} O{o['id']} {b.method} protocol={b.protocol_ref}")
    return {"id": iid, "status": "authorized", "protocol_status": protocol()["status"]}


class RejectIn(BaseModel):
    observation_id: int
    reason: str = Field(min_length=5, max_length=1000)
    habitat_suitability: str | None = None


@router.post("/interventions/reject", status_code=201)
def reject_intervention(b: RejectIn, u: User = Depends(require("authorize:intervention"))):
    """A supervisor's decision not to treat, kept as a record with its reason."""
    with tx() as c:
        o = c.execute("SELECT * FROM observations WHERE id=?", (b.observation_id,)).fetchone()
        if not o:
            raise HTTPException(404, "Unknown observation.")
        if o["status"] != "verified":
            raise HTTPException(409, "Only a verified observation can be assessed for intervention.")
        cur = c.execute("INSERT INTO interventions (site_id, observation_id, method, protocol_ref, status, authorized_by, authorized_utc, demo, "
                        "habitat_suitability, protocol_status, decision_reason) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (o["site_id"], o["id"], "none", "not applicable", "rejected", u.id, now(), _demo(u), b.habitat_suitability,
                         protocol()["status"], b.reason))
    ops.audit(u.email, "intervention.reject", f"I{cur.lastrowid} site={o['site_id']} O{o['id']}: {b.reason}")
    return {"id": cur.lastrowid, "status": "rejected"}


@router.get("/protocol")
def get_protocol(u: User = Depends(require("read:forecast"))):
    return protocol_public()


# ------------------------------------------------------------------- photos
@router.post("/observations/{oid}/photos", status_code=201)
async def add_photo(oid: int, file: UploadFile = File(...), u: User = Depends(require("write:observation"))):
    with tx() as c:
        o = c.execute("SELECT observer_id FROM observations WHERE id=?", (oid,)).fetchone()
        n = c.execute("SELECT COUNT(*) FROM observation_photos WHERE observation_id=?", (oid,)).fetchone()[0]
    if not o:
        raise HTTPException(404, "Unknown observation.")
    if o["observer_id"] != u.id and not u.can("review:observation"):
        raise HTTPException(403, "Only the observer or a supervisor can add photographs.")
    if n >= 6:
        raise HTTPException(409, "An observation holds at most six photographs.")
    ext = PHOTO_TYPES.get(file.content_type or "")
    if not ext:
        raise HTTPException(422, "Photographs must be JPEG, PNG or WebP.")
    raw = await file.read(MAX_PHOTO + 1)
    if len(raw) > MAX_PHOTO:
        raise HTTPException(413, "Photographs are limited to 5 MB.")
    if not raw.startswith(PHOTO_MAGIC[ext]):
        raise HTTPException(422, "The file is not a valid image of the stated type.")
    PHOTOS.mkdir(parents=True, exist_ok=True)
    name = f"O{oid}_{os.urandom(8).hex()}{ext}"
    (PHOTOS / name).write_bytes(raw)
    with tx() as c:
        cur = c.execute("INSERT INTO observation_photos (observation_id, path, content_type, bytes, uploaded_by, uploaded_utc) VALUES (?,?,?,?,?,?)",
                        (oid, name, file.content_type, len(raw), u.id, now()))
    ops.audit(u.email, "observation.photo", f"O{oid} photo {cur.lastrowid}")
    return {"id": cur.lastrowid}


@router.get("/photos/{pid}")
def get_photo(pid: int, u: User = Depends(require("read:field"))):
    with tx() as c:
        p = c.execute("SELECT p.*, o.observer_id FROM observation_photos p JOIN observations o ON o.id=p.observation_id WHERE p.id=?", (pid,)).fetchone()
    if not p:
        raise HTTPException(404, "Unknown photograph.")
    if p["observer_id"] != u.id and not (u.can("review:observation") or u.can("plan:mission")):
        raise HTTPException(403, "You can see photographs of your own observations only.")
    return FileResponse(PHOTOS / p["path"], media_type=p["content_type"], headers={"Cache-Control": "private, no-store"})


class PerformIn(BaseModel):
    performed_at: str
    quantity_used: str = Field(min_length=1, max_length=200)
    notes: str = Field(default="", max_length=1000)


@router.post("/interventions/{iid}/performed")
def performed(iid: int, b: PerformIn, u: User = Depends(require("record:intervention"))):
    with tx() as c:
        i = c.execute("SELECT * FROM interventions WHERE id=?", (iid,)).fetchone()
        if not i:
            raise HTTPException(404, "Unknown intervention.")
        if i["status"] != "authorized":
            raise HTTPException(409, f"Intervention is {i['status']}; only authorised interventions can be recorded as performed.")
        c.execute("UPDATE interventions SET status='performed', performed_by=?, performed_at=?, quantity_used=?, "
                  "performance_notes=? WHERE id=?", (u.id, b.performed_at, b.quantity_used, b.notes, iid))
    ops.audit(u.email, "intervention.performed", f"I{iid}")
    return {"ok": True}


class FollowUpIn(BaseModel):
    observation_id: int
    outcome: Literal["larvae_absent", "larvae_reduced", "larvae_unchanged", "inconclusive"]


@router.post("/interventions/{iid}/followup")
def followup(iid: int, b: FollowUpIn, u: User = Depends(require("review:observation"))):
    with tx() as c:
        i = c.execute("SELECT * FROM interventions WHERE id=?", (iid,)).fetchone()
        o = c.execute("SELECT * FROM observations WHERE id=?", (b.observation_id,)).fetchone()
        if not i or not o:
            raise HTTPException(404, "Unknown intervention or observation.")
        if i["status"] != "performed":
            raise HTTPException(409, "Record the treatment as performed before closing it with a follow-up.")
        if o["larvae_present"] == "not_sampled":
            raise HTTPException(422, "A follow-up must include dipping. Not sampled is not a finding.")
        if o["site_id"] != i["site_id"] or o["status"] != "verified" or o["observed_at"] <= (i["performed_at"] or ""):
            raise HTTPException(422, "Follow-up must be a verified observation at the same site, made after treatment.")
        c.execute("UPDATE interventions SET status='closed', followup_observation_id=?, outcome=?, closed_utc=? WHERE id=?",
                  (o["id"], b.outcome, now(), iid))
    ops.audit(u.email, "intervention.followup", f"I{iid} O{o['id']} {b.outcome}")
    return {"ok": True}


@router.get("/interventions")
def list_interventions(u: User = Depends(require("read:field"))):
    names = user_names()
    with tx() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM interventions ORDER BY id DESC")]
    for r in rows:
        r["authorized_by_name"] = names.get(r["authorized_by"])
        r["performed_by_name"] = names.get(r["performed_by"])
    return rows


# ----------------------------------------------------------------- habitats
@router.get("/habitats")
def list_habitats(u: User = Depends(require("read:field"))):
    """Habitats registered in the field, away from the radar monitoring points."""
    with tx() as c:
        return [dict(r) for r in c.execute("SELECT code, lat, lon, habitat_type, permanence, description, "
                                           "created_utc, first_observation_id, demo FROM habitats ORDER BY code")]


# ------------------------------------------------------------------ summary
@router.get("/field/summary")
def field_summary(u: User = Depends(current_user)):
    """Counts only: safe for every role, including public viewers."""
    with tx() as c:
        t = {r[0]: r[1] for r in c.execute("SELECT status, COUNT(*) FROM tasks GROUP BY status")}
        o = {r[0]: r[1] for r in c.execute("SELECT status, COUNT(*) FROM observations GROUP BY status")}
        i = {r[0]: r[1] for r in c.execute("SELECT status, COUNT(*) FROM interventions GROUP BY status")}
        last = c.execute("SELECT MAX(observed_at) FROM observations").fetchone()[0]
        overdue = c.execute("SELECT COUNT(*) FROM tasks WHERE status IN ('assigned','in_progress') AND due_date < ?",
                            (datetime.now(timezone.utc).date().isoformat(),)).fetchone()[0]
        demo = c.execute("SELECT (SELECT COUNT(*) FROM tasks WHERE demo=1)+(SELECT COUNT(*) FROM observations WHERE demo=1)"
                         "+(SELECT COUNT(*) FROM interventions WHERE demo=1)").fetchone()[0]
    return {"tasks": t, "observations": o, "interventions": i, "last_observation": last,
            "overdue_tasks": overdue, "demo_records": demo}


# ------------------------------------------------------------------- alerts
def _alert_states() -> dict:
    with tx() as c:
        return {r["alert_id"]: dict(r) for r in c.execute("SELECT * FROM alert_states")}


@router.get("/alerts")
def list_alerts(h: int = 7, date: str | None = None, u: User = Depends(require("read:forecast"))):
    d, f = store().forecast(h, date)
    return alerts_for(h, d, f, _alert_states())


class AlertAction(BaseModel):
    state: Literal["acknowledged", "dismissed"]
    note: str = Field(default="", max_length=500)


@router.post("/alerts/{aid}")
def act_alert(aid: str, b: AlertAction, u: User = Depends(require("act:alert"))):
    if b.state == "dismissed" and not b.note.strip():
        raise HTTPException(422, "Give a reason for dismissing an alert.")
    with tx() as c:
        c.execute("INSERT OR REPLACE INTO alert_states VALUES (?,?,?,?,?)", (aid, b.state, u.id, now(), b.note))
    ops.audit(u.email, f"alert.{b.state}", f"{aid} {b.note}")
    return {"ok": True}


# ------------------------------------------------------------------ exports
@router.get("/field/export/{kind}.csv")
def export(kind: Literal["observations", "interventions", "tasks"], u: User = Depends(require("export:field"))):
    if kind == "observations":
        rows = _obs_rows()
        for r in rows:
            r["species"], r["stages"], r["quality_flags"] = ";".join(r["species"]), ";".join(r["stages"]), ";".join(r["quality_flags"])
    elif kind == "interventions":
        rows = list_interventions(u)
    else:
        rows = list_tasks(None, False, u)
    ops.audit(u.email, "export.field", kind)
    df = pd.DataFrame(rows)
    if "demo" in df:
        df.insert(0, "record_type", df.pop("demo").map({1: "DEMONSTRATION", 0: "operational"}))
    return Response(df.to_csv(index=False), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="rinda_{kind}.csv"'})


# ----------------------------------------------------------------- feedback
class FeedbackIn(BaseModel):
    page: str = Field(default="", max_length=100)
    message: str = Field(min_length=3, max_length=3000)


@router.post("/feedback", status_code=201)
def feedback(b: FeedbackIn, u: User = Depends(current_user)):
    with tx() as c:
        c.execute("INSERT INTO feedback (ts_utc, user_id, page, message) VALUES (?,?,?,?)", (now(), u.id, b.page, b.message))
    return {"ok": True}


@router.get("/feedback")
def list_feedback(u: User = Depends(require("admin:users"))):
    with tx() as c:
        return [dict(r) for r in c.execute("SELECT * FROM feedback ORDER BY id DESC LIMIT 200")]


def site_field_history(site_id: str) -> dict:
    """Latest field evidence for a site dossier (records only, no personal names)."""
    with tx() as c:
        obs = [dict(r) for r in c.execute(
            "SELECT id, observed_at, water_present, larvae_present, anopheles_present, larvae_count, dips, status, demo "
            "FROM observations WHERE site_id=? ORDER BY observed_at DESC LIMIT 5", (site_id,))]
        iv = [dict(r) for r in c.execute(
            "SELECT id, method, status, authorized_utc, performed_at, outcome, demo, decision_reason FROM interventions "
            "WHERE site_id=? ORDER BY id DESC LIMIT 5", (site_id,))]
    return {"observations": obs, "interventions": iv}

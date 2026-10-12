"""Field surveillance: forecast → inspection task → observation → review →
authorised intervention → follow-up → outcome.

Rules enforced here, not only in the interface:
  * a forecast never authorises an intervention: authorisation needs a verified
    field observation that meets the eligibility rule in operational_rules.json;
  * a missing observation is never a negative finding: "not_sampled" is distinct
    from "no";
  * reviewers cannot verify their own observations;
  * resubmitting the same client_uuid (offline sync) returns the stored record.
"""
from __future__ import annotations

import io
import math
from datetime import date, datetime, timedelta, timezone
from typing import Literal

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator

import ops
from data import haversine, store
from db import now, tx
from rules import alerts_for, category, rules
from security import User, current_user, require, user_names

router = APIRouter(tags=["field surveillance"])

YesNoUnknown = Literal["yes", "no", "unknown"]
Sampled = Literal["yes", "no", "not_sampled"]
HABITATS = ["rice_paddy", "marsh_swamp", "pond", "puddle", "ditch_drain", "borrow_pit", "stream_edge",
            "container", "other"]
SPECIES = ["an_gambiae_sl", "an_arabiensis", "an_funestus", "an_other", "culicine", "not_identified"]
STAGES = ["early_instar", "late_instar"]


def _demo(u: User) -> int:
    return int(bool(u.demo))


def _site(site_id: str):
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
    return {"id": tid}


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
    site_id: str
    lat: float | None = Field(default=None, ge=-90, le=90)
    lon: float | None = Field(default=None, ge=-180, le=180)
    gps_accuracy_m: float | None = Field(default=None, ge=0, le=10_000)
    observed_at: str
    water_present: YesNoUnknown
    habitat_type: str | None = None
    larvae_present: Sampled
    dips: int | None = Field(default=None, ge=0, le=500)
    larvae_count: int | None = Field(default=None, ge=0, le=100_000)
    anopheles_present: Literal["yes", "no", "not_identified"] = "not_identified"
    species: list[str] = []
    stages: list[str] = []
    pupae_present: Sampled = "not_sampled"
    pupae_count: int | None = Field(default=None, ge=0, le=100_000)
    water_depth_cm: float | None = Field(default=None, ge=0, le=500)
    vegetation_cover_pct: float | None = Field(default=None, ge=0, le=100)
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
    if o.pupae_present != "yes" and (o.pupae_count or 0) > 0:
        errors.append("A pupal count above zero needs pupae_present = yes.")
    if o.lat is None or o.lon is None:
        flags.append("no_gps")
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
    site = _site(o.site_id)
    errors, flags = _quality(o, site)
    if errors:
        raise HTTPException(422, " ".join(errors))
    status = "needs_review" if flags else "submitted"
    with tx() as c:
        if o.task_id:
            t = c.execute("SELECT * FROM tasks WHERE id=?", (o.task_id,)).fetchone()
            if not t or t["site_id"] != o.site_id:
                raise HTTPException(422, "Task does not exist or is for a different site.")
        cur = c.execute(
            "INSERT INTO observations (client_uuid, task_id, site_id, lat, lon, gps_accuracy_m, observed_at, "
            "observer_id, water_present, habitat_type, larvae_present, dips, larvae_count, anopheles_present, "
            "species, stages, pupae_present, pupae_count, water_depth_cm, vegetation_cover_pct, notes, "
            "quality_flags, status, created_utc, demo) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (o.client_uuid, o.task_id, o.site_id, o.lat, o.lon, o.gps_accuracy_m, o.observed_at, u.id,
             o.water_present, o.habitat_type, o.larvae_present, o.dips, o.larvae_count, o.anopheles_present,
             ",".join(o.species), ",".join(o.stages), o.pupae_present, o.pupae_count, o.water_depth_cm,
             o.vegetation_cover_pct, o.notes, ",".join(flags), status, now(), _demo(u)))
        oid = cur.lastrowid
        if o.task_id:
            c.execute("UPDATE tasks SET status='completed', updated_utc=? WHERE id=? AND status IN ('assigned','in_progress')",
                      (now(), o.task_id))
    ops.audit(u.email, "observation.create", f"O{oid} site={o.site_id} status={status} flags={','.join(flags)}")
    return {"id": oid, "status": status, "quality_flags": flags}


class ReviewIn(BaseModel):
    decision: Literal["verified", "needs_review", "rejected"]
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
    ops.audit(u.email, "observation.review", f"O{oid} -> {b.decision}")
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


def _eligible(o) -> bool:
    return o["status"] == "verified" and (o["larvae_present"] == "yes" or
                                         (o["water_present"] == "yes" and o["larvae_present"] == "not_sampled"))


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
            raise HTTPException(409, "Interventions need a verified observation with larvae present "
                                     "(or water present and larvae not yet sampled). A forecast alone cannot authorise treatment.")
        cur = c.execute("INSERT INTO interventions (site_id, observation_id, method, product, planned_dose, protocol_ref, "
                        "status, authorized_by, authorized_utc, demo) VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (o["site_id"], o["id"], b.method, b.product, b.planned_dose, b.protocol_ref, "authorized",
                         u.id, now(), _demo(u)))
        iid = cur.lastrowid
    ops.audit(u.email, "intervention.authorize", f"I{iid} site={o['site_id']} O{o['id']} {b.method} protocol={b.protocol_ref}")
    return {"id": iid, "status": "authorized"}


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
            "SELECT id, method, status, authorized_utc, performed_at, outcome, demo FROM interventions "
            "WHERE site_id=? ORDER BY id DESC LIMIT 5", (site_id,))]
    return {"observations": obs, "interventions": iv}

"""Automated larva counts on field photographs (an unverified reading, never evidence on its own).

A supervisor can ask for an automated count of larvae on a dip photograph. The photograph is sent to a hosted
object-detection model (Roboflow Serverless, model "larvae_new-dataset-8000/1" by default) and the detections are
stored next to the photograph. The count is shown as "automated, unverified". It never changes an observation's
larval result, a habitat's status or a treatment decision: those stay with the observer and the supervisor's review.

Off by default, because photographs leave RINDA for a third-party service. To switch it on, the operator sets:
  RINDA_LARVA_DETECTION=roboflow           explicit opt-in
  ROBOFLOW_API_KEY=<key>                   read from the environment only; sent in the Authorization header
  RINDA_LARVA_MODEL=larvae_new-dataset-8000/1   optional
and installs the client: pip install -r requirements-detection.txt (inference-sdk >= 1.5, header auth).
"""
from __future__ import annotations

import json
import os
from collections import Counter

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

import ops
from db import now, tx
from engine import VAR
from security import User, require

router = APIRouter(tags=["field surveillance"])
PHOTOS = VAR / "photos"
API_URL = "https://serverless.roboflow.com"
DEFAULT_MODEL = "larvae_new-dataset-8000/1"
CAUTION = ("Automated count from a hosted object-detection model, not checked against these photographs. It does not confirm "
           "larvae, species or stage; the observation and the supervisor's review decide.")


def settings() -> dict:
    opted_in = os.environ.get("RINDA_LARVA_DETECTION", "").strip().lower() == "roboflow"
    has_key = bool(os.environ.get("ROBOFLOW_API_KEY", "").strip())
    try:
        import inference_sdk  # noqa: F401
        installed = True
    except ImportError:
        installed = False
    reason = None
    if not opted_in:
        reason = "Automated larva counts are switched off on this installation (RINDA_LARVA_DETECTION is not set to roboflow)."
    elif not has_key:
        reason = "No Roboflow API key is configured (ROBOFLOW_API_KEY)."
    elif not installed:
        reason = "The detection client is not installed (pip install -r requirements-detection.txt)."
    return {"enabled": reason is None, "provider": "roboflow", "model_id": os.environ.get("RINDA_LARVA_MODEL", DEFAULT_MODEL),
            "reason": reason, "sends_photographs_to": API_URL if opted_in else None, "caution": CAUTION}


def infer(path: str, model_id: str) -> dict:
    """One call to the hosted model. The key is read here and sent as a Bearer header, never in the query string."""
    from inference_sdk import InferenceConfiguration, InferenceHTTPClient
    client = InferenceHTTPClient(api_url=API_URL, api_key=os.environ["ROBOFLOW_API_KEY"].strip()).configure(
        InferenceConfiguration(api_key_transport="header"))
    return client.infer(path, model_id=model_id)


def summarise(result: dict, min_conf: float) -> dict:
    """Keep what RINDA shows: boxes at or above min_conf, counts per class, image size."""
    preds = result.get("predictions", []) if isinstance(result, dict) else []
    keep = [{k: p.get(k) for k in ("class", "confidence", "x", "y", "width", "height")} for p in preds
            if (p.get("confidence") or 0) >= min_conf]
    img = result.get("image", {}) if isinstance(result, dict) else {}
    return {"n": len(keep), "counts": dict(Counter(p["class"] for p in keep)), "predictions": keep,
            "width": img.get("width"), "height": img.get("height")}


def _row(r) -> dict:
    d = dict(r)
    d["counts"] = json.loads(d["counts"]) if d.get("counts") else {}
    d["predictions"] = json.loads(d["predictions"]) if d.get("predictions") else []
    d["label"] = "Automated count, unverified"
    return d


@router.get("/larva-detection/status")
def status(u: User = Depends(require("read:field"))):
    return settings()


class DetectIn(BaseModel):
    min_confidence: float = Field(default=0.4, ge=0.05, le=0.95)


@router.post("/photos/{pid}/detections", status_code=201)
def run_detection(pid: int, b: DetectIn | None = None, u: User = Depends(require("review:observation"))):
    b = b or DetectIn()
    st = settings()
    if not st["enabled"]:
        raise HTTPException(409, st["reason"])
    with tx() as c:
        p = c.execute("SELECT id, path FROM observation_photos WHERE id=?", (pid,)).fetchone()
    if not p:
        raise HTTPException(404, "Unknown photograph.")
    f = PHOTOS / p["path"]
    if not f.exists():
        raise HTTPException(404, "The photograph file is missing on this server.")
    try:
        s = summarise(infer(str(f), st["model_id"]), b.min_confidence)
        rec = ("done", s["n"], json.dumps(s["counts"]), json.dumps(s["predictions"]), s["width"], s["height"], None)
    except Exception as e:  # the service, the network or the key failed: record it, never invent a count
        rec = ("failed", None, None, None, None, None, f"{type(e).__name__}: {str(e)[:300]}")
    with tx() as c:
        cur = c.execute("INSERT INTO photo_detections (photo_id, provider, model_id, run_utc, actor, status, n_detections, counts, predictions, "
                        "image_width, image_height, min_confidence, error) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (pid, "roboflow", st["model_id"], now(), u.email, rec[0], *rec[1:6], b.min_confidence, rec[6]))
        row = c.execute("SELECT * FROM photo_detections WHERE id=?", (cur.lastrowid,)).fetchone()
    ops.audit(u.email, "photo.detection", f"photo {pid} {rec[0]} model {st['model_id']}")
    if rec[0] == "failed":
        raise HTTPException(502, "The detection service did not return a result; nothing was counted. " + rec[6])
    return {**_row(row), "caution": CAUTION}


@router.get("/photos/{pid}/detections")
def list_detections(pid: int, u: User = Depends(require("read:field"))):
    with tx() as c:
        if not c.execute("SELECT 1 FROM observation_photos WHERE id=?", (pid,)).fetchone():
            raise HTTPException(404, "Unknown photograph.")
        rows = c.execute("SELECT * FROM photo_detections WHERE photo_id=? ORDER BY id DESC", (pid,)).fetchall()
    return {"photo_id": pid, "detections": [_row(r) for r in rows], "caution": CAUTION}

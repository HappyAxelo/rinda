"""Automated larva counts: off by default, opt-in only, stored as unverified readings, never change the observation."""
from __future__ import annotations

import sys
import types

import larva_detect as LD
from conftest import W
from test_rebuild import site_pos
from test_workflow import obs

FAKE = {"image": {"width": 640, "height": 480}, "predictions": [
    {"class": "larva", "confidence": 0.91, "x": 100, "y": 120, "width": 30, "height": 12},
    {"class": "larva", "confidence": 0.62, "x": 300, "y": 200, "width": 28, "height": 10},
    {"class": "pupa", "confidence": 0.55, "x": 410, "y": 90, "width": 20, "height": 20},
    {"class": "larva", "confidence": 0.20, "x": 500, "y": 300, "width": 25, "height": 10}]}


def _photo(roles, tag):
    fo, sup = roles["field_officer"], roles["supervisor"]
    lat, lon = site_pos(sup, "G0442")
    o = fo.post("/api/v1/observations", json=obs("G0442", lat=lat, lon=lon, tag=tag), headers=W).json()
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 64
    return o, fo.post(f"/api/v1/observations/{o['id']}/photos", files={"file": ("d.png", png, "image/png")}, headers=W).json()["id"]


def test_off_by_default(roles, monkeypatch):
    monkeypatch.delenv("RINDA_LARVA_DETECTION", raising=False)
    s = roles["supervisor"].get("/api/v1/larva-detection/status").json()
    assert s["enabled"] is False and "switched off" in s["reason"] and s["sends_photographs_to"] is None
    _, pid = _photo(roles, "ld-off")
    assert roles["supervisor"].post(f"/api/v1/photos/{pid}/detections", json={}, headers=W).status_code == 409


def test_needs_key_even_when_opted_in(roles, monkeypatch):
    monkeypatch.setenv("RINDA_LARVA_DETECTION", "roboflow")
    monkeypatch.delenv("ROBOFLOW_API_KEY", raising=False)
    s = roles["supervisor"].get("/api/v1/larva-detection/status").json()
    assert s["enabled"] is False and "API key" in s["reason"]


def test_count_is_stored_as_unverified_reading(roles, monkeypatch):
    monkeypatch.setenv("RINDA_LARVA_DETECTION", "roboflow")
    monkeypatch.setenv("ROBOFLOW_API_KEY", "test-key-not-real")
    monkeypatch.setitem(sys.modules, "inference_sdk", types.ModuleType("inference_sdk"))
    seen = {}
    monkeypatch.setattr(LD, "infer", lambda path, model_id: seen.update(path=path, model=model_id) or FAKE)
    o, pid = _photo(roles, "ld-on")
    fo, sup = roles["field_officer"], roles["supervisor"]
    assert fo.post(f"/api/v1/photos/{pid}/detections", json={}, headers=W).status_code == 403   # supervisors only
    r = sup.post(f"/api/v1/photos/{pid}/detections", json={"min_confidence": 0.5}, headers=W)
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["n_detections"] == 3 and d["counts"] == {"larva": 2, "pupa": 1} and d["label"] == "Automated count, unverified"
    assert seen["model"] == "larvae_new-dataset-8000/1"
    # the observation keeps the officer's own result
    rec = next(x for x in fo.get("/api/v1/observations").json() if x["id"] == o["id"])
    assert rec["larvae_count"] == 14
    # dossier shows the latest count next to the photograph
    ph = next(p for p in sup.get("/api/v1/dossier/G0442").json()["photos"] if p["id"] == pid)
    assert ph["auto_count"] == 3 and ph["auto_counts"] == {"larva": 2, "pupa": 1}
    assert sup.get(f"/api/v1/photos/{pid}/detections").json()["detections"][0]["status"] == "done"


def test_service_failure_records_no_count(roles, monkeypatch):
    monkeypatch.setenv("RINDA_LARVA_DETECTION", "roboflow")
    monkeypatch.setenv("ROBOFLOW_API_KEY", "test-key-not-real")
    monkeypatch.setitem(sys.modules, "inference_sdk", types.ModuleType("inference_sdk"))

    def boom(path, model_id):
        raise ConnectionError("unreachable")
    monkeypatch.setattr(LD, "infer", boom)
    _, pid = _photo(roles, "ld-fail")
    sup = roles["supervisor"]
    assert sup.post(f"/api/v1/photos/{pid}/detections", json={}, headers=W).status_code == 502
    d = sup.get(f"/api/v1/photos/{pid}/detections").json()["detections"][0]
    assert d["status"] == "failed" and d["n_detections"] is None and "unreachable" in d["error"]

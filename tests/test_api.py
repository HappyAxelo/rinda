"""End-to-end smoke tests: the console's guarantees, exercised through the API."""
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("RINDA_VAR", tempfile.mkdtemp(prefix="rinda-test-"))
sys.path.insert(0, str(ROOT / "server"))

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402


@pytest.fixture(scope="module")
def client():
    with TestClient(main.app) as c:
        yield c


def test_engine_reproduces_published_7day_score(client):
    rep = client.get("/api/bootstrap").json()["model"]["horizons"]["7"]["reproduction"]
    assert rep["abs_diff_pr_auc"] < 0.01


def test_forecast_rows_carry_claim_and_calls(client):
    f = client.get("/api/forecast?h=7").json()
    assert "No entomological claim" in f["claim"]
    assert f["rows"] and {"formation", "uncertain", "no_formation"} >= {r["conformal_decision"] for r in f["rows"]}
    assert [r["priority_rank"] for r in f["rows"][:3]] == [1, 2, 3]


def test_plan_respects_endurance_and_locks_uncertain_targets(client):
    res = client.post("/api/missions/plan", json={"h": 7, "date": "2025-11-26", "policy": {"max_targets": 40}}).json()
    assert res["totals"]["infeasible_sorties"] == 0
    for s in res["sorties"]:
        for w in s["waypoints"]:
            if w["decision"] == "uncertain":
                assert w["action"] == "VERIFY_ONLY"


def test_mission_exports(client):
    res = client.post("/api/missions/plan", json={"h": 7, "date": "2025-11-26", "save": True}).json()
    mid = res["id"]
    assert client.get(f"/api/missions/{mid}/export?fmt=wpl").text.startswith("QGC WPL 110")
    plan = client.get(f"/api/missions/{mid}/export?fmt=plan").json()
    assert plan["fileType"] == "Plan" and plan["mission"]["items"][-1]["command"] == 20


def test_synthetic_logs_are_stamped(client):
    mid = client.get("/api/missions").json()[0]["id"]
    client.post(f"/api/missions/{mid}/synthetic-log")
    an = client.get("/api/flightlogs/analysis").json()
    assert "SYNTHETIC" in an["provenance"]


def test_placeholder_station_keys_never_authenticate(client):
    r = client.post("/api/telemetry/ESP32-GKM-01", json={"seq": 1}, headers={"X-Station-Key": "replace-me-01"})
    assert r.status_code == 401


def test_scenario_and_bulletin(client):
    s = client.get("/api/scenario?site=G0221&date=2025-11-26&h=7&rain=2").json()
    assert s["available"] and len(s["curve"]) == 31
    assert "Habitat Formation Bulletin" in client.get("/api/bulletin?h=7").text

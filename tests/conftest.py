import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
# Every test run gets its own empty operations database; demo sign-in is on so
# journeys can act as each role without real credentials.
os.environ["RINDA_VAR"] = tempfile.mkdtemp(prefix="rinda-test-")
os.environ["RINDA_DEMO"] = "1"
os.environ["RINDA_PUBLIC_READ"] = "1"
os.environ["RINDA_ADMIN_PASSWORD"] = "Test-Admin-Password-1"
sys.path.insert(0, str(ROOT / "server"))

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402

W = {"X-RINDA": "1"}


@pytest.fixture(scope="session")
def app():
    with TestClient(main.app) as c:
        yield c


def client_as(role: str | None):
    """A fresh client (own cookie jar) signed in as a demonstration role."""
    c = TestClient(main.app)
    if role:
        r = c.post("/api/v1/auth/demo", json={"role": role}, headers=W)
        assert r.status_code == 200, r.text
    return c


@pytest.fixture(scope="session")
def roles(app):
    return {r: client_as(r) for r in ("viewer", "field_officer", "analyst", "supervisor", "admin")}

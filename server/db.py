"""SQLite persistence with versioned, additive migrations.

Every migration is append-only and runs once; schema_version records which have
run. Nothing here drops or rewrites existing tables.
"""
from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager

from engine import VAR

DB = VAR / "operations.db"

MIGRATIONS: list[tuple[int, str, str]] = [
    (1, "operations core", """
CREATE TABLE IF NOT EXISTS missions (
  id TEXT PRIMARY KEY, created_utc TEXT, issue_date TEXT, horizon INTEGER,
  status TEXT, author TEXT, params TEXT, result TEXT);
CREATE TABLE IF NOT EXISTS flight_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, mission_id TEXT, uploaded_utc TEXT,
  filename TEXT, provenance TEXT, n_rows INTEGER, payload TEXT);
CREATE TABLE IF NOT EXISTS telemetry (
  id INTEGER PRIMARY KEY AUTOINCREMENT, station_id TEXT, ts_utc TEXT,
  received_utc TEXT, seq INTEGER, payload TEXT);
CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts_utc TEXT, actor TEXT, action TEXT, detail TEXT);
"""),
    (2, "users and sessions", """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
  role TEXT NOT NULL, organisation TEXT, pw_hash TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
  demo INTEGER NOT NULL DEFAULT 0, created_utc TEXT, last_login_utc TEXT);
CREATE TABLE IF NOT EXISTS sessions (
  token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL, created_utc TEXT, expires_utc TEXT,
  FOREIGN KEY(user_id) REFERENCES users(id));
"""),
    (3, "field surveillance", """
CREATE TABLE IF NOT EXISTS tasks (
  id INTEGER PRIMARY KEY AUTOINCREMENT, site_id TEXT NOT NULL, issue_date TEXT, horizon INTEGER,
  reason TEXT, priority TEXT, status TEXT NOT NULL, due_date TEXT, assignee_id INTEGER,
  created_by INTEGER, created_utc TEXT, updated_utc TEXT, alert_id TEXT, demo INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS observations (
  id INTEGER PRIMARY KEY AUTOINCREMENT, client_uuid TEXT UNIQUE, task_id INTEGER, site_id TEXT,
  lat REAL, lon REAL, gps_accuracy_m REAL, observed_at TEXT NOT NULL, observer_id INTEGER,
  water_present TEXT, habitat_type TEXT, larvae_present TEXT, dips INTEGER, larvae_count INTEGER,
  anopheles_present TEXT, species TEXT, stages TEXT, pupae_present TEXT, pupae_count INTEGER,
  water_depth_cm REAL, vegetation_cover_pct REAL, notes TEXT, quality_flags TEXT,
  status TEXT NOT NULL, reviewer_id INTEGER, review_comment TEXT, reviewed_utc TEXT,
  created_utc TEXT, demo INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS interventions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, site_id TEXT NOT NULL, observation_id INTEGER NOT NULL,
  method TEXT NOT NULL, product TEXT, planned_dose TEXT, protocol_ref TEXT NOT NULL,
  status TEXT NOT NULL, authorized_by INTEGER NOT NULL, authorized_utc TEXT,
  performed_by INTEGER, performed_at TEXT, quantity_used TEXT, performance_notes TEXT,
  followup_observation_id INTEGER, outcome TEXT, closed_utc TEXT, demo INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS alert_states (
  alert_id TEXT PRIMARY KEY, state TEXT NOT NULL, actor_id INTEGER, ts_utc TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS feedback (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts_utc TEXT, user_id INTEGER, page TEXT, message TEXT);
"""),
    (4, "mission approval trail", """
ALTER TABLE missions ADD COLUMN approved_by INTEGER;
ALTER TABLE missions ADD COLUMN approved_utc TEXT;
ALTER TABLE missions ADD COLUMN demo INTEGER NOT NULL DEFAULT 0;
"""),
]


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _migrate(c: sqlite3.Connection) -> None:
    c.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY, name TEXT, applied_utc TEXT)")
    done = {r[0] for r in c.execute("SELECT version FROM schema_version")}
    for v, name, sql in MIGRATIONS:
        if v in done:
            continue
        for stmt in [s.strip() for s in sql.split(";") if s.strip()]:
            try:
                c.execute(stmt)
            except sqlite3.OperationalError as e:  # column already present from an earlier hand migration
                if "duplicate column" not in str(e):
                    raise
        c.execute("INSERT INTO schema_version VALUES (?,?,?)", (v, name, now()))
    c.commit()


_ready = False


def conn() -> sqlite3.Connection:
    global _ready
    c = sqlite3.connect(DB, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    if not _ready:
        c.execute("PRAGMA journal_mode = WAL")
        _migrate(c)
        _ready = True
    return c


@contextmanager
def tx():
    c = conn()
    try:
        yield c
        c.commit()
    finally:
        c.close()


def schema_version() -> int:
    with tx() as c:
        r = c.execute("SELECT MAX(version) FROM schema_version").fetchone()
    return int(r[0] or 0)


def backup(dest) -> None:
    """Online, consistent copy using SQLite's backup API."""
    src = conn()
    out = sqlite3.connect(dest)
    try:
        src.backup(out)
    finally:
        out.close()
        src.close()
